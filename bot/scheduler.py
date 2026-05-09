import contextlib
import logging
from datetime import UTC, datetime

from aiogram import Bot
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from sqlalchemy import select

from bot.db.models import Assignment, ReminderType
from bot.db.session import SessionLocal
from bot.services.reminders import (
    RECOVERY_MIN_LEAD,
    offsets_for,
    send_reminder_batch,
)

logger = logging.getLogger(__name__)

_scheduler: AsyncIOScheduler | None = None
_bot: Bot | None = None


def _job_id(assignment_id: int, reminder_type: ReminderType) -> str:
    return f"rem:{assignment_id}:{reminder_type.value}"


def get_scheduler() -> AsyncIOScheduler:
    if _scheduler is None:
        raise RuntimeError("Scheduler не инициализирован")
    return _scheduler


async def _fire_reminder(assignment_id: int, reminder_type_value: str) -> None:
    if _bot is None:
        return
    reminder_type = ReminderType(reminder_type_value)
    async with SessionLocal() as session, session.begin():
        await send_reminder_batch(
            session,
            _bot,
            assignment_id=assignment_id,
            reminder_type=reminder_type,
        )


def schedule_assignment_reminders(assignment: Assignment, *, now_utc: datetime | None = None) -> None:
    """Планирует все будущие напоминания задания. Прошедшие пропускает."""
    if _scheduler is None:
        return
    now = now_utc or datetime.now(tz=UTC)
    for rtype, offset in offsets_for(assignment.type):
        run_at = assignment.deadline - offset
        if run_at <= now:
            continue
        _scheduler.add_job(
            _fire_reminder,
            trigger="date",
            run_date=run_at,
            args=[assignment.id, rtype.value],
            id=_job_id(assignment.id, rtype),
            replace_existing=True,
            misfire_grace_time=3600,
        )


def cancel_assignment_reminders(assignment_id: int) -> None:
    if _scheduler is None:
        return
    for rtype in OFFSETS_BY_TYPE_KEYS:
        with contextlib.suppress(Exception):
            _scheduler.remove_job(_job_id(assignment_id, rtype))


# Все ReminderType, которые могут использоваться для personal reminders.
OFFSETS_BY_TYPE_KEYS: tuple[ReminderType, ...] = (
    ReminderType.advance_7d,
    ReminderType.advance_3d,
    ReminderType.advance_1d,
)


async def bootstrap_from_db() -> None:
    """При старте: для каждого активного задания планирует будущие напоминания
    и обрабатывает recovery: уже наступившие, но ещё актуальные — отправляет сейчас.
    """
    if _scheduler is None or _bot is None:
        return
    now = datetime.now(tz=UTC)

    async with SessionLocal() as session:
        assignments = list(
            (
                await session.execute(
                    select(Assignment).where(
                        Assignment.is_active.is_(True),
                        Assignment.deadline > now,
                    )
                )
            ).scalars()
        )

    for a in assignments:
        for rtype, offset in offsets_for(a.type):
            run_at = a.deadline - offset
            if run_at > now:
                _scheduler.add_job(
                    _fire_reminder,
                    trigger="date",
                    run_date=run_at,
                    args=[a.id, rtype.value],
                    id=_job_id(a.id, rtype),
                    replace_existing=True,
                    misfire_grace_time=3600,
                )
                continue
            # Recovery: время напоминания прошло. Шлём сейчас, если до дедлайна ≥ 30 минут.
            if a.deadline - now < RECOVERY_MIN_LEAD:
                continue
            async with SessionLocal() as session, session.begin():
                await send_reminder_batch(
                    session,
                    _bot,
                    assignment_id=a.id,
                    reminder_type=rtype,
                    now_utc=now,
                )


async def start_scheduler(bot: Bot) -> AsyncIOScheduler:
    global _scheduler, _bot
    _bot = bot
    _scheduler = AsyncIOScheduler(timezone=UTC)
    _scheduler.start()
    await bootstrap_from_db()
    return _scheduler


async def shutdown_scheduler() -> None:
    global _scheduler
    if _scheduler is not None:
        _scheduler.shutdown(wait=False)
        _scheduler = None

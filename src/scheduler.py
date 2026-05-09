import contextlib
import logging
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from aiogram import Bot
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from sqlalchemy import select

from src.db.models import Assignment, Group, ReminderType
from src.db.session import SessionLocal
from src.services.digest import build_digest_messages, list_groups, list_recipients
from src.services.reminders import (
    RECOVERY_MIN_LEAD,
    offsets_for,
    send_reminder_batch,
)
from src.utils import ensure_utc

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
    await send_reminder_batch(
        SessionLocal,
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
        run_at = ensure_utc(assignment.deadline) - offset
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
            run_at = ensure_utc(a.deadline) - offset
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
            if ensure_utc(a.deadline) - now < RECOVERY_MIN_LEAD:
                continue
            await send_reminder_batch(
                SessionLocal,
                _bot,
                assignment_id=a.id,
                reminder_type=rtype,
                now_utc=now,
            )


def _last_digest_slot_utc(group: Group, now_utc: datetime) -> datetime:
    """Последнее наступление (digest_day, digest_hour) в TZ группы, не позднее now_utc.
    Возвращает aware UTC-таймстамп начала слота.
    """
    tz = ZoneInfo(group.timezone)
    local = now_utc.astimezone(tz)
    today_slot = local.replace(
        hour=group.digest_hour, minute=0, second=0, microsecond=0
    )
    days_since = (local.weekday() - group.digest_day) % 7
    slot_local = today_slot - timedelta(days=days_since)
    if slot_local > local:
        slot_local -= timedelta(days=7)
    return slot_local.astimezone(UTC)


async def _digest_tick() -> None:
    if _bot is None:
        return
    now_utc = datetime.now(tz=UTC)
    async with SessionLocal() as session:
        groups = await list_groups(session)

    for group in groups:
        try:
            slot_start = _last_digest_slot_utc(group, now_utc)
        except Exception:
            logger.exception("Невалидная TZ у группы %s", group.id)
            continue
        last_sent = group.digest_last_sent_at
        if last_sent is not None and last_sent.tzinfo is None:
            last_sent = last_sent.replace(tzinfo=UTC)
        if last_sent is not None and last_sent >= slot_start:
            continue
        try:
            sent = await _send_digest_for_group(group_id=group.id)
        except Exception:
            logger.exception("Ошибка дайджеста группы %s", group.id)
            continue
        if not sent:
            # Нечего отправлять — всё равно фиксируем слот, чтобы не пытаться повторно.
            async with SessionLocal() as session, session.begin():
                g = await session.get(Group, group.id)
                if g is not None:
                    g.digest_last_sent_at = now_utc
            continue
        async with SessionLocal() as session, session.begin():
            g = await session.get(Group, group.id)
            if g is not None:
                g.digest_last_sent_at = now_utc


async def _send_digest_for_group(*, group_id: int) -> bool:
    if _bot is None:
        return False
    async with SessionLocal() as session:
        group = await session.get(Group, group_id)
        if group is None:
            return False
        messages = await build_digest_messages(session, group)
        chat_id = group.chat_id
        recipients = [] if chat_id else await list_recipients(session, group_id)

    if not messages:
        return False
    if chat_id is not None:
        for text in messages:
            with contextlib.suppress(Exception):
                await _bot.send_message(chat_id, text)
        return True
    for user in recipients:
        for text in messages:
            with contextlib.suppress(Exception):
                await _bot.send_message(user.tg_user_id, text)
    return True


async def start_scheduler(bot: Bot) -> AsyncIOScheduler:
    global _scheduler, _bot
    _bot = bot
    _scheduler = AsyncIOScheduler(timezone=UTC)
    _scheduler.add_job(
        _digest_tick,
        trigger="cron",
        minute=0,
        id="digest_tick",
        replace_existing=True,
    )
    _scheduler.start()
    await bootstrap_from_db()
    # Догон пропущенного дайджеста при старте — не ждём ближайшего часа.
    try:
        await _digest_tick()
    except Exception:
        logger.exception("Ошибка стартового _digest_tick")
    return _scheduler


async def shutdown_scheduler() -> None:
    global _scheduler
    if _scheduler is not None:
        _scheduler.shutdown(wait=False)
        _scheduler = None

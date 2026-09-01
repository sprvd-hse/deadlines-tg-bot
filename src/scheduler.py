import contextlib
import logging
from datetime import UTC, datetime

from aiogram import Bot
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from src.db.repositories.groups import GroupRepository
from src.db.repositories.items import ItemRepository
from src.db.session import SessionLocal
from src.domain.humanize import ensure_utc
from src.domain.models import Group, Item
from src.services.digest import DigestService
from src.services.reminders import RECOVERY_MIN_LEAD, ReminderService, schedule_for

logger = logging.getLogger(__name__)

_scheduler: AsyncIOScheduler | None = None
_reminders: ReminderService | None = None
_digest: DigestService | None = None


def _job_id(item_id: int, offset_minutes: int) -> str:
    return f"rem:{item_id}:{offset_minutes}"


def get_scheduler() -> AsyncIOScheduler:
    """Возвращает работающий планировщик."""
    if _scheduler is None:
        raise RuntimeError("Scheduler не инициализирован")
    return _scheduler


async def _fire_reminder(item_id: int, offset_minutes: int) -> None:
    if _reminders is None:
        return
    await _reminders.send_batch(item_id=item_id, offset_minutes=offset_minutes)


def schedule_item(item: Item, group: Group, *, now: datetime | None = None) -> None:
    """Перепланирует все будущие напоминания записи."""
    if _scheduler is None:
        return
    cancel_item(item.id)
    moment = now or datetime.now(tz=UTC)
    for reminder in schedule_for(item, group):
        if reminder.run_at <= moment:
            continue
        _scheduler.add_job(
            _fire_reminder,
            trigger="date",
            run_date=reminder.run_at,
            args=[item.id, reminder.offset_minutes],
            id=_job_id(item.id, reminder.offset_minutes),
            replace_existing=True,
            misfire_grace_time=3600,
        )


def cancel_item(item_id: int) -> None:
    """Снимает все запланированные напоминания записи."""
    if _scheduler is None:
        return
    prefix = f"rem:{item_id}:"
    for job in _scheduler.get_jobs():
        if job.id.startswith(prefix):
            with contextlib.suppress(Exception):
                job.remove()


async def bootstrap_from_db() -> None:
    """Восстанавливает расписание после рестарта и догоняет пропущенные напоминания."""
    if _scheduler is None or _reminders is None:
        return
    now = datetime.now(tz=UTC)

    async with SessionLocal() as session:
        items = await ItemRepository(session).list_scheduled(after=now)
        groups_repo = GroupRepository(session)
        groups: dict[int, Group] = {}
        for item in items:
            if item.group_id not in groups:
                group = await groups_repo.get(item.group_id)
                if group is not None:
                    groups[item.group_id] = group

    for item in items:
        group = groups.get(item.group_id)
        if group is None or item.due_at is None:
            continue
        for reminder in schedule_for(item, group):
            if reminder.run_at > now:
                _scheduler.add_job(
                    _fire_reminder,
                    trigger="date",
                    run_date=reminder.run_at,
                    args=[item.id, reminder.offset_minutes],
                    id=_job_id(item.id, reminder.offset_minutes),
                    replace_existing=True,
                    misfire_grace_time=3600,
                )
                continue
            if ensure_utc(item.due_at) - now < RECOVERY_MIN_LEAD:
                continue
            await _reminders.send_batch(
                item_id=item.id, offset_minutes=reminder.offset_minutes, now=now
            )


async def _digest_tick() -> None:
    if _digest is None:
        return
    await _digest.tick()


async def start_scheduler(bot: Bot) -> AsyncIOScheduler:
    """Поднимает планировщик, восстанавливает задания и догоняет дайджест."""
    global _scheduler, _reminders, _digest
    _reminders = ReminderService(SessionLocal, bot)
    _digest = DigestService(SessionLocal, bot)
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
    try:
        await _digest_tick()
    except Exception:
        logger.exception("Ошибка стартового дайджеста")
    return _scheduler


async def shutdown_scheduler() -> None:
    """Останавливает планировщик."""
    global _scheduler
    if _scheduler is not None:
        _scheduler.shutdown(wait=False)
        _scheduler = None

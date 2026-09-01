import logging
from datetime import UTC, datetime, timedelta

from aiogram.types import LinkPreviewOptions

from src.db.repositories.groups import GroupRepository
from src.db.repositories.items import ItemRepository
from src.db.repositories.reminders import ReminderLogRepository
from src.db.repositories.subjects import SubjectRepository
from src.domain.humanize import ensure_utc
from src.domain.models import Group, Item
from src.domain.reminders import ScheduledReminder, plan_reminders, resolve_offsets
from src.views.reminders import render_reminder

logger = logging.getLogger(__name__)

# Напоминание, чьё время уже прошло, догоняем при старте, только если до дедлайна
# осталось хотя бы столько.
RECOVERY_MIN_LEAD = timedelta(minutes=30)


def schedule_for(item: Item, group: Group) -> list[ScheduledReminder]:
    """Считает расписание напоминаний записи по настройкам её группы."""
    offsets = resolve_offsets(
        kind=item.kind,
        item_offsets=item.reminder_offsets,
        muted=item.reminders_muted,
        settings=group.reminders,
    )
    return plan_reminders(
        due_at=item.due_at,
        offsets=offsets,
        tz=group.tz,
        reminder_hour=group.reminders.reminder_hour,
    )


class ReminderService:
    """Рассылка личных напоминаний с защитой от повторов."""

    def __init__(self, session_factory, bot) -> None:
        self._session_factory = session_factory
        self._bot = bot

    async def send_batch(
        self, *, item_id: int, offset_minutes: int, now: datetime | None = None
    ) -> int:
        """Рассылает одно напоминание всем подходящим участникам группы.

        Каждый получатель обрабатывается отдельной транзакцией: журнал коммитится
        сразу после успешной отправки, поэтому падение в середине рассылки не
        приводит к дублям при повторе.
        """
        moment = now or datetime.now(tz=UTC)
        prepared = await self._prepare(item_id=item_id, offset_minutes=offset_minutes, now=moment)
        if prepared is None:
            return 0
        text, recipients = prepared

        sent = 0
        for user_id, tg_user_id in recipients:
            try:
                await self._bot.send_message(
                    tg_user_id,
                    text,
                    parse_mode="HTML",
                    link_preview_options=LinkPreviewOptions(is_disabled=True),
                )
            except Exception:
                logger.exception(
                    "Не удалось отправить напоминание user=%s item=%s", user_id, item_id
                )
                continue
            try:
                async with self._session_factory() as session, session.begin():
                    await ReminderLogRepository(session).mark_sent(
                        item_id=item_id, user_id=user_id, offset_minutes=offset_minutes
                    )
            except Exception:
                logger.exception("Не удалось записать журнал user=%s item=%s", user_id, item_id)
                continue
            sent += 1
        return sent

    async def _prepare(
        self, *, item_id: int, offset_minutes: int, now: datetime
    ) -> tuple[str, list[tuple[int, int]]] | None:
        async with self._session_factory() as session:
            item = await ItemRepository(session).get(item_id)
            if item is None or not item.is_active or item.due_at is None:
                return None
            if ensure_utc(item.due_at) <= now:
                return None
            groups = GroupRepository(session)
            group = await groups.get(item.group_id)
            if group is None:
                return None
            offsets = resolve_offsets(
                kind=item.kind,
                item_offsets=item.reminder_offsets,
                muted=item.reminders_muted,
                settings=group.reminders,
            )
            if offset_minutes not in offsets:
                return None
            subject = await SubjectRepository(session).get(item.subject_id)
            already = await ReminderLogRepository(session).sent_user_ids(
                item_id=item_id, offset_minutes=offset_minutes
            )
            recipients = [
                (m.user.id, m.user.tg_user_id)
                for m in await groups.list_members(group.id)
                if m.personal_reminders_enabled and m.user.id not in already
            ]
            text = render_reminder(
                item=item,
                subject_name=subject.name if subject else "?",
                group=group,
                offset_minutes=offset_minutes,
                now=now,
            )
        return text, recipients

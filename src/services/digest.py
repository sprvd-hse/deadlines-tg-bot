import contextlib
import logging
from datetime import UTC, datetime, timedelta

from aiogram.types import LinkPreviewOptions

from src.db.repositories.groups import GroupRepository
from src.db.repositories.subjects import SubjectRepository
from src.domain.models import Group
from src.services.items import ItemManager
from src.views.common import split_messages
from src.views.items import render_digest

logger = logging.getLogger(__name__)

DEFAULT_HORIZON_DAYS = 14


def last_digest_slot(group: Group, now: datetime) -> datetime:
    """Последнее наступление (digest_day, digest_hour) в поясе группы не позже now, в UTC."""
    local = now.astimezone(group.tz)
    today_slot = local.replace(hour=group.digest_hour, minute=0, second=0, microsecond=0)
    days_since = (local.weekday() - group.digest_day) % 7
    slot_local = today_slot - timedelta(days=days_since)
    if slot_local > local:
        slot_local -= timedelta(days=7)
    return slot_local.astimezone(UTC)


def is_digest_due(group: Group, now: datetime) -> bool:
    """Наступил ли новый слот дайджеста, ещё не отработанный для группы."""
    slot_start = last_digest_slot(group, now)
    last_sent = group.digest_last_sent_at
    if last_sent is None:
        return True
    if last_sent.tzinfo is None:
        last_sent = last_sent.replace(tzinfo=UTC)
    return last_sent < slot_start


class DigestService:
    """Сборка и отправка дайджеста ближайших дедлайнов."""

    def __init__(self, session_factory, bot) -> None:
        self._session_factory = session_factory
        self._bot = bot

    async def build(
        self,
        session,
        group: Group,
        *,
        now: datetime,
        horizon_days: int | None = DEFAULT_HORIZON_DAYS,
        header: str | None = None,
    ) -> list[str]:
        """Собирает готовые к отправке сообщения дайджеста."""
        items = ItemManager(session)
        until = now + timedelta(days=horizon_days) if horizon_days else None
        upcoming = await items.upcoming(group.id, now=now, until=until)
        subject_names = {
            s.id: s.name for s in await SubjectRepository(session).list_for_group(group.id)
        }
        title = header or _default_header(horizon_days)
        blocks = render_digest(
            items=upcoming,
            subject_names=subject_names,
            group=group,
            now=now,
            header=title,
        )
        return split_messages(blocks)

    async def send_for_group(self, group_id: int, *, now: datetime | None = None) -> bool:
        """Отправляет дайджест в привязанный чат или всем участникам в личку."""
        moment = now or datetime.now(tz=UTC)
        async with self._session_factory() as session:
            groups = GroupRepository(session)
            group = await groups.get(group_id)
            if group is None:
                return False
            messages = await self.build(session, group, now=moment)
            chat_id = group.chat_id
            previous_pin = group.digest_pinned_message_id
            recipients = (
                []
                if chat_id
                else [
                    m.user.tg_user_id
                    for m in await groups.list_members(group_id)
                    if m.personal_reminders_enabled
                ]
            )
        if not messages:
            return False
        if chat_id is not None:
            await self._send_to_chat(group_id, chat_id, messages, previous_pin=previous_pin)
            return True
        for target in recipients:
            await self._send_all(target, messages)
        return True

    async def _send_to_chat(
        self, group_id: int, chat_id: int, messages: list[str], *, previous_pin: int | None
    ) -> None:
        """Шлёт дайджест в чат и закрепляет его, откручивая предыдущий закреп."""
        sent = await self._send_all(chat_id, messages)
        if not sent:
            return
        if previous_pin is not None:
            with contextlib.suppress(Exception):
                await self._bot.unpin_chat_message(chat_id=chat_id, message_id=previous_pin)
        pinned_id: int | None = None
        try:
            await self._bot.pin_chat_message(
                chat_id=chat_id, message_id=sent[0], disable_notification=True
            )
            pinned_id = sent[0]
        except Exception:
            logger.exception("Не удалось закрепить дайджест в чате %s", chat_id)
        async with self._session_factory() as session, session.begin():
            await GroupRepository(session).update_settings(
                group_id, digest_pinned_message_id=pinned_id
            )

    async def _send_all(self, target: int, messages: list[str]) -> list[int]:
        """Отправляет все части дайджеста, возвращает id доставленных сообщений."""
        sent: list[int] = []
        for text in messages:
            try:
                message = await self._bot.send_message(
                    target,
                    text,
                    parse_mode="HTML",
                    link_preview_options=LinkPreviewOptions(is_disabled=True),
                )
            except Exception:
                logger.exception("Не удалось отправить дайджест в чат %s", target)
                continue
            sent.append(message.message_id)
        return sent

    async def tick(self, *, now: datetime | None = None) -> None:
        """Проверяет все группы и рассылает дайджест тем, у кого наступил слот."""
        moment = now or datetime.now(tz=UTC)
        async with self._session_factory() as session:
            groups = await GroupRepository(session).list_all()

        for group in groups:
            try:
                if not is_digest_due(group, moment):
                    continue
            except Exception:
                logger.exception("Не удалось вычислить слот дайджеста группы %s", group.id)
                continue
            try:
                await self.send_for_group(group.id, now=moment)
            except Exception:
                logger.exception("Ошибка дайджеста группы %s", group.id)
                continue
            # Слот фиксируем даже когда отправлять было нечего, чтобы не пытаться повторно.
            async with self._session_factory() as session, session.begin():
                await GroupRepository(session).update_settings(group.id, digest_last_sent_at=moment)


def _default_header(horizon_days: int | None) -> str:
    if horizon_days is None:
        return "📅 Все задания группы"
    return f"📅 Дедлайны на ближайшие {horizon_days} дней"

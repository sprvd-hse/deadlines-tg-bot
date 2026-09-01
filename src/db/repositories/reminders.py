from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.db import models as orm


class ReminderLogRepository:
    """Журнал отправленных напоминаний — ключ идемпотентности рассылки."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def sent_user_ids(self, *, item_id: int, offset_minutes: int) -> set[int]:
        """Кому уже отправлено это напоминание."""
        rows = await self._session.execute(
            select(orm.ReminderLog.user_id).where(
                orm.ReminderLog.item_id == item_id,
                orm.ReminderLog.offset_minutes == offset_minutes,
                orm.ReminderLog.user_id.is_not(None),
            )
        )
        return {uid for uid in rows.scalars() if uid is not None}

    async def mark_sent(self, *, item_id: int, user_id: int, offset_minutes: int) -> None:
        """Фиксирует факт отправки напоминания."""
        self._session.add(
            orm.ReminderLog(item_id=item_id, user_id=user_id, offset_minutes=offset_minutes)
        )

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.db import models as orm


class CompletionRepository:
    """Личные отметки «выполнено» по записям."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def completed_ids(self, *, user_id: int, item_ids: list[int]) -> set[int]:
        """Какие из перечисленных записей пользователь уже отметил."""
        if not item_ids:
            return set()
        rows = await self._session.execute(
            select(orm.ItemCompletion.item_id).where(
                orm.ItemCompletion.user_id == user_id,
                orm.ItemCompletion.item_id.in_(item_ids),
            )
        )
        return set(rows.scalars())

    async def is_completed(self, *, user_id: int, item_id: int) -> bool:
        """Отмечена ли запись этим пользователем."""
        found = (
            await self._session.execute(
                select(orm.ItemCompletion.id).where(
                    orm.ItemCompletion.user_id == user_id,
                    orm.ItemCompletion.item_id == item_id,
                )
            )
        ).scalar_one_or_none()
        return found is not None

    async def users_who_completed(self, item_id: int) -> set[int]:
        """Кто из участников уже отметил запись выполненной."""
        rows = await self._session.execute(
            select(orm.ItemCompletion.user_id).where(orm.ItemCompletion.item_id == item_id)
        )
        return set(rows.scalars())

    async def mark(self, *, user_id: int, item_id: int) -> None:
        """Ставит отметку."""
        self._session.add(orm.ItemCompletion(user_id=user_id, item_id=item_id))
        await self._session.flush()

    async def unmark(self, *, user_id: int, item_id: int) -> None:
        """Снимает отметку."""
        await self._session.execute(
            delete(orm.ItemCompletion).where(
                orm.ItemCompletion.user_id == user_id,
                orm.ItemCompletion.item_id == item_id,
            )
        )

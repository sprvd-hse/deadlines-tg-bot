from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from src.db import models as orm
from src.domain.models import User


def to_domain(row: orm.User) -> User:
    """Преобразует ORM-пользователя в доменную модель."""
    return User.model_validate(row)


class UserRepository:
    """Доступ к пользователям бота."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_or_create(
        self, *, tg_user_id: int, tg_username: str | None, full_name: str
    ) -> User:
        """Возвращает пользователя по tg_user_id, создавая его при необходимости."""
        row = (
            await self._session.execute(select(orm.User).where(orm.User.tg_user_id == tg_user_id))
        ).scalar_one_or_none()
        if row is None:
            row = orm.User(tg_user_id=tg_user_id, tg_username=tg_username, full_name=full_name)
            self._session.add(row)
            await self._session.flush()
        else:
            row.tg_username = tg_username
            row.full_name = full_name
        return to_domain(row)

    async def get(self, user_id: int) -> User | None:
        """Возвращает пользователя по внутреннему id."""
        row = await self._session.get(orm.User, user_id)
        return to_domain(row) if row else None

    async def set_active_group(self, *, user_id: int, group_id: int | None) -> None:
        """Устанавливает активную группу пользователя."""
        await self._session.execute(
            update(orm.User).where(orm.User.id == user_id).values(active_group_id=group_id)
        )

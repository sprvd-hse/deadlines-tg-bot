from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models import User


async def get_or_create_user(
    session: AsyncSession,
    tg_user_id: int,
    tg_username: str | None,
    full_name: str,
) -> User:
    """Достаёт пользователя по tg_user_id или создаёт нового; обновляет username/full_name."""
    user = (
        await session.execute(select(User).where(User.tg_user_id == tg_user_id))
    ).scalar_one_or_none()
    if user is None:
        user = User(tg_user_id=tg_user_id, tg_username=tg_username, full_name=full_name)
        session.add(user)
        await session.flush()
        return user

    if user.tg_username != tg_username:
        user.tg_username = tg_username
    if user.full_name != full_name:
        user.full_name = full_name
    return user

import time
from typing import Literal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bot.db.models import Group, GroupMember, MemberRole, User
from bot.utils import generate_invite_code

JoinStatus = Literal["ok", "not_found", "already_member"]

_INVITE_GENERATION_RETRIES = 5

# Простой in-memory rate-limit для /join: 5 попыток в минуту на tg_user_id.
_JOIN_RATE_WINDOW = 60.0
_JOIN_RATE_MAX = 5
_join_attempts: dict[int, list[float]] = {}


def check_join_rate_limit(tg_user_id: int) -> bool:
    """True — запрос разрешён, False — лимит превышен."""
    now = time.monotonic()
    window = _join_attempts.setdefault(tg_user_id, [])
    window[:] = [t for t in window if now - t < _JOIN_RATE_WINDOW]
    if len(window) >= _JOIN_RATE_MAX:
        return False
    window.append(now)
    return True


async def create_group(
    session: AsyncSession,
    creator: User,
    name: str,
    timezone: str,
) -> Group:
    """Создаёт группу, добавляет creator-членство, ставит группу активной у создателя."""
    code = await _generate_unique_invite_code(session)

    group = Group(
        name=name,
        invite_code=code,
        timezone=timezone,
        created_by_user_id=creator.id,
    )
    session.add(group)
    await session.flush()

    session.add(GroupMember(group_id=group.id, user_id=creator.id, role=MemberRole.creator))
    creator.active_group_id = group.id
    return group


async def join_group(
    session: AsyncSession,
    user: User,
    invite_code: str,
) -> tuple[Group | None, JoinStatus]:
    """Присоединяет пользователя к группе по коду. Делает группу активной."""
    group = (
        await session.execute(select(Group).where(Group.invite_code == invite_code))
    ).scalar_one_or_none()
    if group is None:
        return None, "not_found"

    existing = (
        await session.execute(
            select(GroupMember).where(
                GroupMember.group_id == group.id,
                GroupMember.user_id == user.id,
            )
        )
    ).scalar_one_or_none()

    if existing is not None:
        user.active_group_id = group.id
        return group, "already_member"

    session.add(GroupMember(group_id=group.id, user_id=user.id, role=MemberRole.member))
    user.active_group_id = group.id
    return group, "ok"


async def list_user_groups(
    session: AsyncSession,
    user: User,
) -> list[tuple[Group, MemberRole]]:
    """Возвращает группы пользователя с его ролью, отсортированные по названию."""
    rows = (
        await session.execute(
            select(Group, GroupMember.role)
            .join(GroupMember, GroupMember.group_id == Group.id)
            .where(GroupMember.user_id == user.id)
            .order_by(Group.name)
        )
    ).all()
    return [(g, role) for g, role in rows]


async def switch_active_group(
    session: AsyncSession,
    user: User,
    group_id: int,
) -> Group | None:
    """Делает группу активной, если пользователь в ней состоит. Иначе None."""
    group = (
        await session.execute(
            select(Group)
            .join(GroupMember, GroupMember.group_id == Group.id)
            .where(Group.id == group_id, GroupMember.user_id == user.id)
        )
    ).scalar_one_or_none()
    if group is None:
        return None
    user.active_group_id = group.id
    return group


async def _generate_unique_invite_code(session: AsyncSession) -> str:
    for _ in range(_INVITE_GENERATION_RETRIES):
        code = generate_invite_code()
        existing = (
            await session.execute(select(Group.id).where(Group.invite_code == code))
        ).scalar_one_or_none()
        if existing is None:
            return code
    raise RuntimeError("Не удалось сгенерировать уникальный invite-код")

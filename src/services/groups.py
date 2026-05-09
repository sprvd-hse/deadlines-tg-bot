import time
from typing import Literal

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models import (
    Assignment,
    AssignmentCompletion,
    Group,
    GroupMember,
    MemberRole,
    ReminderLog,
    Subject,
    User,
)
from src.utils import generate_invite_code

JoinStatus = Literal["ok", "not_found", "already_member"]
TransferStatus = Literal["ok", "not_member", "same_user"]
LeaveStatus = Literal["left", "group_deleted", "need_transfer", "not_member"]

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
    # Лёгкая чистка пустых окон у других пользователей, чтобы dict не рос.
    if len(_join_attempts) > 1024:
        for uid, w in list(_join_attempts.items()):
            if uid != tg_user_id and (not w or now - w[-1] >= _JOIN_RATE_WINDOW):
                _join_attempts.pop(uid, None)
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


async def get_active_group(session: AsyncSession, user: User) -> Group | None:
    if user.active_group_id is None:
        return None
    return (
        await session.execute(select(Group).where(Group.id == user.active_group_id))
    ).scalar_one_or_none()


async def is_creator(session: AsyncSession, user_id: int, group_id: int) -> bool:
    role = (
        await session.execute(
            select(GroupMember.role).where(
                GroupMember.user_id == user_id, GroupMember.group_id == group_id
            )
        )
    ).scalar_one_or_none()
    return role == MemberRole.creator


async def list_members(
    session: AsyncSession, group_id: int
) -> list[tuple[User, MemberRole]]:
    """Список (user, role) для группы, отсортированный creator-сначала, потом по имени."""
    rows = (
        await session.execute(
            select(User, GroupMember.role)
            .join(GroupMember, GroupMember.user_id == User.id)
            .where(GroupMember.group_id == group_id)
            .order_by(GroupMember.role, User.full_name)
        )
    ).all()
    return [(u, role) for u, role in rows]


async def transfer_ownership(
    session: AsyncSession, *, group_id: int, current_creator_id: int, new_creator_user_id: int
) -> TransferStatus:
    if current_creator_id == new_creator_user_id:
        return "same_user"
    new_member = (
        await session.execute(
            select(GroupMember).where(
                GroupMember.group_id == group_id,
                GroupMember.user_id == new_creator_user_id,
            )
        )
    ).scalar_one_or_none()
    if new_member is None:
        return "not_member"
    current_member = (
        await session.execute(
            select(GroupMember).where(
                GroupMember.group_id == group_id,
                GroupMember.user_id == current_creator_id,
            )
        )
    ).scalar_one_or_none()
    if current_member is not None:
        current_member.role = MemberRole.member
    new_member.role = MemberRole.creator
    return "ok"


async def regenerate_invite_code(session: AsyncSession, group: Group) -> str:
    code = await _generate_unique_invite_code(session)
    group.invite_code = code
    return code


async def get_member(
    session: AsyncSession, *, group_id: int, user_id: int
) -> GroupMember | None:
    return (
        await session.execute(
            select(GroupMember).where(
                GroupMember.group_id == group_id, GroupMember.user_id == user_id
            )
        )
    ).scalar_one_or_none()


async def set_personal_reminders(
    session: AsyncSession, *, group_id: int, user_id: int, enabled: bool
) -> bool:
    """Устанавливает personal_reminders_enabled, возвращает новое значение."""
    member = await get_member(session, group_id=group_id, user_id=user_id)
    if member is None:
        return enabled
    member.personal_reminders_enabled = enabled
    return enabled


async def leave_group(
    session: AsyncSession, *, group_id: int, user_id: int
) -> LeaveStatus:
    member = await get_member(session, group_id=group_id, user_id=user_id)
    if member is None:
        return "not_member"
    members_count = (
        await session.execute(
            select(GroupMember).where(GroupMember.group_id == group_id)
        )
    ).all()
    if member.role == MemberRole.creator and len(members_count) > 1:
        return "need_transfer"
    if len(members_count) == 1:
        await delete_group(session, group_id)
        return "group_deleted"
    await session.execute(
        delete(GroupMember).where(
            GroupMember.group_id == group_id, GroupMember.user_id == user_id
        )
    )
    return "left"


async def delete_group(session: AsyncSession, group_id: int) -> None:
    """Удаляет группу. Порядок: reminder_log → completions → assignments → subjects → members → group.

    Явный порядок нужен потому, что Assignment.subject_id имеет ondelete=RESTRICT
    и каскад с groups может попытаться удалить subject раньше assignment.
    Также сбрасываем active_group_id у пользователей, чтобы FK SET NULL отработал
    предсказуемо при последующем DELETE Group.
    """
    assignment_ids = list(
        (
            await session.execute(
                select(Assignment.id).where(Assignment.group_id == group_id)
            )
        ).scalars()
    )
    if assignment_ids:
        await session.execute(
            delete(ReminderLog).where(ReminderLog.assignment_id.in_(assignment_ids))
        )
        await session.execute(
            delete(AssignmentCompletion).where(
                AssignmentCompletion.assignment_id.in_(assignment_ids)
            )
        )
    await session.execute(delete(Assignment).where(Assignment.group_id == group_id))
    await session.execute(delete(Subject).where(Subject.group_id == group_id))
    await session.execute(delete(GroupMember).where(GroupMember.group_id == group_id))
    await session.execute(
        User.__table__.update()
        .where(User.active_group_id == group_id)
        .values(active_group_id=None)
    )
    await session.execute(delete(Group).where(Group.id == group_id))


async def _generate_unique_invite_code(session: AsyncSession) -> str:
    for _ in range(_INVITE_GENERATION_RETRIES):
        code = generate_invite_code()
        existing = (
            await session.execute(select(Group.id).where(Group.invite_code == code))
        ).scalar_one_or_none()
        if existing is None:
            return code
    raise RuntimeError("Не удалось сгенерировать уникальный invite-код")

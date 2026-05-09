from datetime import UTC, datetime, timedelta
from typing import Literal

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models import Assignment, AssignmentCompletion, AssignmentType, ReminderLog

DeleteStatus = Literal["ok", "not_found", "forbidden", "already_deleted"]
CompleteStatus = Literal["ok", "already", "not_found"]
UncompleteStatus = Literal["ok", "not_marked", "not_found"]

PASSED_DAYS_BACK = 14


async def next_short_id(session: AsyncSession, group_id: int) -> int:
    max_id = (
        await session.execute(
            select(func.max(Assignment.short_id)).where(Assignment.group_id == group_id)
        )
    ).scalar()
    return (max_id or 0) + 1


async def create_assignment(
    session: AsyncSession,
    *,
    group_id: int,
    creator_user_id: int,
    subject_id: int,
    assignment_type: AssignmentType,
    title: str,
    description: str | None,
    links: list[str],
    deadline_utc: datetime | None,
) -> Assignment:
    short_id = await next_short_id(session, group_id)
    a = Assignment(
        group_id=group_id,
        short_id=short_id,
        subject_id=subject_id,
        type=assignment_type,
        title=title,
        description=description,
        links=links,
        deadline=deadline_utc,
        created_by_user_id=creator_user_id,
        is_active=True,
    )
    session.add(a)
    await session.flush()
    return a


async def get_by_short_id(session: AsyncSession, group_id: int, short_id: int) -> Assignment | None:
    return (
        await session.execute(
            select(Assignment).where(
                Assignment.group_id == group_id, Assignment.short_id == short_id
            )
        )
    ).scalar_one_or_none()


async def soft_delete(
    session: AsyncSession,
    *,
    group_id: int,
    short_id: int,
    requesting_user_id: int,
    is_creator_of_group: bool,
) -> DeleteStatus:
    a = await get_by_short_id(session, group_id, short_id)
    if a is None:
        return "not_found"
    if not a.is_active:
        return "already_deleted"
    if a.created_by_user_id != requesting_user_id and not is_creator_of_group:
        return "forbidden"
    a.is_active = False
    return "ok"


async def list_active(
    session: AsyncSession,
    group_id: int,
    *,
    subject_id: int | None = None,
    now_utc: datetime | None = None,
) -> list[Assignment]:
    from sqlalchemy import or_

    now = now_utc or datetime.now(tz=UTC)
    stmt = select(Assignment).where(
        Assignment.group_id == group_id,
        Assignment.is_active.is_(True),
        or_(Assignment.deadline.is_(None), Assignment.deadline >= now),
    )
    if subject_id is not None:
        stmt = stmt.where(Assignment.subject_id == subject_id)
    # NULL-дедлайны («до конца модуля») в конец списка — после датированных по возрастанию.
    stmt = stmt.order_by(Assignment.deadline.is_(None), Assignment.deadline)
    return list((await session.execute(stmt)).scalars())


async def list_passed(
    session: AsyncSession,
    group_id: int,
    *,
    subject_id: int | None = None,
    now_utc: datetime | None = None,
    days_back: int = PASSED_DAYS_BACK,
) -> list[Assignment]:
    now = now_utc or datetime.now(tz=UTC)
    cutoff = now - timedelta(days=days_back)
    stmt = select(Assignment).where(
        Assignment.group_id == group_id,
        Assignment.is_active.is_(True),
        Assignment.deadline.is_not(None),
        Assignment.deadline < now,
        Assignment.deadline >= cutoff,
    )
    if subject_id is not None:
        stmt = stmt.where(Assignment.subject_id == subject_id)
    stmt = stmt.order_by(Assignment.deadline.desc())
    return list((await session.execute(stmt)).scalars())


async def completed_ids_for_user(
    session: AsyncSession, user_id: int, assignment_ids: list[int]
) -> set[int]:
    if not assignment_ids:
        return set()
    rows = (
        await session.execute(
            select(AssignmentCompletion.assignment_id).where(
                AssignmentCompletion.user_id == user_id,
                AssignmentCompletion.assignment_id.in_(assignment_ids),
            )
        )
    ).scalars()
    return set(rows)


async def mark_completed(
    session: AsyncSession, *, group_id: int, short_id: int, user_id: int
) -> CompleteStatus:
    a = await get_by_short_id(session, group_id, short_id)
    if a is None or not a.is_active:
        return "not_found"
    existing = (
        await session.execute(
            select(AssignmentCompletion.id).where(
                AssignmentCompletion.assignment_id == a.id,
                AssignmentCompletion.user_id == user_id,
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        return "already"
    session.add(AssignmentCompletion(assignment_id=a.id, user_id=user_id))
    return "ok"


async def unmark_completed(
    session: AsyncSession, *, group_id: int, short_id: int, user_id: int
) -> UncompleteStatus:
    a = await get_by_short_id(session, group_id, short_id)
    if a is None or not a.is_active:
        return "not_found"
    result = await session.execute(
        delete(AssignmentCompletion).where(
            AssignmentCompletion.assignment_id == a.id,
            AssignmentCompletion.user_id == user_id,
        )
    )
    return "ok" if result.rowcount else "not_marked"


async def update_assignment(
    session: AsyncSession,
    assignment: Assignment,
    *,
    assignment_type: AssignmentType,
    subject_id: int,
    title: str,
    description: str | None,
    links: list[str],
    deadline_utc: datetime | None,
) -> bool:
    """Обновляет поля задания. Возвращает True, если deadline изменился."""
    deadline_changed = assignment.deadline != deadline_utc
    assignment.type = assignment_type
    assignment.subject_id = subject_id
    assignment.title = title
    assignment.description = description
    assignment.links = links
    assignment.deadline = deadline_utc
    return deadline_changed


async def clear_reminder_log(session: AsyncSession, assignment_id: int) -> None:
    await session.execute(
        delete(ReminderLog).where(ReminderLog.assignment_id == assignment_id)
    )

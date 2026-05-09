from datetime import UTC, datetime, timedelta
from typing import Literal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from bot.db.models import Assignment, AssignmentCompletion, AssignmentType

DeleteStatus = Literal["ok", "not_found", "forbidden", "already_deleted"]

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
    deadline_utc: datetime,
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
    now = now_utc or datetime.now(tz=UTC)
    stmt = select(Assignment).where(
        Assignment.group_id == group_id,
        Assignment.is_active.is_(True),
        Assignment.deadline >= now,
    )
    if subject_id is not None:
        stmt = stmt.where(Assignment.subject_id == subject_id)
    stmt = stmt.order_by(Assignment.deadline)
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

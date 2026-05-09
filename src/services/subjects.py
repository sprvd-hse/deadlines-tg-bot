from typing import Literal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models import Assignment, Subject

DeleteSubjectStatus = Literal["ok", "not_found", "has_assignments"]


async def list_subjects(session: AsyncSession, group_id: int) -> list[Subject]:
    rows = await session.execute(
        select(Subject).where(Subject.group_id == group_id).order_by(Subject.name)
    )
    return list(rows.scalars())


def _ci(name: str):
    return func.lower(name)


async def add_subject(session: AsyncSession, group_id: int, name: str) -> Subject | None:
    """Создаёт предмет; возвращает None, если предмет с таким именем уже есть (без учёта регистра)."""
    name = name.strip()
    existing = (
        await session.execute(
            select(Subject).where(
                Subject.group_id == group_id,
                func.lower(Subject.name) == _ci(name),
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        return None
    subject = Subject(group_id=group_id, name=name)
    session.add(subject)
    await session.flush()
    return subject


async def delete_subject(session: AsyncSession, group_id: int, name: str) -> DeleteSubjectStatus:
    """Удаляет предмет, если на нём нет активных заданий."""
    subject = (
        await session.execute(
            select(Subject).where(
                Subject.group_id == group_id,
                func.lower(Subject.name) == _ci(name.strip()),
            )
        )
    ).scalar_one_or_none()
    if subject is None:
        return "not_found"
    has_active = (
        await session.execute(
            select(Assignment.id)
            .where(Assignment.subject_id == subject.id, Assignment.is_active.is_(True))
            .limit(1)
        )
    ).scalar_one_or_none()
    if has_active is not None:
        return "has_assignments"
    await session.delete(subject)
    return "ok"


async def get_subject_by_name(session: AsyncSession, group_id: int, name: str) -> Subject | None:
    return (
        await session.execute(
            select(Subject).where(
                Subject.group_id == group_id,
                func.lower(Subject.name) == _ci(name.strip()),
            )
        )
    ).scalar_one_or_none()

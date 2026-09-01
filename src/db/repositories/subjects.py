from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.db import models as orm
from src.domain.models import Subject


class SubjectRepository:
    """Доступ к предметам группы."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list_for_group(self, group_id: int) -> list[Subject]:
        """Возвращает предметы группы по алфавиту."""
        rows = await self._session.execute(
            select(orm.Subject).where(orm.Subject.group_id == group_id).order_by(orm.Subject.name)
        )
        return [Subject.model_validate(r) for r in rows.scalars()]

    async def get(self, subject_id: int) -> Subject | None:
        """Возвращает предмет по id."""
        row = await self._session.get(orm.Subject, subject_id)
        return Subject.model_validate(row) if row else None

    async def find_by_name(self, *, group_id: int, name: str) -> Subject | None:
        """Ищет предмет по имени без учёта регистра.

        Сравнение идёт в Python: SQLite lower() работает только с ASCII и на
        кириллице даёт ложные промахи.
        """
        needle = name.strip().casefold()
        for subject in await self.list_for_group(group_id):
            if subject.name.casefold() == needle:
                return subject
        return None

    async def create(self, *, group_id: int, name: str) -> Subject:
        """Создаёт предмет."""
        row = orm.Subject(group_id=group_id, name=name.strip())
        self._session.add(row)
        await self._session.flush()
        return Subject.model_validate(row)

    async def delete(self, subject_id: int) -> None:
        """Удаляет предмет."""
        row = await self._session.get(orm.Subject, subject_id)
        if row is not None:
            await self._session.delete(row)

    async def has_items(self, subject_id: int, *, active_only: bool = True) -> bool:
        """Есть ли у предмета записи (по умолчанию — только активные)."""
        query = select(orm.Item.id).where(orm.Item.subject_id == subject_id)
        if active_only:
            query = query.where(orm.Item.is_active.is_(True))
        found = (await self._session.execute(query.limit(1))).scalar_one_or_none()
        return found is not None

    async def has_upcoming_items(self, subject_id: int, *, now: datetime) -> bool:
        """Есть ли у предмета записи с ещё не наступившим дедлайном или без него."""
        found = (
            await self._session.execute(
                select(orm.Item.id)
                .where(
                    orm.Item.subject_id == subject_id,
                    orm.Item.is_active.is_(True),
                    (orm.Item.due_at.is_(None)) | (orm.Item.due_at >= now),
                )
                .limit(1)
            )
        ).scalar_one_or_none()
        return found is not None

    async def count_passed_items(self, subject_id: int, *, now: datetime) -> int:
        """Сколько у предмета прошедших записей."""
        rows = await self._session.execute(
            select(orm.Item.id).where(
                orm.Item.subject_id == subject_id,
                orm.Item.is_active.is_(True),
                orm.Item.due_at.is_not(None),
                orm.Item.due_at < now,
            )
        )
        return len(rows.all())

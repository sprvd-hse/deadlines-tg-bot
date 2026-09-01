from datetime import datetime

from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from src.db import models as orm
from src.domain.enums import ItemKind
from src.domain.models import Item, ItemDraft, ItemPatch


def to_domain(row: orm.Item) -> Item:
    """Преобразует ORM-запись в доменную модель."""
    return Item.model_validate(row)


class ItemRepository:
    """Доступ к заданиям и важным датам группы."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(self, item_id: int) -> Item | None:
        """Возвращает запись по id."""
        row = await self._session.get(orm.Item, item_id)
        return to_domain(row) if row else None

    async def next_short_id(self, group_id: int) -> int:
        """Следующий человекочитаемый номер записи в группе."""
        current = (
            await self._session.execute(
                select(func.max(orm.Item.short_id)).where(orm.Item.group_id == group_id)
            )
        ).scalar()
        return (current or 0) + 1

    async def create(
        self, *, group_id: int, short_id: int, author_id: int, draft: ItemDraft
    ) -> Item:
        """Создаёт запись из черновика."""
        row = orm.Item(
            group_id=group_id,
            subject_id=draft.subject_id,
            short_id=short_id,
            kind=draft.kind,
            category=draft.category,
            title=draft.title,
            description=draft.description,
            links=list(draft.links),
            due_at=draft.due_at,
            reminder_offsets=draft.reminder_offsets,
            reminders_muted=draft.reminders_muted,
            created_by_user_id=author_id,
        )
        self._session.add(row)
        await self._session.flush()
        return to_domain(row)

    async def apply_patch(self, item_id: int, patch: ItemPatch) -> Item | None:
        """Применяет изменения к записи и возвращает обновлённую версию."""
        row = await self._session.get(orm.Item, item_id)
        if row is None:
            return None
        if patch.subject_id is not None:
            row.subject_id = patch.subject_id
        if patch.category is not None:
            row.category = patch.category
        if patch.title is not None:
            row.title = patch.title
        if patch.clear_description:
            row.description = None
        elif patch.description is not None:
            row.description = patch.description
        if patch.links is not None:
            row.links = list(patch.links)
        if patch.clear_due_at:
            row.due_at = None
        elif patch.due_at is not None:
            row.due_at = patch.due_at
        if patch.reset_reminder_offsets:
            row.reminder_offsets = None
        elif patch.reminder_offsets is not None:
            row.reminder_offsets = list(patch.reminder_offsets)
        if patch.reminders_muted is not None:
            row.reminders_muted = patch.reminders_muted
        await self._session.flush()
        return to_domain(row)

    async def deactivate(self, item_id: int) -> None:
        """Мягко удаляет запись."""
        await self._session.execute(
            update(orm.Item).where(orm.Item.id == item_id).values(is_active=False)
        )

    async def list_active(
        self,
        group_id: int,
        *,
        kind: ItemKind | None = None,
        now: datetime | None = None,
        until: datetime | None = None,
        include_open: bool = True,
    ) -> list[Item]:
        """Активные записи с дедлайном в будущем, по возрастанию дедлайна.

        `include_open` добавляет задачи без дедлайна (они идут в конце списка).
        """
        query = select(orm.Item).where(orm.Item.group_id == group_id, orm.Item.is_active.is_(True))
        if kind is not None:
            query = query.where(orm.Item.kind == kind)
        conditions = []
        if now is not None:
            future = orm.Item.due_at >= now
            if until is not None:
                future = future & (orm.Item.due_at < until)
            conditions.append(future)
            if include_open:
                conditions.append(orm.Item.due_at.is_(None))
            query = query.where(_any_of(conditions))
        elif not include_open:
            query = query.where(orm.Item.due_at.is_not(None))
        rows = await self._session.execute(query.order_by(orm.Item.due_at.asc().nulls_last()))
        return [to_domain(r) for r in rows.scalars()]

    async def list_passed(
        self, group_id: int, *, now: datetime, limit: int | None = None
    ) -> list[Item]:
        """Активные записи с прошедшим дедлайном, свежие первыми."""
        query = (
            select(orm.Item)
            .where(
                orm.Item.group_id == group_id,
                orm.Item.is_active.is_(True),
                orm.Item.due_at.is_not(None),
                orm.Item.due_at < now,
            )
            .order_by(orm.Item.due_at.desc())
        )
        if limit is not None:
            query = query.limit(limit)
        rows = await self._session.execute(query)
        return [to_domain(r) for r in rows.scalars()]

    async def list_scheduled(self, *, after: datetime) -> list[Item]:
        """Активные записи с дедлайном позже указанного момента (для bootstrap планировщика)."""
        rows = await self._session.execute(
            select(orm.Item).where(
                orm.Item.is_active.is_(True),
                orm.Item.due_at.is_not(None),
                orm.Item.due_at > after,
            )
        )
        return [to_domain(r) for r in rows.scalars()]

    async def purge_for_subject(self, subject_id: int, *, archived_only: bool = True) -> int:
        """Окончательно удаляет записи предмета: скрытые либо все сразу.

        Нужно перед удалением самого предмета: у items.subject_id стоит
        ON DELETE RESTRICT, поэтому любые оставшиеся записи блокируют удаление.
        Журнал напоминаний и отметки уходят по каскаду.
        """
        query = select(orm.Item.id).where(orm.Item.subject_id == subject_id)
        if archived_only:
            query = query.where(orm.Item.is_active.is_(False))
        rows = await self._session.execute(query)
        item_ids = list(rows.scalars())
        if not item_ids:
            return 0
        await self._session.execute(delete(orm.Item).where(orm.Item.id.in_(item_ids)))
        return len(item_ids)

    async def clear_reminder_log(self, item_id: int) -> None:
        """Сбрасывает журнал отправленных напоминаний записи."""
        await self._session.execute(
            delete(orm.ReminderLog).where(orm.ReminderLog.item_id == item_id)
        )


def _any_of(conditions: list):
    """Собирает OR по списку условий."""
    result = conditions[0]
    for condition in conditions[1:]:
        result = result | condition
    return result

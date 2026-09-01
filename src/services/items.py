import logging
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.repositories.completions import CompletionRepository
from src.db.repositories.groups import GroupRepository
from src.db.repositories.items import ItemRepository
from src.db.repositories.subjects import SubjectRepository
from src.domain.enums import ItemKind
from src.domain.models import Group, Item, ItemDraft, ItemPatch, Subject

logger = logging.getLogger(__name__)

_SHORT_ID_RETRIES = 5


@dataclass(frozen=True, slots=True)
class ItemBoard:
    """Снимок записей группы для отрисовки списка."""

    group: Group
    tasks: list[Item]
    events: list[Item]
    subject_names: dict[int, str]
    completed_ids: frozenset[int] = frozenset()

    @property
    def is_empty(self) -> bool:
        """Нет ни одной активной записи."""
        return not self.tasks and not self.events


class ItemManager:
    """Операции над заданиями и важными датами группы."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._items = ItemRepository(session)
        self._subjects = SubjectRepository(session)
        self._groups = GroupRepository(session)
        self._completions = CompletionRepository(session)

    async def create(self, *, group_id: int, author_id: int, draft: ItemDraft) -> Item:
        """Создаёт запись, подбирая свободный короткий номер при гонке."""
        for attempt in range(_SHORT_ID_RETRIES):
            short_id = await self._items.next_short_id(group_id)
            try:
                async with self._session.begin_nested():
                    return await self._items.create(
                        group_id=group_id, short_id=short_id, author_id=author_id, draft=draft
                    )
            except IntegrityError:
                logger.warning(
                    "Конфликт short_id=%s в группе %s, попытка %s", short_id, group_id, attempt + 1
                )
        raise RuntimeError("Не удалось подобрать номер задания")

    async def get(self, item_id: int) -> Item | None:
        """Возвращает запись по id."""
        return await self._items.get(item_id)

    async def update(self, item_id: int, patch: ItemPatch) -> Item | None:
        """Применяет изменения; при сдвиге сроков сбрасывает журнал напоминаний."""
        before = await self._items.get(item_id)
        if before is None:
            return None
        after = await self._items.apply_patch(item_id, patch)
        if after is not None and _reminders_changed(before, after):
            await self._items.clear_reminder_log(item_id)
        return after

    async def delete(self, item_id: int) -> None:
        """Мягко удаляет запись."""
        await self._items.deactivate(item_id)

    async def close_open_task(self, item_id: int) -> Item | None:
        """Закрывает задачу без дедлайна как прошедшую."""
        await self._items.deactivate(item_id)
        return await self._items.get(item_id)

    async def board(
        self, group: Group, *, user_id: int | None = None, now: datetime | None = None
    ) -> ItemBoard:
        """Собирает активные записи группы вместе с предметами и личными отметками."""
        moment = now or datetime.now(tz=UTC)
        tasks = await self._items.list_active(group.id, kind=ItemKind.TASK, now=moment)
        events = await self._items.list_active(
            group.id, kind=ItemKind.EVENT, now=moment, include_open=False
        )
        completed: frozenset[int] = frozenset()
        if user_id is not None:
            completed = frozenset(
                await self._completions.completed_ids(
                    user_id=user_id, item_ids=[i.id for i in tasks + events]
                )
            )
        return ItemBoard(
            group=group,
            tasks=tasks,
            events=events,
            subject_names=await self.subject_names(group.id),
            completed_ids=completed,
        )

    async def is_completed(self, *, item_id: int, user_id: int) -> bool:
        """Отмечена ли запись выполненной этим участником."""
        return await self._completions.is_completed(user_id=user_id, item_id=item_id)

    async def toggle_completion(self, *, item_id: int, user_id: int) -> bool:
        """Переключает личную отметку и возвращает её новое значение."""
        if await self._completions.is_completed(user_id=user_id, item_id=item_id):
            await self._completions.unmark(user_id=user_id, item_id=item_id)
            return False
        await self._completions.mark(user_id=user_id, item_id=item_id)
        return True

    async def passed(
        self, group_id: int, *, now: datetime | None = None, limit: int = 30
    ) -> list[Item]:
        """Записи с прошедшим дедлайном."""
        moment = now or datetime.now(tz=UTC)
        return await self._items.list_passed(group_id, now=moment, limit=limit)

    async def upcoming(
        self, group_id: int, *, now: datetime, until: datetime | None = None
    ) -> list[Item]:
        """Все активные записи в горизонте, включая задачи без дедлайна."""
        return await self._items.list_active(group_id, now=now, until=until)

    async def subject_names(self, group_id: int) -> dict[int, str]:
        """Отображение id предмета в название."""
        return {s.id: s.name for s in await self._subjects.list_for_group(group_id)}

    async def subjects(self, group_id: int) -> list[Subject]:
        """Предметы группы."""
        return await self._subjects.list_for_group(group_id)

    async def can_modify(self, *, item: Item, user_id: int) -> bool:
        """Право менять запись: автор или создатель группы."""
        if item.created_by_user_id == user_id:
            return True
        member = await self._groups.get_member(group_id=item.group_id, user_id=user_id)
        return member is not None and member.is_creator


def _reminders_changed(before: Item, after: Item) -> bool:
    """Изменились ли параметры, влияющие на расписание напоминаний."""
    return (
        before.due_at != after.due_at
        or before.reminder_offsets != after.reminder_offsets
        or before.reminders_muted != after.reminders_muted
    )

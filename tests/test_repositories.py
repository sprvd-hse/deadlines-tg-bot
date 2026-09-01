from datetime import UTC, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from src.db.repositories.groups import GroupRepository
from src.db.repositories.items import ItemRepository
from src.db.repositories.reminders import ReminderLogRepository
from src.db.repositories.subjects import SubjectRepository
from src.db.repositories.users import UserRepository
from src.domain.enums import EventCategory, ItemKind, MemberRole, TaskCategory
from src.domain.models import Group, ItemDraft, ItemPatch, Subject, User

NOW = datetime(2026, 6, 15, 12, 0, tzinfo=UTC)


async def _fixture_group(session: AsyncSession) -> tuple[User, Group, Subject]:
    user = await UserRepository(session).get_or_create(
        tg_user_id=1, tg_username="u", full_name="Аня"
    )
    group = await GroupRepository(session).create(
        name="кабаньи дедлайны",
        invite_code="AAAA-BBBB-CCCC",
        timezone="Europe/Vilnius",
        creator_id=user.id,
    )
    subject = await SubjectRepository(session).create(group_id=group.id, name="Матан")
    return user, group, subject


def _draft(subject_id: int, **overrides) -> ItemDraft:
    data = {
        "kind": ItemKind.TASK,
        "subject_id": subject_id,
        "category": TaskCategory.HOMEWORK,
        "title": "Задача",
    }
    return ItemDraft(**(data | overrides))


class TestUserRepository:
    async def test_creates_then_returns_same_user(self, session: AsyncSession):
        repo = UserRepository(session)
        first = await repo.get_or_create(tg_user_id=7, tg_username="a", full_name="Аня")
        second = await repo.get_or_create(tg_user_id=7, tg_username="a", full_name="Аня")
        assert first.id == second.id

    async def test_refreshes_profile_on_repeat(self, session: AsyncSession):
        repo = UserRepository(session)
        await repo.get_or_create(tg_user_id=7, tg_username="old", full_name="Старое")
        updated = await repo.get_or_create(tg_user_id=7, tg_username="new", full_name="Новое")
        assert (updated.tg_username, updated.full_name) == ("new", "Новое")

    async def test_set_active_group(self, session: AsyncSession):
        user, group, _ = await _fixture_group(session)
        repo = UserRepository(session)
        await repo.set_active_group(user_id=user.id, group_id=group.id)
        assert (await repo.get(user.id)).active_group_id == group.id


class TestGroupRepository:
    async def test_creator_is_added_as_member(self, session: AsyncSession):
        user, group, _ = await _fixture_group(session)
        member = await GroupRepository(session).get_member(group_id=group.id, user_id=user.id)
        assert member is not None
        assert member.is_creator

    async def test_group_carries_reminder_defaults(self, session: AsyncSession):
        _, group, _ = await _fixture_group(session)
        assert group.reminders.task_offsets == [1440]
        assert group.reminders.event_offsets == [10080, 4320, 1440]
        assert group.reminders.reminder_hour == 10

    async def test_update_reminder_settings_roundtrip(self, session: AsyncSession):
        _, group, _ = await _fixture_group(session)
        repo = GroupRepository(session)
        await repo.update_settings(group.id, default_task_offsets=[120, 60], reminder_hour=8)
        reloaded = await repo.get(group.id)
        assert reloaded.reminders.task_offsets == [120, 60]
        assert reloaded.reminders.reminder_hour == 8

    async def test_lookup_by_invite_code_and_chat(self, session: AsyncSession):
        _, group, _ = await _fixture_group(session)
        repo = GroupRepository(session)
        assert (await repo.get_by_invite_code("AAAA-BBBB-CCCC")).id == group.id
        assert await repo.get_by_invite_code("NOPE") is None
        await repo.update_settings(group.id, chat_id=-100500)
        assert (await repo.get_by_chat_id(-100500)).id == group.id

    async def test_membership_lifecycle(self, session: AsyncSession):
        _, group, _ = await _fixture_group(session)
        repo = GroupRepository(session)
        other = await UserRepository(session).get_or_create(
            tg_user_id=2, tg_username=None, full_name="Боря"
        )
        await repo.add_member(group_id=group.id, user_id=other.id)
        assert await repo.count_members(group.id) == 2
        await repo.set_role(group_id=group.id, user_id=other.id, role=MemberRole.CREATOR)
        assert (await repo.get_member(group_id=group.id, user_id=other.id)).is_creator
        await repo.remove_member(group_id=group.id, user_id=other.id)
        assert await repo.count_members(group.id) == 1

    async def test_personal_reminders_toggle(self, session: AsyncSession):
        user, group, _ = await _fixture_group(session)
        repo = GroupRepository(session)
        await repo.set_personal_reminders(group_id=group.id, user_id=user.id, enabled=False)
        member = await repo.get_member(group_id=group.id, user_id=user.id)
        assert member.personal_reminders_enabled is False

    async def test_delete_group_removes_items_and_clears_active(self, session: AsyncSession):
        user, group, subject = await _fixture_group(session)
        items = ItemRepository(session)
        await items.create(
            group_id=group.id, short_id=1, author_id=user.id, draft=_draft(subject.id)
        )
        await UserRepository(session).set_active_group(user_id=user.id, group_id=group.id)
        repo = GroupRepository(session)

        await repo.delete_group(group.id)

        assert await repo.get(group.id) is None
        assert (await UserRepository(session).get(user.id)).active_group_id is None
        assert await SubjectRepository(session).get(subject.id) is None


class TestSubjectRepository:
    async def test_find_by_name_is_case_insensitive(self, session: AsyncSession):
        _, group, _ = await _fixture_group(session)
        repo = SubjectRepository(session)
        assert (await repo.find_by_name(group_id=group.id, name="  матан ")) is not None

    async def test_has_items_ignores_soft_deleted(self, session: AsyncSession):
        user, group, subject = await _fixture_group(session)
        repo = SubjectRepository(session)
        items = ItemRepository(session)
        assert not await repo.has_items(subject.id)
        item = await items.create(
            group_id=group.id, short_id=1, author_id=user.id, draft=_draft(subject.id)
        )
        assert await repo.has_items(subject.id)
        await items.deactivate(item.id)
        assert not await repo.has_items(subject.id)


class TestItemRepository:
    async def test_short_ids_increment_per_group(self, session: AsyncSession):
        user, group, subject = await _fixture_group(session)
        repo = ItemRepository(session)
        assert await repo.next_short_id(group.id) == 1
        await repo.create(
            group_id=group.id, short_id=1, author_id=user.id, draft=_draft(subject.id)
        )
        assert await repo.next_short_id(group.id) == 2

    async def test_create_event_with_deadline(self, session: AsyncSession):
        user, group, subject = await _fixture_group(session)
        repo = ItemRepository(session)
        item = await repo.create(
            group_id=group.id,
            short_id=1,
            author_id=user.id,
            draft=_draft(
                subject.id,
                kind=ItemKind.EVENT,
                category=EventCategory.EXAM,
                due_at=NOW + timedelta(days=3),
            ),
        )
        assert item.kind is ItemKind.EVENT
        assert item.due_at is not None

    async def test_active_list_orders_by_deadline_with_open_last(self, session: AsyncSession):
        user, group, subject = await _fixture_group(session)
        repo = ItemRepository(session)
        await repo.create(
            group_id=group.id,
            short_id=1,
            author_id=user.id,
            draft=_draft(subject.id, title="Без срока"),
        )
        await repo.create(
            group_id=group.id,
            short_id=2,
            author_id=user.id,
            draft=_draft(subject.id, title="Поздняя", due_at=NOW + timedelta(days=5)),
        )
        await repo.create(
            group_id=group.id,
            short_id=3,
            author_id=user.id,
            draft=_draft(subject.id, title="Ранняя", due_at=NOW + timedelta(days=1)),
        )
        titles = [i.title for i in await repo.list_active(group.id, now=NOW)]
        assert titles == ["Ранняя", "Поздняя", "Без срока"]

    async def test_active_list_can_exclude_open_and_filter_kind(self, session: AsyncSession):
        user, group, subject = await _fixture_group(session)
        repo = ItemRepository(session)
        await repo.create(
            group_id=group.id, short_id=1, author_id=user.id, draft=_draft(subject.id)
        )
        await repo.create(
            group_id=group.id,
            short_id=2,
            author_id=user.id,
            draft=_draft(
                subject.id,
                kind=ItemKind.EVENT,
                category=EventCategory.EXAM,
                due_at=NOW + timedelta(days=2),
            ),
        )
        events = await repo.list_active(group.id, kind=ItemKind.EVENT, now=NOW)
        assert len(events) == 1
        no_open = await repo.list_active(group.id, kind=ItemKind.TASK, now=NOW, include_open=False)
        assert no_open == []

    async def test_horizon_cuts_far_deadlines(self, session: AsyncSession):
        user, group, subject = await _fixture_group(session)
        repo = ItemRepository(session)
        await repo.create(
            group_id=group.id,
            short_id=1,
            author_id=user.id,
            draft=_draft(subject.id, due_at=NOW + timedelta(days=30)),
        )
        within = await repo.list_active(
            group.id, now=NOW, until=NOW + timedelta(days=14), include_open=False
        )
        assert within == []

    async def test_passed_list_newest_first(self, session: AsyncSession):
        user, group, subject = await _fixture_group(session)
        repo = ItemRepository(session)
        await repo.create(
            group_id=group.id,
            short_id=1,
            author_id=user.id,
            draft=_draft(subject.id, title="Старое", due_at=NOW - timedelta(days=5)),
        )
        await repo.create(
            group_id=group.id,
            short_id=2,
            author_id=user.id,
            draft=_draft(subject.id, title="Недавнее", due_at=NOW - timedelta(days=1)),
        )
        titles = [i.title for i in await repo.list_passed(group.id, now=NOW)]
        assert titles == ["Недавнее", "Старое"]

    async def test_deactivated_items_disappear(self, session: AsyncSession):
        user, group, subject = await _fixture_group(session)
        repo = ItemRepository(session)
        item = await repo.create(
            group_id=group.id, short_id=1, author_id=user.id, draft=_draft(subject.id)
        )
        await repo.deactivate(item.id)
        assert await repo.list_active(group.id, now=NOW) == []

    async def test_patch_updates_and_clears_fields(self, session: AsyncSession):
        user, group, subject = await _fixture_group(session)
        repo = ItemRepository(session)
        item = await repo.create(
            group_id=group.id,
            short_id=1,
            author_id=user.id,
            draft=_draft(subject.id, description="старое", due_at=NOW + timedelta(days=1)),
        )
        updated = await repo.apply_patch(
            item.id, ItemPatch(title="Новое", clear_description=True, clear_due_at=True)
        )
        assert updated.title == "Новое"
        assert updated.description is None
        assert updated.due_at is None

    async def test_patch_reminder_offsets_override_and_reset(self, session: AsyncSession):
        user, group, subject = await _fixture_group(session)
        repo = ItemRepository(session)
        item = await repo.create(
            group_id=group.id, short_id=1, author_id=user.id, draft=_draft(subject.id)
        )
        overridden = await repo.apply_patch(item.id, ItemPatch(reminder_offsets=[60]))
        assert overridden.reminder_offsets == [60]
        reset = await repo.apply_patch(item.id, ItemPatch(reset_reminder_offsets=True))
        assert reset.reminder_offsets is None

    async def test_scheduled_list_skips_past_and_open(self, session: AsyncSession):
        user, group, subject = await _fixture_group(session)
        repo = ItemRepository(session)
        await repo.create(
            group_id=group.id, short_id=1, author_id=user.id, draft=_draft(subject.id)
        )
        await repo.create(
            group_id=group.id,
            short_id=2,
            author_id=user.id,
            draft=_draft(subject.id, due_at=NOW - timedelta(days=1)),
        )
        future = await repo.create(
            group_id=group.id,
            short_id=3,
            author_id=user.id,
            draft=_draft(subject.id, due_at=NOW + timedelta(days=1)),
        )
        assert [i.id for i in await repo.list_scheduled(after=NOW)] == [future.id]


class TestReminderLogRepository:
    async def test_marks_and_reads_back(self, session: AsyncSession):
        user, group, subject = await _fixture_group(session)
        item = await ItemRepository(session).create(
            group_id=group.id, short_id=1, author_id=user.id, draft=_draft(subject.id)
        )
        repo = ReminderLogRepository(session)
        assert await repo.sent_user_ids(item_id=item.id, offset_minutes=1440) == set()
        await repo.mark_sent(item_id=item.id, user_id=user.id, offset_minutes=1440)
        await session.flush()
        assert await repo.sent_user_ids(item_id=item.id, offset_minutes=1440) == {user.id}
        assert await repo.sent_user_ids(item_id=item.id, offset_minutes=60) == set()

    async def test_clear_log_resets_history(self, session: AsyncSession):
        user, group, subject = await _fixture_group(session)
        items = ItemRepository(session)
        item = await items.create(
            group_id=group.id, short_id=1, author_id=user.id, draft=_draft(subject.id)
        )
        repo = ReminderLogRepository(session)
        await repo.mark_sent(item_id=item.id, user_id=user.id, offset_minutes=1440)
        await session.flush()
        await items.clear_reminder_log(item.id)
        assert await repo.sent_user_ids(item_id=item.id, offset_minutes=1440) == set()

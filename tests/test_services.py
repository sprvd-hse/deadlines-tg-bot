from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from src.db.models import Base
from src.db.repositories.groups import GroupRepository
from src.domain.enums import EventCategory, ItemKind, TaskCategory
from src.domain.models import ItemDraft, ItemPatch
from src.services.digest import DigestService, is_digest_due, last_digest_slot
from src.services.groups import GroupManager, check_join_rate_limit
from src.services.items import ItemManager
from src.services.reminders import ReminderService, schedule_for

NOW = datetime(2026, 6, 15, 9, 0, tzinfo=UTC)


class FakeBot:
    """Заглушка бота: собирает отправленные сообщения, умеет падать."""

    def __init__(self, fail_for: set[int] | None = None) -> None:
        self.sent: list[tuple[int, str]] = []
        self._fail_for = fail_for or set()

    async def send_message(self, chat_id: int, text: str, **kwargs) -> None:
        if chat_id in self._fail_for:
            raise RuntimeError("telegram down")
        self.sent.append((chat_id, text))


@pytest.fixture
async def factory():
    """Фабрика сессий поверх одной in-memory базы."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    await engine.dispose()


async def _seed(session: AsyncSession, *, tz: str = "Europe/Vilnius"):
    groups = GroupManager(session)
    user = await groups._users.get_or_create(tg_user_id=1, tg_username=None, full_name="Аня")
    group = await groups.create_group(creator=user, name="кабаны", timezone=tz)
    subject, _ = await groups.add_subject(group_id=group.id, name="Матан")
    await session.commit()
    return user, group, subject


class TestGroupManager:
    async def test_context_returns_active_group(self, session: AsyncSession):
        user, group, _ = await _seed(session)
        ctx = await GroupManager(session).context(
            tg_user_id=user.tg_user_id, tg_username=None, full_name="Аня"
        )
        assert ctx.has_group
        assert ctx.is_creator
        assert ctx.group.id == group.id

    async def test_context_recovers_from_deleted_group(self, session: AsyncSession):
        user, group, _ = await _seed(session)
        manager = GroupManager(session)
        await manager.delete_group(group.id)
        ctx = await manager.context(tg_user_id=user.tg_user_id, tg_username=None, full_name="Аня")
        assert not ctx.has_group

    async def test_join_by_code(self, session: AsyncSession):
        _, group, _ = await _seed(session)
        manager = GroupManager(session)
        other = await manager._users.get_or_create(tg_user_id=2, tg_username=None, full_name="Боря")
        joined, status = await manager.join_group(user=other, invite_code=group.invite_code)
        assert status == "ok"
        assert joined.id == group.id
        _, status_again = await manager.join_group(user=other, invite_code=group.invite_code)
        assert status_again == "already_member"

    async def test_join_with_unknown_code(self, session: AsyncSession):
        _, _, _ = await _seed(session)
        manager = GroupManager(session)
        user = await manager._users.get_or_create(tg_user_id=3, tg_username=None, full_name="В")
        group, status = await manager.join_group(user=user, invite_code="ZZZZ-ZZZZ-ZZZZ")
        assert (group, status) == (None, "not_found")

    async def test_rate_limit_blocks_sixth_attempt(self):
        assert all(check_join_rate_limit(999) for _ in range(5))
        assert not check_join_rate_limit(999)

    async def test_creator_cannot_leave_with_other_members(self, session: AsyncSession):
        user, group, _ = await _seed(session)
        manager = GroupManager(session)
        other = await manager._users.get_or_create(tg_user_id=2, tg_username=None, full_name="Б")
        await manager.join_group(user=other, invite_code=group.invite_code)
        assert await manager.leave_group(group_id=group.id, user_id=user.id) == "need_transfer"

    async def test_last_member_leaving_deletes_group(self, session: AsyncSession):
        user, group, _ = await _seed(session)
        manager = GroupManager(session)
        assert await manager.leave_group(group_id=group.id, user_id=user.id) == "group_deleted"
        assert await manager.get_group(group.id) is None

    async def test_transfer_ownership_swaps_roles(self, session: AsyncSession):
        user, group, _ = await _seed(session)
        manager = GroupManager(session)
        other = await manager._users.get_or_create(tg_user_id=2, tg_username=None, full_name="Б")
        await manager.join_group(user=other, invite_code=group.invite_code)
        status = await manager.transfer_ownership(
            group_id=group.id, current_creator_id=user.id, new_creator_id=other.id
        )
        assert status == "ok"
        repo = GroupRepository(session)
        assert (await repo.get_member(group_id=group.id, user_id=other.id)).is_creator
        assert not (await repo.get_member(group_id=group.id, user_id=user.id)).is_creator

    async def test_duplicate_subject_rejected(self, session: AsyncSession):
        _, group, _ = await _seed(session)
        manager = GroupManager(session)
        subject, status = await manager.add_subject(group_id=group.id, name="матан")
        assert (subject, status) == (None, "duplicate")

    async def test_subject_with_items_not_deleted(self, session: AsyncSession):
        user, group, subject = await _seed(session)
        await ItemManager(session).create(
            group_id=group.id,
            author_id=user.id,
            draft=ItemDraft(
                kind=ItemKind.TASK,
                subject_id=subject.id,
                category=TaskCategory.LAB,
                title="Лаба",
            ),
        )
        assert await GroupManager(session).delete_subject(subject.id) == "has_items"

    async def test_regenerated_invite_code_changes(self, session: AsyncSession):
        _, group, _ = await _seed(session)
        manager = GroupManager(session)
        new_code = await manager.regenerate_invite_code(group.id)
        assert new_code != group.invite_code
        assert (await manager.get_group(group.id)).invite_code == new_code


class TestItemManager:
    async def test_short_ids_are_sequential(self, session: AsyncSession):
        user, group, subject = await _seed(session)
        manager = ItemManager(session)
        first = await manager.create(
            group_id=group.id,
            author_id=user.id,
            draft=ItemDraft(
                kind=ItemKind.TASK, subject_id=subject.id, category=TaskCategory.LAB, title="A"
            ),
        )
        second = await manager.create(
            group_id=group.id,
            author_id=user.id,
            draft=ItemDraft(
                kind=ItemKind.TASK, subject_id=subject.id, category=TaskCategory.LAB, title="B"
            ),
        )
        assert (first.short_id, second.short_id) == (1, 2)

    async def test_board_splits_tasks_and_events(self, session: AsyncSession):
        user, group, subject = await _seed(session)
        manager = ItemManager(session)
        await manager.create(
            group_id=group.id,
            author_id=user.id,
            draft=ItemDraft(
                kind=ItemKind.TASK, subject_id=subject.id, category=TaskCategory.LAB, title="Лаба"
            ),
        )
        await manager.create(
            group_id=group.id,
            author_id=user.id,
            draft=ItemDraft(
                kind=ItemKind.EVENT,
                subject_id=subject.id,
                category=EventCategory.EXAM,
                title="Экзамен",
                due_at=NOW + timedelta(days=3),
            ),
        )
        board = await manager.board(group, now=NOW)
        assert [i.title for i in board.tasks] == ["Лаба"]
        assert [i.title for i in board.events] == ["Экзамен"]
        assert board.subject_names[subject.id] == "Матан"
        assert not board.is_empty

    async def test_changing_deadline_clears_reminder_log(self, session: AsyncSession):
        from src.db.repositories.reminders import ReminderLogRepository

        user, group, subject = await _seed(session)
        manager = ItemManager(session)
        item = await manager.create(
            group_id=group.id,
            author_id=user.id,
            draft=ItemDraft(
                kind=ItemKind.TASK,
                subject_id=subject.id,
                category=TaskCategory.LAB,
                title="Лаба",
                due_at=NOW + timedelta(days=2),
            ),
        )
        log = ReminderLogRepository(session)
        await log.mark_sent(item_id=item.id, user_id=user.id, offset_minutes=1440)
        await session.flush()

        await manager.update(item.id, ItemPatch(due_at=NOW + timedelta(days=5)))
        assert await log.sent_user_ids(item_id=item.id, offset_minutes=1440) == set()

    async def test_editing_title_keeps_reminder_log(self, session: AsyncSession):
        from src.db.repositories.reminders import ReminderLogRepository

        user, group, subject = await _seed(session)
        manager = ItemManager(session)
        item = await manager.create(
            group_id=group.id,
            author_id=user.id,
            draft=ItemDraft(
                kind=ItemKind.TASK,
                subject_id=subject.id,
                category=TaskCategory.LAB,
                title="Лаба",
                due_at=NOW + timedelta(days=2),
            ),
        )
        log = ReminderLogRepository(session)
        await log.mark_sent(item_id=item.id, user_id=user.id, offset_minutes=1440)
        await session.flush()

        await manager.update(item.id, ItemPatch(title="Лаба 2"))
        assert await log.sent_user_ids(item_id=item.id, offset_minutes=1440) == {user.id}

    async def test_author_and_creator_may_modify(self, session: AsyncSession):
        user, group, subject = await _seed(session)
        manager = ItemManager(session)
        groups = GroupManager(session)
        other = await groups._users.get_or_create(tg_user_id=2, tg_username=None, full_name="Б")
        await groups.join_group(user=other, invite_code=group.invite_code)
        item = await manager.create(
            group_id=group.id,
            author_id=other.id,
            draft=ItemDraft(
                kind=ItemKind.TASK, subject_id=subject.id, category=TaskCategory.LAB, title="Лаба"
            ),
        )
        assert await manager.can_modify(item=item, user_id=other.id)
        assert await manager.can_modify(item=item, user_id=user.id)


class TestDigestSlots:
    def _group(self, session=None, **kwargs):
        from src.domain.models import Group

        data = {
            "id": 1,
            "name": "г",
            "invite_code": "AAAA-BBBB-CCCC",
            "timezone": "Europe/Vilnius",
            "digest_day": 6,
            "digest_hour": 19,
            "created_by_user_id": 1,
        }
        return Group(**(data | kwargs))

    def test_slot_is_last_sunday_evening(self):
        # четверг 18.06.2026
        now = datetime(2026, 6, 18, 12, 0, tzinfo=UTC)
        slot = last_digest_slot(self._group(), now).astimezone(self._group().tz)
        assert slot.weekday() == 6
        assert slot.hour == 19
        assert slot < now

    def test_slot_before_hour_on_digest_day_points_to_previous_week(self):
        # воскресенье 21.06.2026, 10:00 по Вильнюсу — слот 19:00 ещё не наступил
        now = datetime(2026, 6, 21, 7, 0, tzinfo=UTC)
        slot = last_digest_slot(self._group(), now).astimezone(self._group().tz)
        assert slot.day == 14

    def test_due_when_never_sent(self):
        assert is_digest_due(self._group(), datetime(2026, 6, 18, 12, 0, tzinfo=UTC))

    def test_not_due_when_already_sent_in_slot(self):
        now = datetime(2026, 6, 18, 12, 0, tzinfo=UTC)
        group = self._group(digest_last_sent_at=now - timedelta(hours=1))
        assert not is_digest_due(group, now)

    def test_due_again_in_next_slot(self):
        now = datetime(2026, 6, 18, 12, 0, tzinfo=UTC)
        group = self._group(digest_last_sent_at=now - timedelta(days=8))
        assert is_digest_due(group, now)

    def test_naive_last_sent_treated_as_utc(self):
        now = datetime(2026, 6, 18, 12, 0, tzinfo=UTC)
        group = self._group(digest_last_sent_at=datetime(2026, 6, 18, 11, 0))
        assert not is_digest_due(group, now)


class TestReminderService:
    async def _prepare_item(self, factory, *, muted: bool = False, offsets=None):
        async with factory() as session:
            user, group, subject = await _seed(session)
            item = await ItemManager(session).create(
                group_id=group.id,
                author_id=user.id,
                draft=ItemDraft(
                    kind=ItemKind.TASK,
                    subject_id=subject.id,
                    category=TaskCategory.LAB,
                    title="Лаба",
                    due_at=NOW + timedelta(days=2),
                    reminder_offsets=offsets,
                    reminders_muted=muted,
                ),
            )
            await session.commit()
        return user, group, item

    async def test_sends_to_members_once(self, factory):
        user, _, item = await self._prepare_item(factory)
        bot = FakeBot()
        service = ReminderService(factory, bot)

        sent = await service.send_batch(item_id=item.id, offset_minutes=1440, now=NOW)
        assert sent == 1
        assert bot.sent[0][0] == user.tg_user_id

        again = await service.send_batch(item_id=item.id, offset_minutes=1440, now=NOW)
        assert again == 0
        assert len(bot.sent) == 1

    async def test_failed_send_is_retried_next_time(self, factory):
        user, _, item = await self._prepare_item(factory)
        failing = FakeBot(fail_for={user.tg_user_id})
        assert (
            await ReminderService(factory, failing).send_batch(
                item_id=item.id, offset_minutes=1440, now=NOW
            )
            == 0
        )

        working = FakeBot()
        assert (
            await ReminderService(factory, working).send_batch(
                item_id=item.id, offset_minutes=1440, now=NOW
            )
            == 1
        )

    async def test_unknown_offset_is_ignored(self, factory):
        _, _, item = await self._prepare_item(factory)
        bot = FakeBot()
        assert (
            await ReminderService(factory, bot).send_batch(
                item_id=item.id, offset_minutes=99, now=NOW
            )
            == 0
        )

    async def test_muted_item_sends_nothing(self, factory):
        _, _, item = await self._prepare_item(factory, muted=True)
        bot = FakeBot()
        assert (
            await ReminderService(factory, bot).send_batch(
                item_id=item.id, offset_minutes=1440, now=NOW
            )
            == 0
        )

    async def test_nothing_after_deadline(self, factory):
        _, _, item = await self._prepare_item(factory)
        bot = FakeBot()
        assert (
            await ReminderService(factory, bot).send_batch(
                item_id=item.id, offset_minutes=1440, now=NOW + timedelta(days=3)
            )
            == 0
        )

    async def test_disabled_personal_reminders_excluded(self, factory):
        user, group, item = await self._prepare_item(factory)
        async with factory() as session, session.begin():
            await GroupManager(session).set_personal_reminders(
                group_id=group.id, user_id=user.id, enabled=False
            )
        bot = FakeBot()
        assert (
            await ReminderService(factory, bot).send_batch(
                item_id=item.id, offset_minutes=1440, now=NOW
            )
            == 0
        )

    async def test_deleted_item_sends_nothing(self, factory):
        _, _, item = await self._prepare_item(factory)
        async with factory() as session, session.begin():
            await ItemManager(session).delete(item.id)
        bot = FakeBot()
        assert (
            await ReminderService(factory, bot).send_batch(
                item_id=item.id, offset_minutes=1440, now=NOW
            )
            == 0
        )

    async def test_schedule_for_uses_group_defaults(self, factory):
        _, group, item = await self._prepare_item(factory)
        planned = schedule_for(item, group)
        assert [p.offset_minutes for p in planned] == [1440]

    async def test_schedule_for_uses_item_override(self, factory):
        _, group, item = await self._prepare_item(factory, offsets=[120, 60])
        planned = schedule_for(item, group)
        assert [p.offset_minutes for p in planned] == [120, 60]


class TestDigestService:
    async def test_sends_to_bound_chat(self, factory):
        async with factory() as session:
            user, group, subject = await _seed(session)
            await ItemManager(session).create(
                group_id=group.id,
                author_id=user.id,
                draft=ItemDraft(
                    kind=ItemKind.TASK,
                    subject_id=subject.id,
                    category=TaskCategory.LAB,
                    title="Лаба",
                    due_at=NOW + timedelta(days=2),
                ),
            )
            await GroupManager(session).update_settings(group.id, chat_id=-100)
            await session.commit()

        bot = FakeBot()
        assert await DigestService(factory, bot).send_for_group(group.id, now=NOW)
        assert [chat for chat, _ in bot.sent] == [-100]
        assert "Лаба" in bot.sent[0][1]

    async def test_falls_back_to_direct_messages(self, factory):
        async with factory() as session:
            user, group, subject = await _seed(session)
            await ItemManager(session).create(
                group_id=group.id,
                author_id=user.id,
                draft=ItemDraft(
                    kind=ItemKind.TASK,
                    subject_id=subject.id,
                    category=TaskCategory.LAB,
                    title="Лаба",
                    due_at=NOW + timedelta(days=2),
                ),
            )
            await session.commit()

        bot = FakeBot()
        await DigestService(factory, bot).send_for_group(group.id, now=NOW)
        assert [chat for chat, _ in bot.sent] == [user.tg_user_id]

    async def test_empty_group_sends_nothing(self, factory):
        async with factory() as session:
            await _seed(session)
            group_id = 1
            await session.commit()
        bot = FakeBot()
        assert not await DigestService(factory, bot).send_for_group(group_id, now=NOW)
        assert bot.sent == []

    async def test_tick_marks_slot_even_when_empty(self, factory):
        async with factory() as session:
            _, group, _ = await _seed(session)
            await session.commit()
        bot = FakeBot()
        service = DigestService(factory, bot)
        await service.tick(now=NOW)
        async with factory() as session:
            refreshed = await GroupManager(session).get_group(group.id)
        assert refreshed.digest_last_sent_at is not None
        assert not is_digest_due(refreshed, NOW)

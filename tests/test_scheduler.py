from datetime import UTC, datetime, timedelta

import pytest

from src import scheduler
from src.db.session import SessionLocal
from src.domain.enums import ItemKind, TaskCategory
from src.domain.models import ItemDraft
from src.services.groups import GroupManager
from src.services.items import ItemManager
from tests.fake_telegram import make_bot

pytestmark = pytest.mark.usefixtures("app_db")


@pytest.fixture
async def running_scheduler():
    """Поднимает планировщик на боте-заглушке и гасит его после теста."""
    bot, session = make_bot()
    await scheduler.start_scheduler(bot)
    yield session
    await scheduler.shutdown_scheduler()


async def _seed_item(*, due_in: timedelta, offsets=None):
    async with SessionLocal() as db, db.begin():
        groups = GroupManager(db)
        user = await groups._users.get_or_create(tg_user_id=1, tg_username=None, full_name="Аня")
        group = await groups.create_group(creator=user, name="кабаны")
        subject, _ = await groups.add_subject(group_id=group.id, name="Матан")
        item = await ItemManager(db).create(
            group_id=group.id,
            author_id=user.id,
            draft=ItemDraft(
                kind=ItemKind.TASK,
                subject_id=subject.id,
                category=TaskCategory.LAB,
                title="Лаба",
                due_at=datetime.now(tz=UTC) + due_in,
                reminder_offsets=offsets,
            ),
        )
    return group, item


class TestScheduling:
    async def test_future_reminders_get_jobs(self, running_scheduler):
        _, item = await _seed_item(due_in=timedelta(days=3), offsets=[120])
        group = await _load_group(item.group_id)
        scheduler.schedule_item(item, group)
        ids = [job.id for job in scheduler.get_scheduler().get_jobs()]
        assert f"rem:{item.id}:120" in ids

    async def test_rescheduling_replaces_old_jobs(self, running_scheduler):
        _, item = await _seed_item(due_in=timedelta(days=3), offsets=[120])
        group = await _load_group(item.group_id)
        scheduler.schedule_item(item, group)
        updated = item.model_copy(update={"reminder_offsets": [60]})
        scheduler.schedule_item(updated, group)
        ids = [job.id for job in scheduler.get_scheduler().get_jobs()]
        assert f"rem:{item.id}:60" in ids
        assert f"rem:{item.id}:120" not in ids

    async def test_cancel_removes_all_jobs_of_item(self, running_scheduler):
        _, item = await _seed_item(due_in=timedelta(days=8), offsets=[10080, 120])
        group = await _load_group(item.group_id)
        scheduler.schedule_item(item, group)
        scheduler.cancel_item(item.id)
        ids = [job.id for job in scheduler.get_scheduler().get_jobs()]
        assert not any(i.startswith(f"rem:{item.id}:") for i in ids)

    async def test_muted_item_gets_no_jobs(self, running_scheduler):
        _, item = await _seed_item(due_in=timedelta(days=3), offsets=[120])
        group = await _load_group(item.group_id)
        scheduler.schedule_item(item.model_copy(update={"reminders_muted": True}), group)
        ids = [job.id for job in scheduler.get_scheduler().get_jobs()]
        assert not any(i.startswith(f"rem:{item.id}:") for i in ids)

    async def test_bootstrap_recovers_jobs_after_restart(self, running_scheduler):
        _, item = await _seed_item(due_in=timedelta(days=3), offsets=[120])
        scheduler.cancel_item(item.id)
        await scheduler.bootstrap_from_db()
        ids = [job.id for job in scheduler.get_scheduler().get_jobs()]
        assert f"rem:{item.id}:120" in ids

    async def test_bootstrap_sends_missed_reminder_once(self, running_scheduler):
        # напоминание за 2 часа при дедлайне через час — момент уже прошёл,
        # но до дедлайна больше 30 минут, значит его нужно догнать
        await _seed_item(due_in=timedelta(hours=1), offsets=[120])
        running_scheduler.clear()
        await scheduler.bootstrap_from_db()
        assert len(running_scheduler.texts) == 1
        assert "Лаба" in running_scheduler.texts[0]

        running_scheduler.clear()
        await scheduler.bootstrap_from_db()
        assert running_scheduler.texts == []

    async def test_missed_reminder_skipped_right_before_deadline(self, running_scheduler):
        await _seed_item(due_in=timedelta(minutes=10), offsets=[120])
        running_scheduler.clear()
        await scheduler.bootstrap_from_db()
        assert running_scheduler.texts == []


async def _load_group(group_id: int):
    async with SessionLocal() as db:
        return await GroupManager(db).get_group(group_id)

from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import pytest

from src.domain.enums import ItemKind
from src.domain.models import GroupReminderSettings
from src.domain.reminders import (
    offset_label,
    offsets_label,
    plan_reminders,
    resolve_offsets,
)

VILNIUS = ZoneInfo("Europe/Vilnius")


def _settings(**kwargs) -> GroupReminderSettings:
    return GroupReminderSettings(**kwargs)


class TestResolveOffsets:
    def test_uses_group_defaults_when_item_has_none(self):
        result = resolve_offsets(
            kind=ItemKind.EVENT, item_offsets=None, muted=False, settings=_settings()
        )
        assert result == [10080, 4320, 1440]

    def test_task_and_event_defaults_differ(self):
        settings = _settings(task_offsets=[60], event_offsets=[1440])
        assert resolve_offsets(
            kind=ItemKind.TASK, item_offsets=None, muted=False, settings=settings
        ) == [60]
        assert resolve_offsets(
            kind=ItemKind.EVENT, item_offsets=None, muted=False, settings=settings
        ) == [1440]

    def test_item_override_wins(self):
        result = resolve_offsets(
            kind=ItemKind.EVENT, item_offsets=[120], muted=False, settings=_settings()
        )
        assert result == [120]

    def test_empty_override_means_no_reminders(self):
        result = resolve_offsets(
            kind=ItemKind.TASK, item_offsets=[], muted=False, settings=_settings()
        )
        assert result == []

    def test_muted_beats_everything(self):
        result = resolve_offsets(
            kind=ItemKind.TASK, item_offsets=[1440], muted=True, settings=_settings()
        )
        assert result == []

    def test_deduplicates_and_sorts_descending(self):
        result = resolve_offsets(
            kind=ItemKind.TASK,
            item_offsets=[60, 1440, 60, 0, -5],
            muted=False,
            settings=_settings(),
        )
        assert result == [1440, 60]


class TestPlanReminders:
    def test_day_offsets_fire_at_group_reminder_hour(self):
        due = datetime(2026, 6, 20, 23, 59, tzinfo=VILNIUS).astimezone(UTC)
        planned = plan_reminders(due_at=due, offsets=[4320, 1440], tz=VILNIUS, reminder_hour=10)
        local = [p.run_at.astimezone(VILNIUS) for p in planned]
        assert local[0] == datetime(2026, 6, 17, 10, 0, tzinfo=VILNIUS)
        assert local[1] == datetime(2026, 6, 19, 10, 0, tzinfo=VILNIUS)

    def test_sub_day_offsets_are_literal(self):
        due = datetime(2026, 6, 20, 18, 0, tzinfo=VILNIUS).astimezone(UTC)
        planned = plan_reminders(due_at=due, offsets=[120], tz=VILNIUS, reminder_hour=10)
        assert planned[0].run_at.astimezone(VILNIUS) == datetime(2026, 6, 20, 16, 0, tzinfo=VILNIUS)

    def test_reminder_not_after_deadline(self):
        due = datetime(2026, 6, 20, 8, 0, tzinfo=VILNIUS).astimezone(UTC)
        planned = plan_reminders(due_at=due, offsets=[1440], tz=VILNIUS, reminder_hour=10)
        assert planned[0].run_at.astimezone(VILNIUS) == datetime(2026, 6, 19, 10, 0, tzinfo=VILNIUS)

    def test_same_day_offset_dropped_when_it_lands_past_deadline(self):
        due = datetime(2026, 6, 20, 9, 0, tzinfo=VILNIUS).astimezone(UTC)
        planned = plan_reminders(due_at=due, offsets=[30], tz=VILNIUS, reminder_hour=10)
        assert [p.offset_minutes for p in planned] == [30]
        planned_late = plan_reminders(due_at=due, offsets=[0], tz=VILNIUS, reminder_hour=10)
        assert planned_late == []

    def test_open_deadline_plans_nothing(self):
        assert plan_reminders(due_at=None, offsets=[1440], tz=VILNIUS, reminder_hour=10) == []

    def test_no_offsets_plans_nothing(self):
        due = datetime(2026, 6, 20, 12, 0, tzinfo=UTC)
        assert plan_reminders(due_at=due, offsets=[], tz=VILNIUS, reminder_hour=10) == []

    def test_naive_deadline_treated_as_utc(self):
        naive = datetime(2026, 6, 20, 12, 0)
        aware = datetime(2026, 6, 20, 12, 0, tzinfo=UTC)
        assert plan_reminders(
            due_at=naive, offsets=[120], tz=VILNIUS, reminder_hour=10
        ) == plan_reminders(due_at=aware, offsets=[120], tz=VILNIUS, reminder_hour=10)

    def test_dst_spring_forward_keeps_local_reminder_hour(self):
        # Литва переходит на летнее время в ночь на 29.03.2026
        due = datetime(2026, 3, 31, 12, 0, tzinfo=VILNIUS).astimezone(UTC)
        planned = plan_reminders(due_at=due, offsets=[4320], tz=VILNIUS, reminder_hour=10)
        run_local = planned[0].run_at.astimezone(VILNIUS)
        assert run_local == datetime(2026, 3, 28, 10, 0, tzinfo=VILNIUS)
        assert run_local.hour == 10

    def test_results_sorted_from_earliest_offset_to_latest(self):
        due = datetime(2026, 6, 20, 12, 0, tzinfo=VILNIUS).astimezone(UTC)
        planned = plan_reminders(
            due_at=due, offsets=[1440, 10080, 120], tz=VILNIUS, reminder_hour=10
        )
        assert [p.offset_minutes for p in planned] == [10080, 1440, 120]
        assert planned[0].run_at < planned[1].run_at < planned[2].run_at


class TestLabels:
    @pytest.mark.parametrize(
        ("minutes", "expected"),
        [
            (1440, "за день"),
            (10080, "за неделю"),
            (120, "за 2 часа"),
            (5760, "за 4 дня"),
            (300, "за 5 часов"),
            (45, "за 45 минут"),
        ],
    )
    def test_offset_label(self, minutes: int, expected: str):
        assert offset_label(minutes) == expected

    def test_offsets_label_sorted_descending(self):
        assert offsets_label([1440, 10080]) == "за неделю, за день"

    def test_empty_offsets_label(self):
        assert offsets_label([]) == "выключены"

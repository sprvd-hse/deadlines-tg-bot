from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from src.domain.enums import EventCategory, ItemKind, TaskCategory, category_label
from src.domain.models import GroupReminderSettings, Item, ItemDraft

NOW = datetime(2026, 6, 1, tzinfo=UTC)


def _item(**overrides) -> Item:
    data = {
        "id": 1,
        "group_id": 1,
        "subject_id": 1,
        "short_id": 1,
        "kind": ItemKind.TASK,
        "category": TaskCategory.HOMEWORK,
        "title": "Лаба 3",
        "created_by_user_id": 1,
        "created_at": NOW,
    }
    return Item(**(data | overrides))


class TestItem:
    def test_task_may_have_no_deadline(self):
        assert _item().is_open

    def test_event_requires_deadline(self):
        with pytest.raises(ValidationError):
            _item(kind=ItemKind.EVENT, category=EventCategory.EXAM, due_at=None)

    def test_event_with_deadline_is_valid(self):
        item = _item(kind=ItemKind.EVENT, category=EventCategory.EXAM, due_at=NOW)
        assert not item.is_open

    def test_category_must_match_kind(self):
        with pytest.raises(ValidationError):
            _item(kind=ItemKind.TASK, category=EventCategory.EXAM)
        with pytest.raises(ValidationError):
            _item(kind=ItemKind.EVENT, category=TaskCategory.HOMEWORK, due_at=NOW)

    def test_title_length_limited(self):
        with pytest.raises(ValidationError):
            _item(title="x" * 257)

    def test_frozen(self):
        with pytest.raises(ValidationError):
            _item().title = "другое"


class TestItemDraft:
    def test_event_draft_requires_due_at(self):
        with pytest.raises(ValidationError):
            ItemDraft(
                kind=ItemKind.EVENT, subject_id=1, category=EventCategory.EXAM, title="Экзамен"
            )

    def test_task_draft_without_due_at(self):
        draft = ItemDraft(kind=ItemKind.TASK, subject_id=1, category=TaskCategory.LAB, title="Лаба")
        assert draft.due_at is None
        assert draft.links == []


class TestGroupReminderSettings:
    def test_defaults(self):
        settings = GroupReminderSettings()
        assert settings.defaults_for(ItemKind.TASK) == [1440]
        assert settings.defaults_for(ItemKind.EVENT) == [10080, 4320, 1440]
        assert settings.reminder_hour == 10

    def test_reminder_hour_bounds(self):
        with pytest.raises(ValidationError):
            GroupReminderSettings(reminder_hour=24)


class TestCategoryLabels:
    def test_labels_resolve_per_kind(self):
        assert category_label(ItemKind.TASK, TaskCategory.HOMEWORK) == "Домашка"
        assert category_label(ItemKind.EVENT, EventCategory.EXAM) == "Экзамен"

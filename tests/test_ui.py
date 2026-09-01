from datetime import date

import pytest

from src.domain.enums import ItemKind, TaskCategory
from src.domain.models import Group, Item
from src.ui import keyboards as kb
from src.ui.callbacks import NOOP, AddStep, CalendarNav, ItemAction, Nav, OffsetToggle

TODAY = date(2026, 6, 15)
GROUP = Group(
    id=1,
    name="г",
    invite_code="AAAA-BBBB-CCCC",
    timezone="Europe/Vilnius",
    digest_day=6,
    digest_hour=19,
    created_by_user_id=1,
)


def _buttons(markup):
    return [b for row in markup.inline_keyboard for b in row]


def _item(**overrides) -> Item:
    from datetime import UTC, datetime

    data = {
        "id": 1,
        "group_id": 1,
        "subject_id": 1,
        "short_id": 1,
        "kind": ItemKind.TASK,
        "category": TaskCategory.LAB,
        "title": "Лаба",
        "created_by_user_id": 1,
        "created_at": datetime(2026, 6, 1, tzinfo=UTC),
    }
    return Item(**(data | overrides))


class TestCallbackRoundTrip:
    def test_nav_packs_and_unpacks(self):
        assert Nav.unpack(Nav(to="tasks").pack()).to == "tasks"

    def test_item_action_keeps_id(self):
        parsed = ItemAction.unpack(ItemAction(action="open", item_id=42).pack())
        assert (parsed.action, parsed.item_id) == ("open", 42)

    def test_offset_toggle_keeps_minutes(self):
        packed = OffsetToggle(scope="item", target_id=7, action="toggle", minutes=1440).pack()
        parsed = OffsetToggle.unpack(packed)
        assert (parsed.scope, parsed.target_id, parsed.minutes) == ("item", 7, 1440)

    def test_callback_data_fits_telegram_limit(self):
        packed = CalendarNav(action="pick", year=2026, month=12, day=31).pack()
        assert len(packed.encode()) <= 64


class TestCalendarKeyboard:
    def _markup(self, year=2026, month=6, kind=ItemKind.TASK, allow_open=True):
        return kb.calendar_keyboard(
            year=year, month=month, today=TODAY, kind=kind, allow_open=allow_open
        )

    def test_past_days_are_not_clickable(self):
        buttons = _buttons(self._markup())
        picks = [b for b in buttons if b.callback_data.startswith("cal:pick")]
        days = {CalendarNav.unpack(b.callback_data).day for b in picks}
        assert min(days) == TODAY.day

    def test_past_days_are_inert(self):
        buttons = _buttons(self._markup())
        inert = [b for b in buttons if b.text == "·"]
        assert inert and all(b.callback_data == NOOP for b in inert)

    def test_today_is_marked(self):
        labels = [b.text for b in _buttons(self._markup())]
        assert "·15·" in labels

    def test_back_arrow_disabled_in_current_month(self):
        buttons = _buttons(self._markup())
        assert not any(b.text == "‹" and b.callback_data != NOOP for b in buttons)

    def test_back_arrow_enabled_in_later_month(self):
        arrow = next(b for b in _buttons(self._markup(month=8)) if b.text == "‹")
        assert arrow.callback_data is not None

    def test_forward_limited_two_years_ahead(self):
        buttons = _buttons(self._markup(year=2028, month=6))
        assert not any(b.text == "›" and b.callback_data != NOOP for b in buttons)

    def test_quick_buttons_present(self):
        labels = [b.text for b in _buttons(self._markup())]
        assert {"Сегодня", "Завтра", "+7 дней"} <= set(labels)

    def test_open_deadline_only_for_tasks(self):
        task_labels = [b.text for b in _buttons(self._markup())]
        event_labels = [b.text for b in _buttons(self._markup(kind=ItemKind.EVENT))]
        assert any("До конца модуля" in label for label in task_labels)
        assert not any("До конца модуля" in label for label in event_labels)

    def test_weekday_header_row(self):
        labels = [b.text for b in _buttons(self._markup())]
        assert labels[3:10] == ["пн", "вт", "ср", "чт", "пт", "сб", "вс"]


class TestTimeKeyboard:
    def test_task_presets_include_end_of_day(self):
        labels = [b.text for b in _buttons(kb.time_keyboard(ItemKind.TASK))]
        assert "23:59" in labels

    def test_custom_time_offered(self):
        labels = [b.text for b in _buttons(kb.time_keyboard(ItemKind.EVENT))]
        assert "Другое время" in labels


class TestOffsetsKeyboard:
    def test_selected_offsets_marked(self):
        markup = kb.offsets_keyboard(scope="item", target_id=1, selected={1440}, allow_reset=False)
        marked = [
            b.text
            for b in _buttons(markup)
            if b.text.startswith("✅")
            and b.callback_data.startswith("off:")
            and "toggle" in b.callback_data
        ]
        assert marked == ["✅ за день"]

    def test_reset_button_optional(self):
        without = [
            b.text
            for b in _buttons(
                kb.offsets_keyboard(
                    scope="group_task", target_id=0, selected=set(), allow_reset=False
                )
            )
        ]
        assert not any("Как в группе" in label for label in without)


class TestItemKeyboards:
    def test_card_hides_actions_without_rights(self):
        labels = [b.text for b in _buttons(kb.card_keyboard(_item(), can_modify=False))]
        assert labels == ["✅ Выполнено", "📋 К списку"]

    def test_card_toggle_label_reflects_state(self):
        labels = [
            b.text for b in _buttons(kb.card_keyboard(_item(), can_modify=False, completed=True))
        ]
        assert "↩️ Вернуть в работу" in labels

    def test_close_button_only_for_open_task(self):
        with_deadline = _item(due_at=None)
        labels = [b.text for b in _buttons(kb.card_keyboard(with_deadline, can_modify=True))]
        assert any("Закрыть" in label for label in labels)

    def test_list_buttons_match_numbering(self):
        tasks = [_item(id=1, title="A"), _item(id=2, title="B")]
        markup = kb.list_keyboard(tasks=tasks, events=[], numbers={1: 1, 2: 2})
        labels = [b.text for b in _buttons(markup)]
        assert labels[:4] == ["☐", "1. 📝 A", "☐", "2. 📝 B"]

    def test_completed_item_shows_check(self):
        tasks = [_item(id=1, title="A")]
        markup = kb.list_keyboard(
            tasks=tasks, events=[], numbers={1: 1}, completed_ids=frozenset({1})
        )
        assert _buttons(markup)[0].text == "✅"

    def test_kind_keyboard_offers_both_types(self):
        values = [
            AddStep.unpack(b.callback_data).value
            for b in _buttons(kb.kind_keyboard())
            if b.callback_data.startswith("add:kind")
        ]
        assert values == [ItemKind.TASK, ItemKind.EVENT]

    @pytest.mark.parametrize("kind", [ItemKind.TASK, ItemKind.EVENT])
    def test_categories_keyboard_matches_kind(self, kind):
        labels = [b.text for b in _buttons(kb.categories_keyboard(kind))]
        assert ("Домашка" in labels) is (kind is ItemKind.TASK)
        assert ("Экзамен" in labels) is (kind is ItemKind.EVENT)

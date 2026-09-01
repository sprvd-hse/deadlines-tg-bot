from datetime import UTC, datetime, timedelta

from src.domain.enums import EventCategory, ItemKind, TaskCategory
from src.domain.models import Group, Item
from src.views.common import SAFE_LIMIT, esc, split_messages
from src.views.items import EMPTY_LIST, render_card, render_digest, render_list, render_passed
from src.views.reminders import render_reminder

NOW = datetime(2026, 6, 15, 9, 0, tzinfo=UTC)

GROUP = Group(
    id=1,
    name="кабаньи дедлайны",
    invite_code="AAAA-BBBB-CCCC",
    timezone="Europe/Vilnius",
    digest_day=6,
    digest_hour=19,
    created_by_user_id=1,
)
SUBJECTS = {1: "Микроконтроллерные системы", 2: "Правовая грамотность"}


def _item(**overrides) -> Item:
    data = {
        "id": 1,
        "group_id": 1,
        "subject_id": 1,
        "short_id": 1,
        "kind": ItemKind.TASK,
        "category": TaskCategory.LAB,
        "title": "Защитить ДЗ",
        "created_by_user_id": 1,
        "created_at": NOW,
    }
    return Item(**(data | overrides))


class TestEscaping:
    def test_html_specials_escaped(self):
        assert esc("<b>&</b>") == "&lt;b&gt;&amp;&lt;/b&gt;"

    def test_title_with_angle_brackets_does_not_break_card(self):
        text = render_card(
            item=_item(title="a < b & c"), subject_name="Матан", group=GROUP, now=NOW
        )
        assert "a &lt; b &amp; c" in text
        assert "<b>a < b" not in text


class TestList:
    def test_empty_list_message(self):
        assert (
            render_list(tasks=[], events=[], subject_names=SUBJECTS, group=GROUP, now=NOW)
            == EMPTY_LIST
        )

    def test_sections_are_separate(self):
        tasks = [_item(id=1, title="Лаба")]
        events = [
            _item(
                id=2,
                subject_id=2,
                kind=ItemKind.EVENT,
                category=EventCategory.EXAM,
                title="Экзамен",
                due_at=NOW + timedelta(days=3),
            )
        ]
        text = render_list(tasks=tasks, events=events, subject_names=SUBJECTS, group=GROUP, now=NOW)
        assert "📝 Задачи" in text
        assert "📅 Важные даты" in text
        assert text.index("Лаба") < text.index("Экзамен")

    def test_numbering_is_continuous_across_sections(self):
        tasks = [_item(id=1, title="A"), _item(id=2, title="B")]
        events = [
            _item(
                id=3,
                kind=ItemKind.EVENT,
                category=EventCategory.EXAM,
                title="C",
                due_at=NOW + timedelta(days=1),
            )
        ]
        text = render_list(tasks=tasks, events=events, subject_names=SUBJECTS, group=GROUP, now=NOW)
        assert "1. A" in text and "2. B" in text and "3. C" in text

    def test_open_task_shows_module_label(self):
        text = render_list(
            tasks=[_item(due_at=None)], events=[], subject_names=SUBJECTS, group=GROUP, now=NOW
        )
        assert "до конца модуля" in text

    def test_passed_screen_lists_newest_first_input_order(self):
        items = [
            _item(id=1, title="Недавнее", due_at=NOW - timedelta(days=1)),
            _item(id=2, title="Старое", due_at=NOW - timedelta(days=5)),
        ]
        text = render_passed(items=items, subject_names=SUBJECTS, group=GROUP, now=NOW)
        assert text.index("Недавнее") < text.index("Старое")

    def test_passed_empty(self):
        assert "Прошедших" in render_passed(items=[], subject_names=SUBJECTS, group=GROUP, now=NOW)


class TestCard:
    def test_card_shows_relative_deadline_and_reminders(self):
        item = _item(due_at=NOW + timedelta(days=3))
        text = render_card(item=item, subject_name="Матан", group=GROUP, now=NOW)
        assert "через 3 дня" in text
        assert "как в группе" in text

    def test_card_reports_custom_offsets(self):
        item = _item(due_at=NOW + timedelta(days=3), reminder_offsets=[120])
        text = render_card(item=item, subject_name="Матан", group=GROUP, now=NOW)
        assert "Напоминания: за 2 часа" in text

    def test_card_reports_muted(self):
        item = _item(due_at=NOW + timedelta(days=3), reminders_muted=True)
        text = render_card(item=item, subject_name="Матан", group=GROUP, now=NOW)
        assert "выключены" in text

    def test_links_rendered_as_anchors(self):
        item = _item(links=["https://example.com/a"])
        text = render_card(item=item, subject_name="Матан", group=GROUP, now=NOW)
        assert '<a href="https://example.com/a">' in text


class TestReminderText:
    def test_task_wording(self):
        item = _item(due_at=NOW + timedelta(days=1))
        text = render_reminder(
            item=item, subject_name="Матан", group=GROUP, offset_minutes=1440, now=NOW
        )
        assert "нужно сдать" in text
        assert "За день" in text

    def test_event_wording(self):
        item = _item(
            kind=ItemKind.EVENT,
            category=EventCategory.EXAM,
            title="Экзамен",
            due_at=NOW + timedelta(days=7),
        )
        text = render_reminder(
            item=item, subject_name="Право", group=GROUP, offset_minutes=10080, now=NOW
        )
        assert "состоится" in text
        assert "За неделю" in text

    def test_group_name_included(self):
        item = _item(due_at=NOW + timedelta(days=1))
        text = render_reminder(
            item=item, subject_name="Матан", group=GROUP, offset_minutes=1440, now=NOW
        )
        assert "кабаньи дедлайны" in text


class TestDigest:
    def test_groups_by_day_with_weekday(self):
        items = [_item(id=1, due_at=datetime(2026, 6, 20, 20, 59, tzinfo=UTC))]
        blocks = render_digest(
            items=items, subject_names=SUBJECTS, group=GROUP, now=NOW, header="Дайджест"
        )
        assert any("20.06 (сб)" in b for b in blocks)

    def test_open_items_go_to_own_block(self):
        items = [_item(id=1, due_at=None)]
        blocks = render_digest(
            items=items, subject_names=SUBJECTS, group=GROUP, now=NOW, header="Дайджест"
        )
        assert any("До конца модуля" in b for b in blocks)

    def test_no_items_returns_nothing(self):
        assert (
            render_digest(items=[], subject_names=SUBJECTS, group=GROUP, now=NOW, header="Дайджест")
            == []
        )

    def test_numbering_continues_into_open_block(self):
        items = [
            _item(id=1, due_at=datetime(2026, 6, 20, 20, 59, tzinfo=UTC)),
            _item(id=2, title="Без срока", due_at=None),
        ]
        blocks = render_digest(
            items=items, subject_names=SUBJECTS, group=GROUP, now=NOW, header="Дайджест"
        )
        assert "2. 📝 Без срока" in blocks[-1]


class TestSplitMessages:
    def test_short_blocks_stay_together(self):
        assert split_messages(["a", "b"]) == ["a\n\nb"]

    def test_blocks_split_when_over_limit(self):
        blocks = ["x" * 3000, "y" * 3000]
        messages = split_messages(blocks)
        assert len(messages) == 2

    def test_every_message_fits_telegram_limit(self):
        block = "\n".join("строка " * 20 for _ in range(400))
        messages = split_messages([block])
        assert messages
        assert all(len(m) <= SAFE_LIMIT for m in messages)

    def test_single_oversized_line_is_truncated_not_dropped(self):
        messages = split_messages(["z" * 9000])
        assert messages
        assert all(len(m) <= SAFE_LIMIT for m in messages)

    def test_empty_blocks_skipped(self):
        assert split_messages(["", "a", ""]) == ["a"]

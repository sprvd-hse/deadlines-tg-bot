from datetime import UTC, date, datetime, time
from zoneinfo import ZoneInfo

import pytest

from src.domain.calendar import (
    TimeParseError,
    can_go_back,
    can_go_forward,
    combine_to_utc,
    is_selectable,
    month_grid,
    month_title,
    parse_time,
    shift_month,
    time_presets_for,
    today_in,
)
from src.domain.enums import ItemKind

VILNIUS = ZoneInfo("Europe/Vilnius")


class TestMonthGrid:
    def test_weeks_start_on_monday(self):
        grid = month_grid(2026, 6)
        assert grid[0][0] == date(2026, 6, 1)

    def test_leading_and_trailing_cells_are_none(self):
        grid = month_grid(2026, 9)
        assert grid[0][:1] == [None]
        assert grid[0][1] == date(2026, 9, 1)
        assert grid[-1][-1] is None

    def test_every_row_has_seven_cells(self):
        assert all(len(week) == 7 for week in month_grid(2026, 2))

    def test_all_days_present_exactly_once(self):
        days = [d for week in month_grid(2026, 7) for d in week if d]
        assert len(days) == 31
        assert days[0] == date(2026, 7, 1)
        assert days[-1] == date(2026, 7, 31)

    def test_leap_february(self):
        days = [d for week in month_grid(2028, 2) for d in week if d]
        assert days[-1] == date(2028, 2, 29)


class TestNavigation:
    @pytest.mark.parametrize(
        ("year", "month", "delta", "expected"),
        [
            (2026, 6, 1, (2026, 7)),
            (2026, 12, 1, (2027, 1)),
            (2026, 1, -1, (2025, 12)),
            (2026, 6, 24, (2028, 6)),
        ],
    )
    def test_shift_month(self, year, month, delta, expected):
        assert shift_month(year, month, delta) == expected

    def test_cannot_go_back_before_current_month(self):
        today = date(2026, 6, 15)
        assert not can_go_back(2026, 6, today)
        assert can_go_back(2026, 7, today)

    def test_forward_limited_to_two_years(self):
        today = date(2026, 6, 15)
        assert can_go_forward(2027, 6, today)
        assert not can_go_forward(2028, 6, today)

    def test_past_days_not_selectable(self):
        today = date(2026, 6, 15)
        assert not is_selectable(date(2026, 6, 14), today)
        assert is_selectable(today, today)
        assert is_selectable(date(2026, 6, 16), today)

    def test_month_title_in_russian(self):
        assert month_title(2026, 12) == "Декабрь 2026"

    def test_today_in_group_timezone(self):
        # 22:30 UTC — в Вильнюсе уже следующий день
        now = datetime(2026, 6, 15, 22, 30, tzinfo=UTC)
        assert today_in(VILNIUS, now) == date(2026, 6, 16)


class TestTime:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("18:30", time(18, 30)),
            ("9:05", time(9, 5)),
            ("18.30", time(18, 30)),
            (" 7:00 ", time(7, 0)),
        ],
    )
    def test_parse_time(self, raw, expected):
        assert parse_time(raw) == expected

    @pytest.mark.parametrize("raw", ["25:00", "12:75", "полдень", "1830", "12:3", ""])
    def test_parse_time_rejects_garbage(self, raw):
        with pytest.raises(TimeParseError):
            parse_time(raw)

    def test_presets_differ_by_kind(self):
        assert time(23, 59) in time_presets_for(ItemKind.TASK)
        assert time(23, 59) not in time_presets_for(ItemKind.EVENT)

    def test_combine_to_utc_uses_group_timezone(self):
        result = combine_to_utc(date(2026, 6, 20), time(23, 59), VILNIUS)
        assert result == datetime(2026, 6, 20, 20, 59, tzinfo=UTC)
        assert result.tzinfo is UTC

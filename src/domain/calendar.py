import calendar
import re
from datetime import UTC, date, datetime, time
from zoneinfo import ZoneInfo

from src.domain.enums import ItemKind
from src.domain.humanize import MONTHS_RU

MAX_MONTHS_AHEAD = 24

TASK_TIME_PRESETS: tuple[time, ...] = (
    time(9, 0),
    time(12, 0),
    time(18, 0),
    time(23, 59),
)
EVENT_TIME_PRESETS: tuple[time, ...] = (
    time(9, 0),
    time(10, 30),
    time(12, 0),
    time(14, 0),
)

_HHMM_RE = re.compile(r"^(\d{1,2})[:.\s](\d{2})$")


class TimeParseError(ValueError):
    """Не удалось разобрать время в формате ЧЧ:ММ."""


def time_presets_for(kind: ItemKind) -> tuple[time, ...]:
    """Возвращает пресеты времени для типа записи."""
    return TASK_TIME_PRESETS if kind is ItemKind.TASK else EVENT_TIME_PRESETS


def month_grid(year: int, month: int) -> list[list[date | None]]:
    """Возвращает сетку месяца по неделям (пн—вс); пустые ячейки — None."""
    weeks = calendar.Calendar(firstweekday=0).monthdayscalendar(year, month)
    return [[date(year, month, day) if day else None for day in week] for week in weeks]


def month_title(year: int, month: int) -> str:
    """Заголовок календаря: «Декабрь 2026»."""
    return f"{MONTHS_RU[month - 1]} {year}"


def shift_month(year: int, month: int, delta: int) -> tuple[int, int]:
    """Сдвигает пару (год, месяц) на delta месяцев."""
    index = year * 12 + (month - 1) + delta
    return index // 12, index % 12 + 1


def today_in(tz: ZoneInfo, now: datetime | None = None) -> date:
    """Текущая дата в часовом поясе группы."""
    return (now.astimezone(tz) if now else datetime.now(tz=tz)).date()


def is_selectable(day: date, today: date) -> bool:
    """Можно ли выбрать этот день (прошлое недоступно)."""
    return day >= today


def can_go_back(year: int, month: int, today: date) -> bool:
    """Есть ли смысл листать календарь назад."""
    return (year, month) > (today.year, today.month)


def can_go_forward(year: int, month: int, today: date) -> bool:
    """Есть ли смысл листать календарь вперёд."""
    limit_year, limit_month = shift_month(today.year, today.month, MAX_MONTHS_AHEAD)
    return (year, month) < (limit_year, limit_month)


def parse_time(raw: str) -> time:
    """Разбирает время из текста «18:30», «18.30», «9:05»."""
    match = _HHMM_RE.match(raw.strip())
    if match is None:
        raise TimeParseError("нужен формат ЧЧ:ММ, например 18:30")
    hour, minute = int(match.group(1)), int(match.group(2))
    if hour > 23 or minute > 59:
        raise TimeParseError("часы 0–23, минуты 0–59")
    return time(hour, minute)


def combine_to_utc(day: date, moment: time, tz: ZoneInfo) -> datetime:
    """Собирает локальные дату и время в момент UTC."""
    return datetime.combine(day, moment, tzinfo=tz).astimezone(UTC)

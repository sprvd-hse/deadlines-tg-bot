import re
from datetime import UTC, datetime, time, timedelta
from zoneinfo import ZoneInfo


class DeadlineParseError(ValueError):
    pass


_RE_TODAY = re.compile(r"^сегодня\s+(\d{1,2}):(\d{2})$")
_RE_TOMORROW = re.compile(r"^завтра\s+(\d{1,2}):(\d{2})$")
_RE_DDMM = re.compile(r"^(\d{1,2})\.(\d{1,2})\s+(\d{1,2}):(\d{2})$")
_RE_DDMMYYYY = re.compile(r"^(\d{1,2})\.(\d{1,2})\.(\d{4})\s+(\d{1,2}):(\d{2})$")


def parse_deadline(text: str, tz_name: str, now: datetime | None = None) -> datetime:
    """Парсит дедлайн в TZ группы и возвращает aware UTC datetime."""
    tz = ZoneInfo(tz_name)
    now_local = (now or datetime.now(tz=UTC)).astimezone(tz)
    cleaned = text.strip().lower()

    if m := _RE_TODAY.match(cleaned):
        h, mn = _hm(m.group(1), m.group(2))
        result = now_local.replace(hour=h, minute=mn, second=0, microsecond=0)
        if result <= now_local:
            raise DeadlineParseError("указанное время уже прошло")
    elif m := _RE_TOMORROW.match(cleaned):
        h, mn = _hm(m.group(1), m.group(2))
        d = (now_local + timedelta(days=1)).date()
        result = datetime.combine(d, time(h, mn), tzinfo=tz)
    elif m := _RE_DDMMYYYY.match(cleaned):
        d, mo, y, h, mn = (int(m.group(i)) for i in range(1, 6))
        _validate_hm(h, mn)
        result = _safe_datetime(y, mo, d, h, mn, tz)
        if result <= now_local:
            raise DeadlineParseError("указанная дата уже прошла")
    elif m := _RE_DDMM.match(cleaned):
        d, mo, h, mn = (int(m.group(i)) for i in range(1, 5))
        _validate_hm(h, mn)
        result = _safe_datetime(now_local.year, mo, d, h, mn, tz)
        if result <= now_local:
            result = _safe_datetime(now_local.year + 1, mo, d, h, mn, tz)
    else:
        raise DeadlineParseError("формат не распознан")

    return result.astimezone(UTC)


def _hm(h_str: str, mn_str: str) -> tuple[int, int]:
    h, mn = int(h_str), int(mn_str)
    _validate_hm(h, mn)
    return h, mn


def _validate_hm(h: int, mn: int) -> None:
    if not (0 <= h <= 23 and 0 <= mn <= 59):
        raise DeadlineParseError("неверное время")


def _safe_datetime(y: int, mo: int, d: int, h: int, mn: int, tz: ZoneInfo) -> datetime:
    try:
        return datetime(y, mo, d, h, mn, tzinfo=tz)
    except ValueError as e:
        raise DeadlineParseError(f"неверная дата: {e}") from e

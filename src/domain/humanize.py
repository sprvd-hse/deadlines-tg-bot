from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

WEEKDAYS_RU: tuple[str, ...] = ("пн", "вт", "ср", "чт", "пт", "сб", "вс")
WEEKDAYS_RU_FULL: tuple[str, ...] = (
    "понедельник",
    "вторник",
    "среда",
    "четверг",
    "пятница",
    "суббота",
    "воскресенье",
)
MONTHS_RU: tuple[str, ...] = (
    "Январь",
    "Февраль",
    "Март",
    "Апрель",
    "Май",
    "Июнь",
    "Июль",
    "Август",
    "Сентябрь",
    "Октябрь",
    "Ноябрь",
    "Декабрь",
)

OPEN_DUE_LABEL = "до конца модуля"


def plural_ru(n: int, one: str, few: str, many: str) -> str:
    """Выбирает форму слова для числа по правилам русского языка."""
    n_abs = abs(n) % 100
    if 11 <= n_abs <= 14:
        return many
    last = n_abs % 10
    if last == 1:
        return one
    if 2 <= last <= 4:
        return few
    return many


def humanize_seconds(total_seconds: int) -> str:
    """Возвращает длительность словами: «3 дня», «5 часов», «12 минут»."""
    days = total_seconds // 86400
    if days >= 1:
        return f"{days} {plural_ru(days, 'день', 'дня', 'дней')}"
    hours = total_seconds // 3600
    if hours >= 1:
        return f"{hours} {plural_ru(hours, 'час', 'часа', 'часов')}"
    minutes = max(1, total_seconds // 60)
    return f"{minutes} {plural_ru(minutes, 'минуту', 'минуты', 'минут')}"


def humanize_delta(delta: timedelta) -> str:
    """Возвращает «через N» либо «просрочено на N»."""
    total = int(delta.total_seconds())
    if total < 0:
        return f"просрочено на {humanize_seconds(-total)}"
    return f"через {humanize_seconds(total)}"


def ensure_utc(dt: datetime) -> datetime:
    """SQLite через SQLAlchemy теряет tzinfo — naive значения трактуем как UTC."""
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=UTC)


def format_due_short(due_at: datetime | None, tz: ZoneInfo, now: datetime | None = None) -> str:
    """Короткая дата дедлайна: «15.12 23:59» (с годом, если он не текущий)."""
    if due_at is None:
        return OPEN_DUE_LABEL
    local = ensure_utc(due_at).astimezone(tz)
    now_local = (now or datetime.now(tz=UTC)).astimezone(tz)
    if local.year == now_local.year:
        return f"{local.day:02d}.{local.month:02d} {local.hour:02d}:{local.minute:02d}"
    return f"{local.day:02d}.{local.month:02d}.{local.year} {local.hour:02d}:{local.minute:02d}"


def format_due_full(due_at: datetime | None, tz: ZoneInfo, now: datetime | None = None) -> str:
    """Дата дедлайна с относительным сроком: «15.12 23:59 (через 3 дня)»."""
    if due_at is None:
        return OPEN_DUE_LABEL
    local = ensure_utc(due_at).astimezone(tz)
    now_local = (now or datetime.now(tz=UTC)).astimezone(tz)
    return f"{format_due_short(due_at, tz, now)} ({humanize_delta(local - now_local)})"

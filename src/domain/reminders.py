from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from src.domain.enums import ItemKind
from src.domain.humanize import plural_ru
from src.domain.models import GroupReminderSettings

MINUTES_PER_DAY = 1440


@dataclass(frozen=True, slots=True)
class OffsetPreset:
    """Вариант срока напоминания, доступный в настройках."""

    minutes: int
    label: str


OFFSET_PRESETS: tuple[OffsetPreset, ...] = (
    OffsetPreset(20160, "за 2 недели"),
    OffsetPreset(10080, "за неделю"),
    OffsetPreset(4320, "за 3 дня"),
    OffsetPreset(2880, "за 2 дня"),
    OffsetPreset(1440, "за день"),
    OffsetPreset(180, "за 3 часа"),
    OffsetPreset(120, "за 2 часа"),
    OffsetPreset(60, "за час"),
)

_PRESET_LABELS: dict[int, str] = {p.minutes: p.label for p in OFFSET_PRESETS}


@dataclass(frozen=True, slots=True)
class ScheduledReminder:
    """Запланированное напоминание: офсет и момент отправки в UTC."""

    offset_minutes: int
    run_at: datetime


def offset_label(minutes: int) -> str:
    """Возвращает человекочитаемую подпись срока напоминания."""
    known = _PRESET_LABELS.get(minutes)
    if known is not None:
        return known
    if minutes % MINUTES_PER_DAY == 0:
        days = minutes // MINUTES_PER_DAY
        return f"за {days} {plural_ru(days, 'день', 'дня', 'дней')}"
    if minutes % 60 == 0:
        hours = minutes // 60
        return f"за {hours} {plural_ru(hours, 'час', 'часа', 'часов')}"
    return f"за {minutes} {plural_ru(minutes, 'минуту', 'минуты', 'минут')}"


def offsets_label(offsets: list[int]) -> str:
    """Возвращает подпись набора офсетов или «выключены», если набор пуст."""
    if not offsets:
        return "выключены"
    return ", ".join(offset_label(m) for m in sorted(offsets, reverse=True))


def resolve_offsets(
    *,
    kind: ItemKind,
    item_offsets: list[int] | None,
    muted: bool,
    settings: GroupReminderSettings,
) -> list[int]:
    """Возвращает итоговый набор офсетов записи с учётом переопределения и заглушения."""
    if muted:
        return []
    raw = settings.defaults_for(kind) if item_offsets is None else item_offsets
    return sorted({m for m in raw if m > 0}, reverse=True)


def plan_reminders(
    *,
    due_at: datetime | None,
    offsets: list[int],
    tz: ZoneInfo,
    reminder_hour: int,
) -> list[ScheduledReminder]:
    """Считает моменты отправки напоминаний в UTC для заданного дедлайна.

    Офсеты, кратные суткам, срабатывают в `reminder_hour` по местному времени группы;
    более короткие — ровно за указанный интервал до дедлайна. Напоминания, попадающие
    на момент дедлайна или позже, отбрасываются.
    """
    if due_at is None or not offsets:
        return []
    due_utc = due_at if due_at.tzinfo is not None else due_at.replace(tzinfo=UTC)
    due_local = due_utc.astimezone(tz)

    planned: list[ScheduledReminder] = []
    for minutes in sorted(set(offsets), reverse=True):
        if minutes <= 0:
            continue
        if minutes % MINUTES_PER_DAY == 0:
            days = minutes // MINUTES_PER_DAY
            run_local = (due_local - timedelta(days=days)).replace(
                hour=reminder_hour, minute=0, second=0, microsecond=0
            )
        else:
            run_local = due_local - timedelta(minutes=minutes)
        run_utc = run_local.astimezone(UTC)
        if run_utc >= due_utc:
            continue
        planned.append(ScheduledReminder(offset_minutes=minutes, run_at=run_utc))
    return planned

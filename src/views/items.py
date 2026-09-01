from datetime import UTC, datetime

from src.domain.enums import KIND_ICONS, category_label
from src.domain.humanize import WEEKDAYS_RU, format_due_full, format_due_short
from src.domain.models import Group, Item, ItemDraft
from src.domain.reminders import offsets_label, resolve_offsets
from src.views.common import bold, esc, italic, join_blocks, link

LIST_HEADER_TASKS = "📝 Задачи"
LIST_HEADER_EVENTS = "📅 Важные даты"
EMPTY_LIST = "Пока пусто. Нажми «➕ Добавить», чтобы завести первую задачу или важную дату."
_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)

EMPTY_PASSED = "Прошедших записей нет."


def item_number_map(tasks: list[Item], events: list[Item]) -> dict[int, int]:
    """Сквозная нумерация записей: сначала задачи, затем важные даты."""
    return {item.id: index for index, item in enumerate(tasks + events, start=1)}


def render_list(
    *,
    tasks: list[Item],
    events: list[Item],
    subject_names: dict[int, str],
    group: Group,
    now: datetime,
) -> str:
    """Собирает текст списка: задачи и важные даты отдельными секциями."""
    numbers = item_number_map(tasks, events)
    sections = [
        _render_section(LIST_HEADER_TASKS, tasks, subject_names, group, now, numbers),
        _render_section(LIST_HEADER_EVENTS, events, subject_names, group, now, numbers),
    ]
    body = join_blocks(sections)
    return body or EMPTY_LIST


def render_passed(
    *, items: list[Item], subject_names: dict[int, str], group: Group, now: datetime
) -> str:
    """Собирает текст экрана прошедших записей."""
    if not items:
        return EMPTY_PASSED
    lines = [bold("🗄 Прошедшие")]
    for index, item in enumerate(items, start=1):
        subject = subject_names.get(item.subject_id, "?")
        due = format_due_short(item.due_at, group.tz, now)
        lines.append(
            f"{index}. {KIND_ICONS[item.kind]} {esc(item.title)} — {esc(due)} · {italic(subject)}"
        )
    return "\n".join(lines)


def render_card(*, item: Item, subject_name: str, group: Group, now: datetime) -> str:
    """Карточка записи со всеми подробностями."""
    head = (
        f"{KIND_ICONS[item.kind]} {bold(item.title)}\n"
        f"{esc(category_label(item.kind, item.category))} · {italic(subject_name)}"
    )
    due_line = f"🕒 {esc(format_due_full(item.due_at, group.tz, now))}"
    blocks = [head, due_line]
    if item.description:
        blocks.append(esc(item.description))
    if item.links:
        blocks.append("\n".join(link(url, url) for url in item.links))
    blocks.append(f"🔔 {esc(render_reminder_summary(item, group))}")
    return join_blocks(blocks)


def render_draft_summary(
    *, draft: ItemDraft, subject_name: str, group: Group, now: datetime
) -> str:
    """Предпросмотр записи перед созданием."""
    head = (
        f"{KIND_ICONS[draft.kind]} {bold(draft.title)}\n"
        f"{esc(category_label(draft.kind, draft.category))} · {italic(subject_name)}"
    )
    blocks = [head, f"🕒 {esc(format_due_full(draft.due_at, group.tz, now))}"]
    if draft.description:
        blocks.append(esc(draft.description))
    if draft.links:
        blocks.append("\n".join(link(url, url) for url in draft.links))
    offsets = resolve_offsets(
        kind=draft.kind,
        item_offsets=draft.reminder_offsets,
        muted=draft.reminders_muted,
        settings=group.reminders,
    )
    blocks.append(
        f"🔔 {esc(_reminder_summary(draft.reminder_offsets, draft.reminders_muted, offsets))}"
    )
    return join_blocks(blocks)


def render_reminder_summary(item: Item, group: Group) -> str:
    """Строка «Напоминания: …» для записи."""
    offsets = resolve_offsets(
        kind=item.kind,
        item_offsets=item.reminder_offsets,
        muted=item.reminders_muted,
        settings=group.reminders,
    )
    return _reminder_summary(item.reminder_offsets, item.reminders_muted, offsets)


def render_digest(
    *,
    items: list[Item],
    subject_names: dict[int, str],
    group: Group,
    now: datetime,
    header: str,
) -> list[str]:
    """Дайджест, сгруппированный по дням; возвращает готовые блоки текста."""
    dated: list[tuple[datetime, Item]] = [(i.due_at, i) for i in items if i.due_at is not None]
    open_items = [i for i in items if i.due_at is None]
    blocks = [bold(header)]

    by_day: dict[tuple[int, int, int], list[tuple[datetime, Item]]] = {}
    for due_at, item in sorted(dated, key=lambda pair: pair[0]):
        local = due_at.astimezone(group.tz)
        by_day.setdefault((local.year, local.month, local.day), []).append((due_at, item))

    number = 1
    for (year, month, day), day_items in by_day.items():
        weekday = WEEKDAYS_RU[datetime(year, month, day).weekday()]
        lines = [bold(f"{day:02d}.{month:02d} ({weekday})")]
        for due_at, item in day_items:
            local = due_at.astimezone(group.tz)
            subject = subject_names.get(item.subject_id, "?")
            lines.append(
                f"{number}. {local.hour:02d}:{local.minute:02d} {KIND_ICONS[item.kind]}"
                f" {esc(item.title)} · {italic(subject)}"
            )
            number += 1
        blocks.append("\n".join(lines))

    if open_items:
        lines = [bold("До конца модуля")]
        for item in open_items:
            subject = subject_names.get(item.subject_id, "?")
            lines.append(f"{number}. 📝 {esc(item.title)} · {italic(subject)}")
            number += 1
        blocks.append("\n".join(lines))

    return blocks if len(blocks) > 1 else []


def _render_section(
    header: str,
    items: list[Item],
    subject_names: dict[int, str],
    group: Group,
    now: datetime,
    numbers: dict[int, int],
) -> str:
    if not items:
        return ""
    by_subject: dict[int, list[Item]] = {}
    for item in items:
        by_subject.setdefault(item.subject_id, []).append(item)

    lines = [bold(header)]
    for subject_id, subject_items in by_subject.items():
        lines.append("")
        lines.append(bold(subject_names.get(subject_id, "?")))
        for item in subject_items:
            due = format_due_short(item.due_at, group.tz, now)
            lines.append(f"{numbers[item.id]}. {esc(item.title)} — {esc(due)}")
    return "\n".join(lines)


def _reminder_summary(item_offsets: list[int] | None, muted: bool, resolved: list[int]) -> str:
    if muted:
        return "Напоминания выключены"
    if item_offsets is None:
        return f"Напоминания как в группе ({offsets_label(resolved)})"
    return f"Напоминания: {offsets_label(resolved)}"


def group_by_subject_order(items: list[Item], subject_names: dict[int, str]) -> list[Item]:
    """Сортирует записи так, чтобы предметы шли в порядке ближайшего дедлайна."""
    earliest: dict[int, datetime | None] = {}
    for item in items:
        if item.subject_id not in earliest:
            earliest[item.subject_id] = item.due_at
            continue
        current = earliest[item.subject_id]
        if item.due_at is not None and (current is None or item.due_at < current):
            earliest[item.subject_id] = item.due_at

    ranked = sorted(
        earliest,
        key=lambda sid: (
            earliest[sid] is None,
            earliest[sid] or _EPOCH,
            subject_names.get(sid, "?"),
        ),
    )
    position = {subject_id: index for index, subject_id in enumerate(ranked)}
    return sorted(
        items,
        key=lambda i: (position[i.subject_id], i.due_at is None, i.due_at or _EPOCH),
    )

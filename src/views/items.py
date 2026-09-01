from datetime import UTC, datetime

from src.domain.enums import KIND_ICONS, category_label
from src.domain.humanize import (
    OPEN_DUE_LABEL,
    WEEKDAYS_RU,
    ensure_utc,
    format_due_short,
    humanize_delta,
)
from src.domain.models import Group, Item, ItemDraft
from src.domain.reminders import offsets_label, resolve_offsets
from src.views.common import (
    TIME_FULL,
    TIME_ONLY,
    TIME_RELATIVE,
    bold,
    esc,
    expandable_quote,
    italic,
    join_blocks,
    link,
    quote,
    raw_expandable_quote,
    time_tag,
)

LIST_HEADER_TASKS = "📝 Задачи"
LIST_HEADER_EVENTS = "📅 Важные даты"
EMPTY_LIST = "Пока пусто. Нажми «➕ Добавить», чтобы завести первую задачу или важную дату."
_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)

LONG_DESCRIPTION = 200
MANY_LINKS = 3

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


def render_list_rich(
    *,
    tasks: list[Item],
    events: list[Item],
    subject_names: dict[int, str],
    completed_ids: frozenset[int],
    group: Group,
    now: datetime,
) -> str:
    """Список в разметке Rich HTML: чек-боксы, заголовки секций и предметов.

    Галочка нарисована, но не нажимается — Telegram не шлёт о ней событий,
    поэтому переключают её кнопки под сообщением.
    """
    numbers = item_number_map(tasks, events)
    parts: list[str] = []
    for header, section_items in (
        (LIST_HEADER_TASKS, tasks),
        (LIST_HEADER_EVENTS, events),
    ):
        if not section_items:
            continue
        parts.append(f"<h3>{esc(header)}</h3>")
        by_subject: dict[int, list[Item]] = {}
        for item in section_items:
            by_subject.setdefault(item.subject_id, []).append(item)
        for subject_id, subject_items in by_subject.items():
            parts.append(f"<p><b>{esc(subject_names.get(subject_id, '?'))}</b></p>")
            rows = []
            for item in subject_items:
                checked = " checked" if item.id in completed_ids else ""
                title = esc(item.title)
                if item.id in completed_ids:
                    title = f"<s>{title}</s>"
                rows.append(
                    f'<li><input type="checkbox"{checked}>'
                    f"{numbers[item.id]}. {title} — {_due_short(item, group, now)}</li>"
                )
            parts.append(f"<ul>{''.join(rows)}</ul>")
    return "".join(parts) or f"<p>{esc(EMPTY_LIST)}</p>"


def render_passed(
    *, items: list[Item], subject_names: dict[int, str], group: Group, now: datetime
) -> str:
    """Собирает текст экрана прошедших записей."""
    if not items:
        return EMPTY_PASSED
    lines = []
    for index, item in enumerate(items, start=1):
        subject = subject_names.get(item.subject_id, "?")
        lines.append(
            f"{index}. {KIND_ICONS[item.kind]} {esc(item.title)}"
            f" — {_due_short(item, group, now)} · {italic(subject)}"
        )
    return f"{bold('🗄 Прошедшие')}\n{raw_expandable_quote(chr(10).join(lines))}"


def render_card(
    *,
    item: Item,
    subject_name: str,
    group: Group,
    now: datetime,
    completed: bool = False,
) -> str:
    """Карточка записи со всеми подробностями."""
    mark = "✅ " if completed else ""
    head = (
        f"{KIND_ICONS[item.kind]} {mark}{bold(item.title)}\n"
        f"{esc(category_label(item.kind, item.category))} · {italic(subject_name)}"
    )
    blocks = [head, f"🕒 {_due_full(item.due_at, group, now)}"]
    if item.description:
        blocks.append(_description_block(item.description))
    if item.links:
        blocks.append(_links_block(item.links))
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
    blocks = [head, f"🕒 {_due_full(draft.due_at, group, now)}"]
    if draft.description:
        blocks.append(_description_block(draft.description))
    if draft.links:
        blocks.append(_links_block(draft.links))
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
            clock = time_tag(f"{local.hour:02d}:{local.minute:02d}", due_at, TIME_ONLY)
            lines.append(
                f"{number}. {clock} {KIND_ICONS[item.kind]} {esc(item.title)} · {italic(subject)}"
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
            lines.append(f"{numbers[item.id]}. {esc(item.title)} — {_due_short(item, group, now)}")
    return "\n".join(lines)


def _due_short(item: Item, group: Group, now: datetime) -> str:
    """Короткий дедлайн записи: местное время читателя либо метка открытой задачи."""
    if item.due_at is None:
        return esc(OPEN_DUE_LABEL)
    return time_tag(format_due_short(item.due_at, group.tz, now), item.due_at)


def _due_full(due_at: datetime | None, group: Group, now: datetime) -> str:
    """Полный дедлайн с относительным сроком; оба — в локали читателя."""
    if due_at is None:
        return esc(OPEN_DUE_LABEL)
    absolute = time_tag(format_due_short(due_at, group.tz, now), due_at, TIME_FULL)
    relative = time_tag(humanize_delta(ensure_utc(due_at) - now), due_at, TIME_RELATIVE)
    return f"{absolute} ({relative})"


def _description_block(description: str) -> str:
    """Описание цитатой; длинное — сворачиваемой."""
    if len(description) > LONG_DESCRIPTION:
        return expandable_quote(description)
    return quote(description)


def _links_block(links: list[str]) -> str:
    """Ссылки списком; если их много — сворачиваемой цитатой."""
    body = "\n".join(link(url, url) for url in links)
    if len(links) > MANY_LINKS:
        return raw_expandable_quote(body)
    return body


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

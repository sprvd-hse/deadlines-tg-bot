from datetime import datetime

from src.domain.enums import KIND_ICONS, ItemKind, category_label
from src.domain.humanize import ensure_utc, format_due_short, humanize_delta
from src.domain.models import Group, Item
from src.domain.reminders import offset_label
from src.views.common import (
    TIME_FULL,
    TIME_RELATIVE,
    bold,
    esc,
    expandable_quote,
    italic,
    join_blocks,
    link,
    quote,
    time_tag,
)

_LEAD_IN = {
    ItemKind.TASK: "нужно сдать",
    ItemKind.EVENT: "состоится",
}


def render_reminder(
    *, item: Item, subject_name: str, group: Group, offset_minutes: int, now: datetime
) -> str:
    """Текст личного напоминания о задании или важной дате."""
    lead = _LEAD_IN[item.kind]
    header = bold(f"🔔 {offset_label(offset_minutes).capitalize()} — {lead}")
    body = (
        f"{KIND_ICONS[item.kind]} {bold(item.title)}\n"
        f"{esc(category_label(item.kind, item.category))} · {italic(subject_name)}\n"
        f"🕒 {_due(item.due_at, group, now)}"
    )
    blocks = [f"{header}\n{esc(group.name)}", body]
    if item.description:
        blocks.append(
            expandable_quote(item.description)
            if len(item.description) > 200
            else quote(item.description)
        )
    if item.links:
        blocks.append("\n".join(link(url, url) for url in item.links))
    return join_blocks(blocks)


def _due(due_at, group: Group, now: datetime) -> str:
    """Дедлайн напоминания в местном времени читателя."""
    if due_at is None:
        return ""
    absolute = time_tag(format_due_short(due_at, group.tz, now), due_at, TIME_FULL)
    delta = time_tag(humanize_delta(ensure_utc(due_at) - now), due_at, TIME_RELATIVE)
    return f"{absolute} ({delta})"

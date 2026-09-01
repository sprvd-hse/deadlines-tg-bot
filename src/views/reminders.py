from datetime import datetime

from src.domain.enums import KIND_ICONS, ItemKind, category_label
from src.domain.humanize import format_due_full
from src.domain.models import Group, Item
from src.domain.reminders import offset_label
from src.views.common import bold, esc, italic, join_blocks, link

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
        f"🕒 {esc(format_due_full(item.due_at, group.tz, now))}"
    )
    blocks = [f"{header}\n{esc(group.name)}", body]
    if item.description:
        blocks.append(esc(item.description))
    if item.links:
        blocks.append("\n".join(link(url, url) for url in item.links))
    return join_blocks(blocks)

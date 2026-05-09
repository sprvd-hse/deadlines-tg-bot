import logging
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models import Assignment, Group, GroupMember, Subject, User
from src.keyboards import ASSIGNMENT_TYPE_LABELS

logger = logging.getLogger(__name__)

DIGEST_HORIZON_DAYS = 14
MAX_MESSAGE_CHARS = 4000

_RU_WEEKDAYS = ["пн", "вт", "ср", "чт", "пт", "сб", "вс"]


async def list_groups(session: AsyncSession) -> list[Group]:
    return list((await session.execute(select(Group))).scalars())


async def list_recipients(session: AsyncSession, group_id: int) -> list[User]:
    rows = (
        await session.execute(
            select(User)
            .join(GroupMember, GroupMember.user_id == User.id)
            .where(GroupMember.group_id == group_id)
        )
    ).scalars()
    return list(rows)


async def build_digest_messages(
    session: AsyncSession, group: Group, *, now_utc: datetime | None = None
) -> list[str]:
    """Возвращает список сообщений (учитывая лимит 4000 символов, разбивка по дням)."""
    now = now_utc or datetime.now(tz=UTC)
    horizon = now + timedelta(days=DIGEST_HORIZON_DAYS)
    rows = list(
        (
            await session.execute(
                select(Assignment, Subject.name)
                .join(Subject, Subject.id == Assignment.subject_id)
                .where(
                    Assignment.group_id == group.id,
                    Assignment.is_active.is_(True),
                    Assignment.deadline >= now,
                    Assignment.deadline < horizon,
                )
                .order_by(Assignment.deadline)
            )
        ).all()
    )
    tz = ZoneInfo(group.timezone)
    by_day: dict[str, list[str]] = {}
    day_order: list[str] = []

    for a, subject_name in rows:
        local = a.deadline.astimezone(tz)
        day_key = local.strftime("%Y-%m-%d")
        type_label = ASSIGNMENT_TYPE_LABELS.get(a.type, a.type.value)
        time_str = local.strftime("%H:%M")
        line = f"  {time_str} #{a.short_id} [{type_label}] {a.title} ({subject_name})"
        if day_key not in by_day:
            by_day[day_key] = []
            day_order.append(day_key)
        by_day[day_key].append(line)

    if not day_order:
        return []

    header = f"Дайджест группы «{group.name}» — задания на ближайшие {DIGEST_HORIZON_DAYS} дней:"
    day_blocks: list[str] = []
    for day_key in day_order:
        d = datetime.strptime(day_key, "%Y-%m-%d")
        weekday = _RU_WEEKDAYS[d.weekday()]
        title = f"{d.day:02d}.{d.month:02d} ({weekday})"
        day_blocks.append("\n".join([title, *by_day[day_key]]))

    messages: list[str] = []
    current = header
    for block in day_blocks:
        candidate = current + "\n\n" + block
        if len(candidate) <= MAX_MESSAGE_CHARS:
            current = candidate
            continue
        messages.append(current)
        current = block
    if current:
        messages.append(current)
    return messages

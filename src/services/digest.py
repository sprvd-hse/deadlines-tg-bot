import logging
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models import Assignment, AssignmentType, Group, GroupMember, Subject, User
from src.keyboards import ASSIGNMENT_TYPE_LABELS
from src.utils import ensure_utc

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
    session: AsyncSession,
    group: Group,
    *,
    now_utc: datetime | None = None,
    horizon_days: int | None = DIGEST_HORIZON_DAYS,
    include_passed: bool = False,
    header_override: str | None = None,
) -> list[str]:
    """Возвращает список сообщений (учитывая лимит 4000 символов, разбивка по дням).

    horizon_days=None — без ограничения сверху (все будущие дедлайны).
    include_passed=True — добавить блок «Прошедшие» в конец.
    """
    now = now_utc or datetime.now(tz=UTC)
    upcoming_stmt = (
        select(Assignment, Subject.name)
        .join(Subject, Subject.id == Assignment.subject_id)
        .where(
            Assignment.group_id == group.id,
            Assignment.is_active.is_(True),
            Assignment.deadline >= now,
        )
        .order_by(Assignment.deadline)
    )
    if horizon_days is not None:
        upcoming_stmt = upcoming_stmt.where(
            Assignment.deadline < now + timedelta(days=horizon_days)
        )
    rows = list((await session.execute(upcoming_stmt)).all())
    open_rows = list(
        (
            await session.execute(
                select(Assignment, Subject.name)
                .join(Subject, Subject.id == Assignment.subject_id)
                .where(
                    Assignment.group_id == group.id,
                    Assignment.is_active.is_(True),
                    Assignment.deadline.is_(None),
                )
                .order_by(Subject.name, Assignment.short_id)
            )
        ).all()
    )
    passed_rows: list[tuple[Assignment, str]] = []
    if include_passed:
        passed_rows = list(
            (
                await session.execute(
                    select(Assignment, Subject.name)
                    .join(Subject, Subject.id == Assignment.subject_id)
                    .where(
                        Assignment.group_id == group.id,
                        Assignment.is_active.is_(True),
                        Assignment.deadline.is_not(None),
                        Assignment.deadline < now,
                    )
                    .order_by(Assignment.deadline.desc())
                )
            ).all()
        )
    tz = ZoneInfo(group.timezone)
    by_day: dict[str, list[tuple[Assignment, str]]] = {}
    day_order: list[str] = []

    for a, subject_name in rows:
        local = ensure_utc(a.deadline).astimezone(tz)
        day_key = local.strftime("%Y-%m-%d")
        if day_key not in by_day:
            by_day[day_key] = []
            day_order.append(day_key)
        by_day[day_key].append((a, subject_name))

    if not day_order and not open_rows and not passed_rows:
        return []

    def _type_part(a: Assignment) -> str:
        if a.type == AssignmentType.other:
            return ""
        return f"[{ASSIGNMENT_TYPE_LABELS.get(a.type, a.type.value)}] "

    if header_override is not None:
        header = header_override
    elif horizon_days is None:
        header = f"Все задания группы «{group.name}»:"
    else:
        header = (
            f"Дайджест группы «{group.name}» — задания на ближайшие {horizon_days} дней:"
        )
    day_blocks: list[str] = []
    counter = 1
    for day_key in day_order:
        d = datetime.strptime(day_key, "%Y-%m-%d")
        weekday = _RU_WEEKDAYS[d.weekday()]
        title = f"{d.day:02d}.{d.month:02d} ({weekday})"
        lines = [title]
        for a, subject_name in by_day[day_key]:
            local = ensure_utc(a.deadline).astimezone(tz)
            time_str = local.strftime("%H:%M")
            lines.append(
                f"  {counter}. {time_str} {_type_part(a)}{a.title} ({subject_name})"
            )
            counter += 1
        day_blocks.append("\n".join(lines))

    if open_rows:
        open_lines = ["До конца модуля:"]
        for a, subject_name in open_rows:
            open_lines.append(
                f"  {counter}. {_type_part(a)}{a.title} ({subject_name})"
            )
            counter += 1
        day_blocks.append("\n".join(open_lines))

    if passed_rows:
        passed_lines = ["Прошедшие:"]
        for a, subject_name in passed_rows:
            local = ensure_utc(a.deadline).astimezone(tz)
            dl = f"{local.day:02d}.{local.month:02d} {local.strftime('%H:%M')}"
            passed_lines.append(
                f"  {counter}. {dl} {_type_part(a)}{a.title} ({subject_name})"
            )
            counter += 1
        day_blocks.append("\n".join(passed_lines))

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

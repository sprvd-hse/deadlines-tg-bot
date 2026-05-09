import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models import (
    Assignment,
    AssignmentCompletion,
    AssignmentType,
    Group,
    GroupMember,
    ReminderLog,
    ReminderType,
    Subject,
    User,
)
from src.keyboards import ASSIGNMENT_TYPE_LABELS
from src.utils import format_deadline_display

logger = logging.getLogger(__name__)

OFFSETS_BY_TYPE: dict[AssignmentType, list[tuple[ReminderType, timedelta]]] = {
    AssignmentType.homework: [(ReminderType.advance_1d, timedelta(days=1))],
    AssignmentType.test: [
        (ReminderType.advance_7d, timedelta(days=7)),
        (ReminderType.advance_3d, timedelta(days=3)),
        (ReminderType.advance_1d, timedelta(days=1)),
    ],
    AssignmentType.exam: [
        (ReminderType.advance_7d, timedelta(days=7)),
        (ReminderType.advance_3d, timedelta(days=3)),
        (ReminderType.advance_1d, timedelta(days=1)),
    ],
    AssignmentType.lab: [
        (ReminderType.advance_3d, timedelta(days=3)),
        (ReminderType.advance_1d, timedelta(days=1)),
    ],
    AssignmentType.project: [
        (ReminderType.advance_3d, timedelta(days=3)),
        (ReminderType.advance_1d, timedelta(days=1)),
    ],
    AssignmentType.other: [(ReminderType.advance_1d, timedelta(days=1))],
}

RECOVERY_MIN_LEAD = timedelta(minutes=30)

_OFFSET_LABELS: dict[ReminderType, str] = {
    ReminderType.advance_7d: "за 7 дней",
    ReminderType.advance_3d: "за 3 дня",
    ReminderType.advance_1d: "за 1 день",
}


def offsets_for(assignment_type: AssignmentType) -> list[tuple[ReminderType, timedelta]]:
    return OFFSETS_BY_TYPE.get(assignment_type, [])


def build_reminder_text(
    *,
    group_name: str,
    group_tz: str,
    assignment: Assignment,
    subject_name: str,
    reminder_type: ReminderType,
) -> str:
    type_label = ASSIGNMENT_TYPE_LABELS.get(assignment.type, assignment.type.value)
    deadline_str = format_deadline_display(assignment.deadline, group_tz)
    offset_label = _OFFSET_LABELS.get(reminder_type, "")
    lines = [
        f"Напоминание {offset_label} ({group_name}):",
        f"#{assignment.short_id} [{type_label}] {assignment.title} ({subject_name})",
        f"Дедлайн: {deadline_str}",
    ]
    if assignment.description:
        lines.append(assignment.description)
    if assignment.links:
        lines.extend(assignment.links)
    return "\n".join(lines)


async def recipients_for(
    session: AsyncSession, assignment: Assignment
) -> list[User]:
    """Возвращает участников группы, которым нужно слать напоминание:
    personal_reminders_enabled=True и без AssignmentCompletion.
    """
    completed_user_ids = set(
        (
            await session.execute(
                select(AssignmentCompletion.user_id).where(
                    AssignmentCompletion.assignment_id == assignment.id
                )
            )
        ).scalars()
    )
    rows = (
        await session.execute(
            select(User)
            .join(GroupMember, GroupMember.user_id == User.id)
            .where(
                GroupMember.group_id == assignment.group_id,
                GroupMember.personal_reminders_enabled.is_(True),
            )
        )
    ).scalars()
    return [u for u in rows if u.id not in completed_user_ids]


async def already_logged_user_ids(
    session: AsyncSession, *, assignment_id: int, reminder_type: ReminderType
) -> set[int]:
    rows = (
        await session.execute(
            select(ReminderLog.user_id).where(
                ReminderLog.assignment_id == assignment_id,
                ReminderLog.reminder_type == reminder_type,
            )
        )
    ).scalars()
    return {uid for uid in rows if uid is not None}


async def fetch_context(
    session: AsyncSession, assignment_id: int
) -> tuple[Assignment, Group, str] | None:
    """Возвращает (assignment, group, subject_name) либо None, если задание неактивно/удалено."""
    a = await session.get(Assignment, assignment_id)
    if a is None or not a.is_active:
        return None
    group = await session.get(Group, a.group_id)
    if group is None:
        return None
    subject = await session.get(Subject, a.subject_id)
    subject_name = subject.name if subject is not None else "?"
    return a, group, subject_name


async def send_reminder_batch(
    session_factory,
    bot,
    *,
    assignment_id: int,
    reminder_type: ReminderType,
    now_utc: datetime | None = None,
) -> int:
    """Отправляет личные напоминания всем подходящим участникам, пишет ReminderLog.

    Идемпотентно: на каждого получателя своя транзакция, ReminderLog коммитится
    сразу после успешной отправки, поэтому при сбое в середине батча не будет
    дублей при ретрае. `session_factory` — async_sessionmaker (например, SessionLocal).
    """
    now = now_utc or datetime.now(tz=UTC)

    # Подготовка: один read-снимок контекста, получателей и уже разосланных.
    async with session_factory() as session:
        ctx = await fetch_context(session, assignment_id)
        if ctx is None:
            return 0
        assignment, group, subject_name = ctx
        if assignment.deadline <= now:
            return 0
        already = await already_logged_user_ids(
            session, assignment_id=assignment_id, reminder_type=reminder_type
        )
        recipients = await recipients_for(session, assignment)
        text = build_reminder_text(
            group_name=group.name,
            group_tz=group.timezone,
            assignment=assignment,
            subject_name=subject_name,
            reminder_type=reminder_type,
        )
        recipient_pairs = [(u.id, u.tg_user_id) for u in recipients if u.id not in already]

    sent = 0
    for user_id, tg_user_id in recipient_pairs:
        try:
            await bot.send_message(tg_user_id, text)
        except Exception:
            logger.exception(
                "Не удалось отправить напоминание user=%s assignment=%s",
                user_id,
                assignment_id,
            )
            continue
        # Коммитим лог сразу после успешной отправки — это даёт идемпотентность
        # при падениях между отправками.
        try:
            async with session_factory() as log_session, log_session.begin():
                log_session.add(
                    ReminderLog(
                        assignment_id=assignment_id,
                        user_id=user_id,
                        reminder_type=reminder_type,
                    )
                )
        except Exception:
            logger.exception(
                "Не удалось записать ReminderLog user=%s assignment=%s",
                user_id,
                assignment_id,
            )
            continue
        sent += 1
    return sent

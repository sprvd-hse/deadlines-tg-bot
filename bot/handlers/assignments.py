from datetime import datetime

from aiogram import F, Router
from aiogram.filters import Command, CommandObject
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from bot import texts
from bot.db.models import Assignment, AssignmentType
from bot.db.session import SessionLocal
from bot.keyboards import (
    ASSIGNMENT_TYPE_LABELS,
    assignment_type_kb,
    confirm_kb,
    subjects_kb,
)
from bot.services import assignments as assn_svc
from bot.services import subjects as subj_svc
from bot.services.deadline_parser import DeadlineParseError, parse_deadline
from bot.services.groups import get_active_group, is_creator
from bot.services.users import get_or_create_user
from bot.states import AddAssignment
from bot.utils import format_deadline_display, parse_links

router = Router(name="assignments")

_TITLE_MAX = 256
_DESCRIPTION_MAX = 2048
_SKIP_TOKEN = "-"


def _user_meta(message: Message) -> tuple[int, str | None, str] | None:
    if message.from_user is None:
        return None
    u = message.from_user
    return u.id, u.username, u.full_name or u.username or "user"


# ============ /add FSM ============


@router.message(Command("add"))
async def cmd_add(message: Message, state: FSMContext) -> None:
    meta = _user_meta(message)
    if meta is None:
        return
    tg_id, username, full_name = meta

    async with SessionLocal() as session, session.begin():
        user = await get_or_create_user(session, tg_id, username, full_name)
        group = await get_active_group(session, user)
        if group is None:
            await message.answer(texts.NO_ACTIVE_GROUP)
            return
        subjects = await subj_svc.list_subjects(session, group.id)
        if not subjects:
            await message.answer(texts.ADD_NEED_SUBJECTS)
            return
        await state.update_data(group_id=group.id, group_tz=group.timezone)

    await state.set_state(AddAssignment.waiting_type)
    await message.answer(texts.ADD_STEP_TYPE, reply_markup=assignment_type_kb())


@router.callback_query(AddAssignment.waiting_type, F.data.startswith("assign_type:"))
async def add_type(callback: CallbackQuery, state: FSMContext) -> None:
    if callback.data is None:
        await callback.answer()
        return
    type_value = callback.data.split(":", 1)[1]
    try:
        atype = AssignmentType(type_value)
    except ValueError:
        await callback.answer()
        return

    data = await state.get_data()
    group_id = data["group_id"]

    async with SessionLocal() as session:
        subjects = await subj_svc.list_subjects(session, group_id)

    await state.update_data(type=atype.value)
    await state.set_state(AddAssignment.waiting_subject)
    if isinstance(callback.message, Message):
        await callback.message.answer(texts.ADD_STEP_SUBJECT, reply_markup=subjects_kb(subjects))
    await callback.answer()


@router.callback_query(AddAssignment.waiting_subject, F.data.startswith("assign_subj:"))
async def add_subject(callback: CallbackQuery, state: FSMContext) -> None:
    if callback.data is None:
        await callback.answer()
        return
    try:
        subject_id = int(callback.data.split(":", 1)[1])
    except ValueError:
        await callback.answer()
        return

    data = await state.get_data()
    group_id = data["group_id"]

    async with SessionLocal() as session:
        subjects = await subj_svc.list_subjects(session, group_id)
    subject = next((s for s in subjects if s.id == subject_id), None)
    if subject is None:
        await callback.answer()
        return

    await state.update_data(subject_id=subject.id, subject_name=subject.name)
    await state.set_state(AddAssignment.waiting_title)
    if isinstance(callback.message, Message):
        await callback.message.answer(texts.ADD_STEP_TITLE)
    await callback.answer()


@router.message(AddAssignment.waiting_title, F.text)
async def add_title(message: Message, state: FSMContext) -> None:
    title = (message.text or "").strip()
    if not title:
        await message.answer(texts.ADD_STEP_TITLE)
        return
    if len(title) > _TITLE_MAX:
        await message.answer(texts.TITLE_TOO_LONG)
        return
    await state.update_data(title=title)
    await state.set_state(AddAssignment.waiting_description)
    await message.answer(texts.ADD_STEP_DESCRIPTION)


@router.message(AddAssignment.waiting_description, F.text)
async def add_description(message: Message, state: FSMContext) -> None:
    raw = (message.text or "").strip()
    description: str | None
    if raw == _SKIP_TOKEN:
        description = None
    else:
        if len(raw) > _DESCRIPTION_MAX:
            await message.answer(texts.DESCRIPTION_TOO_LONG)
            return
        description = raw
    await state.update_data(description=description)
    await state.set_state(AddAssignment.waiting_deadline)
    await message.answer(texts.ADD_STEP_DEADLINE)


@router.message(AddAssignment.waiting_deadline, F.text)
async def add_deadline(message: Message, state: FSMContext) -> None:
    raw = (message.text or "").strip()
    data = await state.get_data()
    try:
        deadline_utc = parse_deadline(raw, data["group_tz"])
    except DeadlineParseError as e:
        await message.answer(texts.ADD_DEADLINE_ERROR.format(error=str(e)))
        return
    await state.update_data(deadline_iso=deadline_utc.isoformat())
    await state.set_state(AddAssignment.waiting_links)
    await message.answer(texts.ADD_STEP_LINKS)


@router.message(AddAssignment.waiting_links, F.text)
async def add_links(message: Message, state: FSMContext) -> None:
    raw = (message.text or "").strip()
    if raw == _SKIP_TOKEN:
        links: list[str] = []
    else:
        links = parse_links(raw)
        if not links:
            await message.answer(texts.ADD_LINKS_NONE_VALID)
            return
    await state.update_data(links=links)
    await state.set_state(AddAssignment.waiting_confirm)

    data = await state.get_data()
    summary = _build_summary(data)
    await message.answer(f"{texts.ADD_CONFIRM_HEADER}\n\n{summary}", reply_markup=confirm_kb())


@router.callback_query(AddAssignment.waiting_confirm, F.data == "assign_confirm:no")
async def add_confirm_no(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    if isinstance(callback.message, Message):
        await callback.message.answer(texts.ADD_CANCELLED)
    await callback.answer()


@router.callback_query(AddAssignment.waiting_confirm, F.data == "assign_confirm:yes")
async def add_confirm_yes(callback: CallbackQuery, state: FSMContext) -> None:
    if callback.from_user is None:
        await callback.answer()
        return
    data = await state.get_data()

    deadline_utc = datetime.fromisoformat(data["deadline_iso"])
    atype = AssignmentType(data["type"])

    tg_user = callback.from_user
    async with SessionLocal() as session, session.begin():
        user = await get_or_create_user(
            session,
            tg_user.id,
            tg_user.username,
            tg_user.full_name or tg_user.username or "user",
        )
        assignment = await assn_svc.create_assignment(
            session,
            group_id=data["group_id"],
            creator_user_id=user.id,
            subject_id=data["subject_id"],
            assignment_type=atype,
            title=data["title"],
            description=data.get("description"),
            links=data.get("links", []),
            deadline_utc=deadline_utc,
        )
        short_id, title = assignment.short_id, assignment.title

    await state.clear()
    if isinstance(callback.message, Message):
        await callback.message.answer(texts.ADD_CREATED.format(short_id=short_id, title=title))
    await callback.answer()


def _build_summary(data: dict) -> str:

    atype = AssignmentType(data["type"])
    deadline_utc = datetime.fromisoformat(data["deadline_iso"])
    deadline_str = format_deadline_display(deadline_utc, data["group_tz"])

    lines = [
        f"Тип: {ASSIGNMENT_TYPE_LABELS[atype]}",
        f"Предмет: {data['subject_name']}",
        f"Название: {data['title']}",
    ]
    if data.get("description"):
        lines.append(f"Описание: {data['description']}")
    lines.append(f"Дедлайн: {deadline_str}")
    if data.get("links"):
        lines.append("Ссылки:\n" + "\n".join(data["links"]))
    return "\n".join(lines)


# ============ /list, /list_subject ============


@router.message(Command("list"))
async def cmd_list(message: Message) -> None:
    await _render_list(message, subject_name=None)


@router.message(Command("list_subject"))
async def cmd_list_subject(message: Message, command: CommandObject) -> None:
    name = (command.args or "").strip()
    if not name:
        await message.answer(texts.USAGE_LIST_SUBJECT)
        return
    await _render_list(message, subject_name=name)


async def _render_list(message: Message, subject_name: str | None) -> None:
    meta = _user_meta(message)
    if meta is None:
        return
    tg_id, username, full_name = meta

    async with SessionLocal() as session, session.begin():
        user = await get_or_create_user(session, tg_id, username, full_name)
        group = await get_active_group(session, user)
        if group is None:
            await message.answer(texts.NO_ACTIVE_GROUP)
            return

        subject_id: int | None = None
        if subject_name is not None:
            subject = await subj_svc.get_subject_by_name(session, group.id, subject_name)
            if subject is None:
                await message.answer(texts.SUBJECT_NOT_FOUND.format(name=subject_name))
                return
            subject_id = subject.id

        active = await assn_svc.list_active(session, group.id, subject_id=subject_id)
        passed = await assn_svc.list_passed(session, group.id, subject_id=subject_id)
        all_ids = [a.id for a in active] + [a.id for a in passed]
        completed = await assn_svc.completed_ids_for_user(session, user.id, all_ids)
        subjects = await subj_svc.list_subjects(session, group.id)
        subj_by_id = {s.id: s.name for s in subjects}
        tz_name = group.timezone

    if not active and not passed:
        await message.answer(texts.LIST_EMPTY)
        return

    parts: list[str] = []
    if active:
        parts.append(texts.LIST_ACTIVE_HEADER)
        parts.extend(_render_item(a, subj_by_id, tz_name, a.id in completed) for a in active)
    if passed:
        if parts:
            parts.append("")
        parts.append(texts.LIST_PASSED_HEADER)
        parts.extend(_render_item(a, subj_by_id, tz_name, a.id in completed) for a in passed)

    await message.answer("\n".join(parts))


def _render_item(a: Assignment, subj_by_id: dict[int, str], tz_name: str, completed: bool) -> str:
    marker = "✓ " if completed else ""
    type_label = ASSIGNMENT_TYPE_LABELS.get(a.type, a.type.value)
    subject_label = subj_by_id.get(a.subject_id, "?")
    deadline_str = format_deadline_display(a.deadline, tz_name)
    return f"{marker}#{a.short_id} [{type_label}] {a.title} ({subject_label}) — {deadline_str}"


# ============ /delete ============


@router.message(Command("delete"))
async def cmd_delete(message: Message, command: CommandObject) -> None:
    meta = _user_meta(message)
    if meta is None:
        return
    raw = (command.args or "").strip()
    try:
        short_id = int(raw)
    except ValueError:
        await message.answer(texts.USAGE_DELETE)
        return

    tg_id, username, full_name = meta
    async with SessionLocal() as session, session.begin():
        user = await get_or_create_user(session, tg_id, username, full_name)
        group = await get_active_group(session, user)
        if group is None:
            await message.answer(texts.NO_ACTIVE_GROUP)
            return
        is_grp_creator = await is_creator(session, user.id, group.id)
        status = await assn_svc.soft_delete(
            session,
            group_id=group.id,
            short_id=short_id,
            requesting_user_id=user.id,
            is_creator_of_group=is_grp_creator,
        )

    if status == "not_found":
        await message.answer(texts.DELETE_NOT_FOUND.format(n=short_id))
    elif status == "forbidden":
        await message.answer(texts.DELETE_FORBIDDEN)
    elif status == "already_deleted":
        await message.answer(texts.DELETE_ALREADY.format(n=short_id))
    else:
        await message.answer(texts.DELETE_OK.format(n=short_id))

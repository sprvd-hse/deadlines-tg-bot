from datetime import datetime

from aiogram import F, Router
from aiogram.filters import Command, CommandObject
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy.exc import IntegrityError

from src import texts
from src.db.models import Assignment, AssignmentType
from src.db.session import SessionLocal
from src.keyboards import (
    ASSIGNMENT_TYPE_LABELS,
    assignment_type_kb,
    confirm_kb,
    skip_kb,
    subjects_kb,
)
from src.scheduler import (
    cancel_assignment_reminders,
    schedule_assignment_reminders,
)
from src.services import assignments as assn_svc
from src.services import subjects as subj_svc
from src.services.deadline_parser import DeadlineParseError, parse_deadline
from src.services.groups import get_active_group, is_creator
from src.services.users import get_or_create_user
from src.states import AddAssignment, EditAssignment
from src.utils import format_deadline_display, parse_links

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
    await message.answer(
        texts.ADD_STEP_DESCRIPTION, reply_markup=skip_kb("add_desc")
    )


async def _add_after_description(message: Message, state: FSMContext) -> None:
    await state.set_state(AddAssignment.waiting_deadline)
    await message.answer(texts.ADD_STEP_DEADLINE)


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
    await _add_after_description(message, state)


@router.callback_query(AddAssignment.waiting_description, F.data == "add_desc:skip")
async def add_description_skip(callback: CallbackQuery, state: FSMContext) -> None:
    await state.update_data(description=None)
    if isinstance(callback.message, Message):
        await _add_after_description(callback.message, state)
    await callback.answer()


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
    await message.answer(texts.ADD_STEP_LINKS, reply_markup=skip_kb("add_links"))


async def _add_after_links(message: Message, state: FSMContext) -> None:
    await state.set_state(AddAssignment.waiting_confirm)
    data = await state.get_data()
    summary = _build_summary(data)
    await message.answer(f"{texts.ADD_CONFIRM_HEADER}\n\n{summary}", reply_markup=confirm_kb())


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
    await _add_after_links(message, state)


@router.callback_query(AddAssignment.waiting_links, F.data == "add_links:skip")
async def add_links_skip(callback: CallbackQuery, state: FSMContext) -> None:
    await state.update_data(links=[])
    if isinstance(callback.message, Message):
        await _add_after_links(callback.message, state)
    await callback.answer()


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
    assignment = None
    last_err: IntegrityError | None = None
    # Ретрай на конфликт UNIQUE(group_id, short_id) при параллельных /add.
    for _ in range(5):
        try:
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
            break
        except IntegrityError as e:
            last_err = e
            assignment = None
            continue
    if assignment is None:
        if last_err is not None:
            raise last_err
        return
    short_id, title = assignment.short_id, assignment.title

    schedule_assignment_reminders(assignment)

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
    marker = "✅ " if completed else ""
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
        deleted_id: int | None = None
        if status == "ok":
            deleted = await assn_svc.get_by_short_id(session, group.id, short_id)
            deleted_id = deleted.id if deleted is not None else None

    if deleted_id is not None:
        cancel_assignment_reminders(deleted_id)

    if status == "not_found":
        await message.answer(texts.DELETE_NOT_FOUND.format(n=short_id))
    elif status == "forbidden":
        await message.answer(texts.DELETE_FORBIDDEN)
    elif status == "already_deleted":
        await message.answer(texts.DELETE_ALREADY.format(n=short_id))
    else:
        await message.answer(texts.DELETE_OK.format(n=short_id))


# ============ /done, /undone ============


def _parse_short_id(raw: str) -> int | None:
    raw = raw.strip().lstrip("#")
    try:
        return int(raw)
    except ValueError:
        return None


@router.message(Command("done"))
async def cmd_done(message: Message, command: CommandObject) -> None:
    meta = _user_meta(message)
    if meta is None:
        return
    short_id = _parse_short_id(command.args or "")
    if short_id is None:
        await message.answer(texts.USAGE_DONE)
        return
    tg_id, username, full_name = meta
    async with SessionLocal() as session, session.begin():
        user = await get_or_create_user(session, tg_id, username, full_name)
        group = await get_active_group(session, user)
        if group is None:
            await message.answer(texts.NO_ACTIVE_GROUP)
            return
        status = await assn_svc.mark_completed(
            session, group_id=group.id, short_id=short_id, user_id=user.id
        )

    if status == "not_found":
        await message.answer(texts.DONE_NOT_FOUND.format(n=short_id))
    elif status == "already":
        await message.answer(texts.DONE_ALREADY.format(n=short_id))
    else:
        await message.answer(texts.DONE_OK.format(n=short_id))


@router.message(Command("undone"))
async def cmd_undone(message: Message, command: CommandObject) -> None:
    meta = _user_meta(message)
    if meta is None:
        return
    short_id = _parse_short_id(command.args or "")
    if short_id is None:
        await message.answer(texts.USAGE_UNDONE)
        return
    tg_id, username, full_name = meta
    async with SessionLocal() as session, session.begin():
        user = await get_or_create_user(session, tg_id, username, full_name)
        group = await get_active_group(session, user)
        if group is None:
            await message.answer(texts.NO_ACTIVE_GROUP)
            return
        status = await assn_svc.unmark_completed(
            session, group_id=group.id, short_id=short_id, user_id=user.id
        )

    if status == "not_found":
        await message.answer(texts.DONE_NOT_FOUND.format(n=short_id))
    elif status == "not_marked":
        await message.answer(texts.UNDONE_NOT_MARKED.format(n=short_id))
    else:
        await message.answer(texts.UNDONE_OK.format(n=short_id))


# ============ /edit FSM ============


_CLEAR_TOKEN = "_"


def _links_display(links: list[str]) -> str:
    return ", ".join(links) if links else "—"


@router.message(Command("edit"))
async def cmd_edit(message: Message, command: CommandObject, state: FSMContext) -> None:
    meta = _user_meta(message)
    if meta is None:
        return
    short_id = _parse_short_id(command.args or "")
    if short_id is None:
        await message.answer(texts.USAGE_EDIT)
        return
    tg_id, username, full_name = meta

    async with SessionLocal() as session, session.begin():
        user = await get_or_create_user(session, tg_id, username, full_name)
        group = await get_active_group(session, user)
        if group is None:
            await message.answer(texts.NO_ACTIVE_GROUP)
            return
        a = await assn_svc.get_by_short_id(session, group.id, short_id)
        if a is None or not a.is_active:
            await message.answer(texts.DELETE_NOT_FOUND.format(n=short_id))
            return
        is_grp_creator = await is_creator(session, user.id, group.id)
        if a.created_by_user_id != user.id and not is_grp_creator:
            await message.answer(texts.EDIT_FORBIDDEN)
            return
        subjects = await subj_svc.list_subjects(session, group.id)
        subj_by_id = {s.id: s.name for s in subjects}
        await state.update_data(
            assignment_id=a.id,
            group_id=group.id,
            group_tz=group.timezone,
            short_id=a.short_id,
            type=a.type.value,
            subject_id=a.subject_id,
            subject_name=subj_by_id.get(a.subject_id, "?"),
            title=a.title,
            description=a.description,
            links=list(a.links or []),
            deadline_iso=a.deadline.isoformat(),
            orig_deadline_iso=a.deadline.isoformat(),
        )

    current_label = ASSIGNMENT_TYPE_LABELS[AssignmentType(a.type.value)]
    await state.set_state(EditAssignment.waiting_type)
    await message.answer(
        texts.ADD_STEP_TYPE,
        reply_markup=assignment_type_kb(prefix="edit_type", keep_label=current_label),
    )


@router.callback_query(EditAssignment.waiting_type, F.data.startswith("edit_type:"))
async def edit_type(callback: CallbackQuery, state: FSMContext) -> None:
    if callback.data is None:
        await callback.answer()
        return
    value = callback.data.split(":", 1)[1]
    data = await state.get_data()
    if value != "keep":
        try:
            atype = AssignmentType(value)
        except ValueError:
            await callback.answer()
            return
        await state.update_data(type=atype.value)

    async with SessionLocal() as session:
        subjects = await subj_svc.list_subjects(session, data["group_id"])
    await state.set_state(EditAssignment.waiting_subject)
    if isinstance(callback.message, Message):
        await callback.message.answer(
            texts.ADD_STEP_SUBJECT,
            reply_markup=subjects_kb(subjects, prefix="edit_subj", keep_label=data["subject_name"]),
        )
    await callback.answer()


@router.callback_query(EditAssignment.waiting_subject, F.data.startswith("edit_subj:"))
async def edit_subject(callback: CallbackQuery, state: FSMContext) -> None:
    if callback.data is None:
        await callback.answer()
        return
    value = callback.data.split(":", 1)[1]
    data = await state.get_data()
    if value != "keep":
        try:
            subject_id = int(value)
        except ValueError:
            await callback.answer()
            return
        async with SessionLocal() as session:
            subjects = await subj_svc.list_subjects(session, data["group_id"])
        subject = next((s for s in subjects if s.id == subject_id), None)
        if subject is None:
            await callback.answer()
            return
        await state.update_data(subject_id=subject.id, subject_name=subject.name)

    await state.set_state(EditAssignment.waiting_title)
    if isinstance(callback.message, Message):
        new_data = await state.get_data()
        await callback.message.answer(
            texts.EDIT_STEP_TITLE.format(current=new_data["title"])
        )
    await callback.answer()


@router.message(EditAssignment.waiting_title, F.text)
async def edit_title(message: Message, state: FSMContext) -> None:
    raw = (message.text or "").strip()
    data = await state.get_data()
    if raw != _SKIP_TOKEN:
        if not raw:
            await message.answer(texts.EDIT_STEP_TITLE.format(current=data["title"]))
            return
        if len(raw) > _TITLE_MAX:
            await message.answer(texts.TITLE_TOO_LONG)
            return
        await state.update_data(title=raw)

    await _edit_prompt_description(message, state)


async def _edit_prompt_description(message: Message, state: FSMContext) -> None:
    new_data = await state.get_data()
    current_desc = new_data.get("description") or "—"
    await state.set_state(EditAssignment.waiting_description)
    await message.answer(
        texts.EDIT_STEP_DESCRIPTION.format(current=current_desc),
        reply_markup=skip_kb("edit_desc", with_clear=True),
    )


async def _edit_prompt_deadline(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    deadline_utc = datetime.fromisoformat(data["deadline_iso"])
    current_dl = format_deadline_display(deadline_utc, data["group_tz"])
    await state.set_state(EditAssignment.waiting_deadline)
    await message.answer(
        texts.EDIT_STEP_DEADLINE.format(current=current_dl),
        reply_markup=skip_kb("edit_deadline"),
    )


async def _edit_prompt_links(message: Message, state: FSMContext) -> None:
    new_data = await state.get_data()
    current_links = _links_display(new_data.get("links") or [])
    await state.set_state(EditAssignment.waiting_links)
    await message.answer(
        texts.EDIT_STEP_LINKS.format(current=current_links),
        reply_markup=skip_kb("edit_links", with_clear=True),
    )


async def _edit_prompt_confirm(message: Message, state: FSMContext) -> None:
    await state.set_state(EditAssignment.waiting_confirm)
    data = await state.get_data()
    summary = _build_summary(data)
    await message.answer(
        f"{texts.EDIT_CONFIRM_HEADER}\n\n{summary}",
        reply_markup=confirm_kb(prefix="edit_confirm", yes_label="✅ Сохранить"),
    )


@router.message(EditAssignment.waiting_description, F.text)
async def edit_description(message: Message, state: FSMContext) -> None:
    raw = (message.text or "").strip()
    if raw == _SKIP_TOKEN:
        pass
    elif raw == _CLEAR_TOKEN:
        await state.update_data(description=None)
    else:
        if len(raw) > _DESCRIPTION_MAX:
            await message.answer(texts.DESCRIPTION_TOO_LONG)
            return
        await state.update_data(description=raw)
    await _edit_prompt_deadline(message, state)


@router.callback_query(EditAssignment.waiting_description, F.data.startswith("edit_desc:"))
async def edit_description_btn(callback: CallbackQuery, state: FSMContext) -> None:
    if callback.data is None:
        await callback.answer()
        return
    action = callback.data.rsplit(":", 1)[1]
    if action == "clear":
        await state.update_data(description=None)
    # на skip ничего не меняем — оставляем старое
    if isinstance(callback.message, Message):
        await _edit_prompt_deadline(callback.message, state)
    await callback.answer()


@router.message(EditAssignment.waiting_deadline, F.text)
async def edit_deadline(message: Message, state: FSMContext) -> None:
    raw = (message.text or "").strip()
    data = await state.get_data()
    if raw != _SKIP_TOKEN:
        try:
            deadline_utc = parse_deadline(raw, data["group_tz"])
        except DeadlineParseError as e:
            await message.answer(texts.ADD_DEADLINE_ERROR.format(error=str(e)))
            return
        await state.update_data(deadline_iso=deadline_utc.isoformat())
    await _edit_prompt_links(message, state)


@router.callback_query(EditAssignment.waiting_deadline, F.data == "edit_deadline:skip")
async def edit_deadline_skip(callback: CallbackQuery, state: FSMContext) -> None:
    if isinstance(callback.message, Message):
        await _edit_prompt_links(callback.message, state)
    await callback.answer()


@router.message(EditAssignment.waiting_links, F.text)
async def edit_links(message: Message, state: FSMContext) -> None:
    raw = (message.text or "").strip()
    if raw == _SKIP_TOKEN:
        pass
    elif raw == _CLEAR_TOKEN:
        await state.update_data(links=[])
    else:
        links = parse_links(raw)
        if not links:
            await message.answer(texts.ADD_LINKS_NONE_VALID)
            return
        await state.update_data(links=links)
    await _edit_prompt_confirm(message, state)


@router.callback_query(EditAssignment.waiting_links, F.data.startswith("edit_links:"))
async def edit_links_btn(callback: CallbackQuery, state: FSMContext) -> None:
    if callback.data is None:
        await callback.answer()
        return
    action = callback.data.rsplit(":", 1)[1]
    if action == "clear":
        await state.update_data(links=[])
    if isinstance(callback.message, Message):
        await _edit_prompt_confirm(callback.message, state)
    await callback.answer()


@router.callback_query(EditAssignment.waiting_confirm, F.data == "edit_confirm:no")
async def edit_confirm_no(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    if isinstance(callback.message, Message):
        await callback.message.answer(texts.EDIT_CANCELLED)
    await callback.answer()


@router.callback_query(EditAssignment.waiting_confirm, F.data == "edit_confirm:yes")
async def edit_confirm_yes(callback: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    deadline_utc = datetime.fromisoformat(data["deadline_iso"])
    atype = AssignmentType(data["type"])
    deadline_changed = data["deadline_iso"] != data["orig_deadline_iso"]

    async with SessionLocal() as session, session.begin():
        a = await session.get(Assignment, data["assignment_id"])
        if a is None or not a.is_active:
            await state.clear()
            if isinstance(callback.message, Message):
                await callback.message.answer(texts.DELETE_NOT_FOUND.format(n=data["short_id"]))
            await callback.answer()
            return
        await assn_svc.update_assignment(
            session,
            a,
            assignment_type=atype,
            subject_id=data["subject_id"],
            title=data["title"],
            description=data.get("description"),
            links=data.get("links", []),
            deadline_utc=deadline_utc,
        )
        if deadline_changed:
            await assn_svc.clear_reminder_log(session, a.id)
        updated = a

    cancel_assignment_reminders(updated.id)
    schedule_assignment_reminders(updated)

    await state.clear()
    if isinstance(callback.message, Message):
        await callback.message.answer(texts.EDIT_OK.format(n=data["short_id"]))
    await callback.answer()

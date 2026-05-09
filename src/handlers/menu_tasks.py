from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from src import texts
from src.db.models import Assignment
from src.db.session import SessionLocal
from src.handlers.menu import render
from src.keyboards import ASSIGNMENT_TYPE_LABELS, back_home_kb, with_back_home
from src.services import assignments as assn_svc
from src.services import subjects as subj_svc
from src.services.groups import get_active_group, is_creator
from src.services.users import get_or_create_user
from src.utils import format_deadline_display

router = Router(name="menu_tasks")

_PAGE_SIZE = 8


async def _user_ctx(callback: CallbackQuery | Message):
    u = callback.from_user
    if u is None:
        return None
    async with SessionLocal() as session, session.begin():
        user = await get_or_create_user(
            session, u.id, u.username, u.full_name or u.username or "user"
        )
        group = await get_active_group(session, user)
        if group is None:
            return None
        creator_flag = await is_creator(session, user.id, group.id)
        return user.id, group.id, group.timezone, creator_flag


async def _load_tasks(group_id: int, user_id: int) -> tuple[list[Assignment], list[Assignment], set[int], dict[int, str]]:
    async with SessionLocal() as session:
        active = await assn_svc.list_active(session, group_id)
        passed = await assn_svc.list_passed(session, group_id)
        all_ids = [a.id for a in active] + [a.id for a in passed]
        completed = await assn_svc.completed_ids_for_user(session, user_id, all_ids)
        subjects = await subj_svc.list_subjects(session, group_id)
        subj_by_id = {s.id: s.name for s in subjects}
    return active, passed, completed, subj_by_id


def _row_label(a: Assignment, subj_by_id: dict[int, str], tz_name: str, completed: bool) -> str:
    marker = "✓ " if completed else ""
    type_label = ASSIGNMENT_TYPE_LABELS.get(a.type, a.type.value)
    subject = subj_by_id.get(a.subject_id, "?")
    deadline = format_deadline_display(a.deadline, tz_name)
    return f"{marker}#{a.short_id} [{type_label}] {a.title} — {subject} — {deadline}"


def _short_button_label(a: Assignment, subj_by_id: dict[int, str], completed: bool) -> str:
    marker = "✓ " if completed else ""
    return f"{marker}#{a.short_id} {a.title} ({subj_by_id.get(a.subject_id, '?')})"


@router.callback_query(F.data == "m:tasks")
async def cb_tasks(callback: CallbackQuery) -> None:
    await _render_tasks_page(callback, page=0)


@router.callback_query(F.data.regexp(r"^m:tasks:p:\d+$"))
async def cb_tasks_page(callback: CallbackQuery) -> None:
    if callback.data is None:
        await callback.answer()
        return
    page = int(callback.data.rsplit(":", 1)[1])
    await _render_tasks_page(callback, page=page)


async def open_tasks_page(target: CallbackQuery | Message, *, page: int) -> None:
    await _render_tasks_page(target, page=page)


async def _render_tasks_page(callback: CallbackQuery | Message, *, page: int) -> None:
    ctx = await _user_ctx(callback)
    if ctx is None:
        await render(callback, texts.NO_ACTIVE_GROUP, back_home_kb())
        return
    user_id, group_id, _tz, _creator = ctx
    active, passed, completed, subj_by_id = await _load_tasks(group_id, user_id)
    items: list[tuple[str, Assignment]] = [("a", a) for a in active] + [("p", a) for a in passed]
    if not items:
        await render(callback, texts.LIST_EMPTY, back_home_kb())
        return

    pages = max(1, (len(items) + _PAGE_SIZE - 1) // _PAGE_SIZE)
    page = max(0, min(page, pages - 1))
    chunk = items[page * _PAGE_SIZE : (page + 1) * _PAGE_SIZE]

    rows: list[list[InlineKeyboardButton]] = []
    cur_section = ""
    header_lines: list[str] = []
    for kind, a in chunk:
        if kind != cur_section:
            cur_section = kind
            header_lines.append(
                texts.LIST_ACTIVE_HEADER if kind == "a" else texts.LIST_PASSED_HEADER
            )
        is_done = a.id in completed
        rows.append(
            [
                InlineKeyboardButton(
                    text=_short_button_label(a, subj_by_id, is_done),
                    callback_data=f"m:task:{a.id}",
                )
            ]
        )

    nav: list[InlineKeyboardButton] = []
    if page > 0:
        nav.append(InlineKeyboardButton(text="◀", callback_data=f"m:tasks:p:{page - 1}"))
    nav.append(InlineKeyboardButton(text=f"{page + 1}/{pages}", callback_data="m:tasks:noop"))
    if page < pages - 1:
        nav.append(InlineKeyboardButton(text="▶", callback_data=f"m:tasks:p:{page + 1}"))
    if pages > 1:
        rows.append(nav)

    text = "\n".join(["📋 Задания", *header_lines]) if header_lines else "📋 Задания"
    await render(callback, text, with_back_home(rows))


@router.callback_query(F.data == "m:tasks:noop")
async def cb_tasks_noop(callback: CallbackQuery) -> None:
    await callback.answer()


@router.callback_query(F.data.regexp(r"^m:task:\d+$"))
async def cb_task_view(callback: CallbackQuery) -> None:
    if callback.data is None:
        await callback.answer()
        return
    assignment_id = int(callback.data.split(":")[2])
    await _render_task_card(callback, assignment_id)


async def _render_task_card(callback: CallbackQuery, assignment_id: int) -> None:
    ctx = await _user_ctx(callback)
    if ctx is None:
        await render(callback, texts.NO_ACTIVE_GROUP, back_home_kb())
        return
    user_id, group_id, tz_name, creator_flag = ctx

    async with SessionLocal() as session:
        a = await session.get(Assignment, assignment_id)
        if a is None or a.group_id != group_id:
            await render(callback, texts.DELETE_NOT_FOUND.format(n="?"), back_home_kb())
            return
        subjects = await subj_svc.list_subjects(session, group_id)
        subj_by_id = {s.id: s.name for s in subjects}
        completed = await assn_svc.completed_ids_for_user(session, user_id, [a.id])

    is_done = a.id in completed
    can_modify = (a.created_by_user_id == user_id) or creator_flag
    type_label = ASSIGNMENT_TYPE_LABELS.get(a.type, a.type.value)
    subj_name = subj_by_id.get(a.subject_id, "?")
    deadline = format_deadline_display(a.deadline, tz_name)
    state_str = "✓ выполнено" if is_done else "○ не выполнено"
    active_str = "" if a.is_active else "\n(удалено)"

    lines = [
        f"#{a.short_id} [{type_label}] {a.title}",
        f"Предмет: {subj_name}",
        f"Дедлайн: {deadline}",
        f"Статус: {state_str}{active_str}",
    ]
    if a.description:
        lines.append("")
        lines.append(a.description)
    if a.links:
        lines.append("")
        lines.extend(a.links)

    rows: list[list[InlineKeyboardButton]] = []
    if a.is_active:
        if is_done:
            rows.append(
                [
                    InlineKeyboardButton(
                        text="↺ Снять отметку",
                        callback_data=f"m:task:{a.id}:undone",
                    )
                ]
            )
        else:
            rows.append(
                [
                    InlineKeyboardButton(
                        text="✓ Отметить выполненным",
                        callback_data=f"m:task:{a.id}:done",
                    )
                ]
            )
        if can_modify:
            rows.append(
                [
                    InlineKeyboardButton(
                        text="✏️ Редактировать",
                        callback_data=f"m:task:{a.id}:edit",
                    ),
                    InlineKeyboardButton(
                        text="🗑 Удалить",
                        callback_data=f"m:task:{a.id}:del",
                    ),
                ]
            )
    rows.append([InlineKeyboardButton(text="📋 К списку", callback_data="m:tasks")])

    await render(callback, "\n".join(lines), with_back_home(rows))


@router.callback_query(F.data.regexp(r"^m:task:\d+:done$"))
async def cb_task_done(callback: CallbackQuery) -> None:
    if callback.data is None or callback.from_user is None:
        await callback.answer()
        return
    assignment_id = int(callback.data.split(":")[2])
    ctx = await _user_ctx(callback)
    if ctx is None:
        await callback.answer(texts.NO_ACTIVE_GROUP, show_alert=True)
        return
    user_id, group_id, *_ = ctx
    async with SessionLocal() as session, session.begin():
        a = await session.get(Assignment, assignment_id)
        if a is None or a.group_id != group_id or not a.is_active:
            await callback.answer()
            return
        status = await assn_svc.mark_completed(
            session, group_id=group_id, short_id=a.short_id, user_id=user_id
        )
    if status == "already":
        await callback.answer("Уже отмечено")
    elif status == "ok":
        await callback.answer("✓ Отмечено")
    await _render_task_card(callback, assignment_id)


@router.callback_query(F.data.regexp(r"^m:task:\d+:undone$"))
async def cb_task_undone(callback: CallbackQuery) -> None:
    if callback.data is None or callback.from_user is None:
        await callback.answer()
        return
    assignment_id = int(callback.data.split(":")[2])
    ctx = await _user_ctx(callback)
    if ctx is None:
        await callback.answer(texts.NO_ACTIVE_GROUP, show_alert=True)
        return
    user_id, group_id, *_ = ctx
    async with SessionLocal() as session, session.begin():
        a = await session.get(Assignment, assignment_id)
        if a is None or a.group_id != group_id or not a.is_active:
            await callback.answer()
            return
        status = await assn_svc.unmark_completed(
            session, group_id=group_id, short_id=a.short_id, user_id=user_id
        )
    if status == "not_marked":
        await callback.answer("Отметки не было")
    else:
        await callback.answer("Отметка снята")
    await _render_task_card(callback, assignment_id)


@router.callback_query(F.data.regexp(r"^m:task:\d+:del$"))
async def cb_task_del_ask(callback: CallbackQuery) -> None:
    if callback.data is None:
        await callback.answer()
        return
    assignment_id = int(callback.data.split(":")[2])
    rows = [
        [
            InlineKeyboardButton(text="✓ Удалить", callback_data=f"m:task:{assignment_id}:delyes"),
            InlineKeyboardButton(text="✗ Отмена", callback_data=f"m:task:{assignment_id}"),
        ]
    ]
    await render(
        callback,
        "Удалить задание? Это можно сделать только автору или создателю группы.",
        InlineKeyboardMarkup(inline_keyboard=rows),
    )


@router.callback_query(F.data.regexp(r"^m:task:\d+:delyes$"))
async def cb_task_del_confirm(callback: CallbackQuery) -> None:
    from src.scheduler import cancel_assignment_reminders

    if callback.data is None or callback.from_user is None:
        await callback.answer()
        return
    assignment_id = int(callback.data.split(":")[2])
    ctx = await _user_ctx(callback)
    if ctx is None:
        await callback.answer(texts.NO_ACTIVE_GROUP, show_alert=True)
        return
    user_id, group_id, _tz, creator_flag = ctx
    async with SessionLocal() as session, session.begin():
        a = await session.get(Assignment, assignment_id)
        if a is None or a.group_id != group_id:
            await callback.answer()
            return
        status = await assn_svc.soft_delete(
            session,
            group_id=group_id,
            short_id=a.short_id,
            requesting_user_id=user_id,
            is_creator_of_group=creator_flag,
        )

    if status == "ok":
        cancel_assignment_reminders(assignment_id)
        await callback.answer("Удалено")
        await _render_tasks_page(callback, page=0)
        return
    if status == "forbidden":
        await callback.answer(texts.DELETE_FORBIDDEN, show_alert=True)
    elif status == "already_deleted":
        await callback.answer("Уже удалено")
    else:
        await callback.answer("Не найдено")
    await _render_task_card(callback, assignment_id)


@router.callback_query(F.data.regexp(r"^m:task:\d+:edit$"))
async def cb_task_edit(callback: CallbackQuery, state: FSMContext) -> None:
    if callback.data is None or callback.from_user is None:
        await callback.answer()
        return
    assignment_id = int(callback.data.split(":")[2])
    ctx = await _user_ctx(callback)
    if ctx is None:
        await render(callback, texts.NO_ACTIVE_GROUP, back_home_kb())
        return
    user_id, group_id, _tz, creator_flag = ctx

    async with SessionLocal() as session, session.begin():
        a = await session.get(Assignment, assignment_id)
        if a is None or a.group_id != group_id or not a.is_active:
            await callback.answer(texts.DELETE_NOT_FOUND.format(n="?"), show_alert=True)
            return
        if a.created_by_user_id != user_id and not creator_flag:
            await callback.answer(texts.EDIT_FORBIDDEN, show_alert=True)
            return
        group = await get_active_group_obj(session, user_id)
        if group is None:
            await callback.answer(texts.NO_ACTIVE_GROUP, show_alert=True)
            return
        subjects = await subj_svc.list_subjects(session, group_id)
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
        type_value = a.type.value

    from src.db.models import AssignmentType
    from src.keyboards import assignment_type_kb
    from src.states import EditAssignment

    current_label = ASSIGNMENT_TYPE_LABELS[AssignmentType(type_value)]
    await state.set_state(EditAssignment.waiting_type)
    msg = callback.message
    kb = assignment_type_kb(prefix="edit_type", keep_label=current_label)
    if isinstance(msg, Message):
        try:
            await msg.edit_text(texts.ADD_STEP_TYPE, reply_markup=kb)
        except Exception:
            await msg.answer(texts.ADD_STEP_TYPE, reply_markup=kb)
    await callback.answer()


async def get_active_group_obj(session, user_id: int):
    from sqlalchemy import select

    from src.db.models import Group, User
    user = await session.get(User, user_id)
    if user is None or user.active_group_id is None:
        return None
    return (
        await session.execute(select(Group).where(Group.id == user.active_group_id))
    ).scalar_one_or_none()

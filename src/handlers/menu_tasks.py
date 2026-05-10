from datetime import UTC, datetime

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
from src.utils import ensure_utc, format_deadline_display

router = Router(name="menu_tasks")


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


async def _load_tasks(
    group_id: int,
) -> tuple[list[Assignment], list[Assignment], dict[int, str]]:
    async with SessionLocal() as session:
        active = await assn_svc.list_active(session, group_id)
        passed = await assn_svc.list_passed(session, group_id)
        subjects = await subj_svc.list_subjects(session, group_id)
        subj_by_id = {s.id: s.name for s in subjects}
    return active, passed, subj_by_id


@router.callback_query(F.data == "m:tasks")
async def cb_tasks(callback: CallbackQuery) -> None:
    await _render_tasks(callback)


async def open_tasks_page(target: CallbackQuery | Message, *, page: int = 0) -> None:
    # page оставлен для совместимости с reply-keyboard роутером.
    _ = page
    await _render_tasks(target)


_MD2_SPECIAL = r"_*[]()~`>#+-=|{}.!\\"


def _md2(s: str) -> str:
    return "".join("\\" + c if c in _MD2_SPECIAL else c for c in s)


def _short_deadline(a: Assignment, tz_name: str) -> str:
    if a.deadline is None:
        return "до конца модуля"
    from zoneinfo import ZoneInfo
    local = ensure_utc(a.deadline).astimezone(ZoneInfo(tz_name))
    return f"{local.day:02d}.{local.month:02d} {local.hour:02d}:{local.minute:02d}"


async def _send_md2(target: CallbackQuery | Message, text: str, kb) -> None:
    if isinstance(target, CallbackQuery):
        msg = target.message
        if isinstance(msg, Message):
            try:
                await msg.edit_text(text, reply_markup=kb, parse_mode="MarkdownV2")
            except Exception:
                await msg.answer(text, reply_markup=kb, parse_mode="MarkdownV2")
        await target.answer()
        return
    await target.answer(text, reply_markup=kb, parse_mode="MarkdownV2")


async def _render_tasks(callback: CallbackQuery | Message) -> None:
    ctx = await _user_ctx(callback)
    if ctx is None:
        await render(callback, texts.NO_ACTIVE_GROUP, back_home_kb())
        return
    _user_id, group_id, tz_name, _creator = ctx
    active, passed, subj_by_id = await _load_tasks(group_id)

    if not active and not passed:
        await render(callback, texts.LIST_EMPTY, back_home_kb())
        return

    INF = datetime.max.replace(tzinfo=UTC)

    def _akey(a: Assignment) -> datetime:
        return ensure_utc(a.deadline) if a.deadline is not None else INF

    # Группируем active и passed по subject_id.
    by_subject_active: dict[int, list[Assignment]] = {}
    for a in active:
        by_subject_active.setdefault(a.subject_id, []).append(a)
    for lst in by_subject_active.values():
        lst.sort(key=_akey)

    by_subject_passed: dict[int, list[Assignment]] = {}
    for a in passed:
        by_subject_passed.setdefault(a.subject_id, []).append(a)
    for lst in by_subject_passed.values():
        lst.sort(key=lambda a: ensure_utc(a.deadline), reverse=True)

    subject_ids = sorted(
        set(by_subject_active.keys()) | set(by_subject_passed.keys()),
        key=lambda sid: (
            _akey(by_subject_active[sid][0]) if sid in by_subject_active else INF,
            subj_by_id.get(sid, ""),
        ),
    )

    text_lines: list[str] = ["📋 *Задания*"]
    rows: list[list[InlineKeyboardButton]] = []

    # Жёсткие лимиты Telegram: 4096 символов в сообщении, ~100 inline-кнопок.
    # Берём с запасом и при превышении печатаем хвост текстом без кнопок.
    MAX_BUTTONS = 90
    MAX_TEXT = 3800
    truncated_buttons = 0

    def _fits() -> bool:
        return len(rows) < MAX_BUTTONS and sum(len(line) + 1 for line in text_lines) < MAX_TEXT

    for sid in subject_ids:
        name = subj_by_id.get(sid, "?")
        actives = by_subject_active.get(sid, [])
        passeds = by_subject_passed.get(sid, [])
        if not _fits():
            truncated_buttons += len(actives) + len(passeds)
            continue
        text_lines.append("")
        text_lines.append(f"*{_md2(name)}*")

        for i, a in enumerate(actives, 1):
            if not _fits():
                truncated_buttons += 1
                continue
            dl = _short_deadline(a, tz_name)
            text_lines.append(f"{i}\\. {_md2(a.title)} — {_md2(dl)}")
            rows.append(
                [
                    InlineKeyboardButton(
                        text=f"{i}. {a.title}",
                        callback_data=f"m:task:{a.id}",
                    )
                ]
            )

        if passeds:
            text_lines.append("_Прошедшие:_")
            for i, a in enumerate(passeds, 1):
                if not _fits():
                    truncated_buttons += 1
                    continue
                dl = _short_deadline(a, tz_name)
                text_lines.append(f"П{i}\\. {_md2(a.title)} — {_md2(dl)}")
                rows.append(
                    [
                        InlineKeyboardButton(
                            text=f"П{i}. {a.title}",
                            callback_data=f"m:task:{a.id}",
                        )
                    ]
                )

    if truncated_buttons:
        text_lines.append("")
        text_lines.append(f"_…и ещё {truncated_buttons} \\(используй /digest\\)_")

    rows.append([InlineKeyboardButton(text=texts.DIGEST_BTN, callback_data="m:tasks:digest")])
    await _send_md2(callback, "\n".join(text_lines), with_back_home(rows))


@router.callback_query(F.data == "m:tasks:digest")
async def cb_tasks_digest(callback: CallbackQuery) -> None:
    from src.db.models import Group as _G
    from src.services.digest import build_digest_messages

    ctx = await _user_ctx(callback)
    if ctx is None:
        await render(callback, texts.NO_ACTIVE_GROUP, back_home_kb())
        return
    _user_id, group_id, *_ = ctx
    async with SessionLocal() as session:
        group = await session.get(_G, group_id)
        if group is None:
            await render(callback, texts.NO_ACTIVE_GROUP, back_home_kb())
            return
        messages = await build_digest_messages(session, group)

    if not messages:
        await render(callback, texts.DIGEST_EMPTY, back_home_kb())
        return
    await render(callback, messages[0], back_home_kb())
    msg = callback.message
    if isinstance(msg, Message):
        for chunk in messages[1:]:
            await msg.answer(chunk)


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

    can_modify = (a.created_by_user_id == user_id) or creator_flag
    type_label = ASSIGNMENT_TYPE_LABELS.get(a.type, a.type.value)
    subj_name = subj_by_id.get(a.subject_id, "?")
    deadline = format_deadline_display(a.deadline, tz_name)
    active_str = "" if a.is_active else "\n(удалено)"

    lines = [
        f"#{a.short_id} [{type_label}] {a.title}",
        f"Предмет: {subj_name}",
        f"Дедлайн: {deadline}{active_str}",
    ]
    if a.description:
        lines.append("")
        lines.append(a.description)
    if a.links:
        lines.append("")
        lines.extend(a.links)

    rows: list[list[InlineKeyboardButton]] = []
    if a.is_active and can_modify:
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
        if a.deadline is None:
            rows.append(
                [
                    InlineKeyboardButton(
                        text="📦 Закрыть как прошедшее",
                        callback_data=f"m:task:{a.id}:close",
                    )
                ]
            )
    rows.append([InlineKeyboardButton(text="📋 К списку", callback_data="m:tasks")])

    await render(callback, "\n".join(lines), with_back_home(rows))


@router.callback_query(F.data.regexp(r"^m:task:\d+:del$"))
async def cb_task_del_ask(callback: CallbackQuery) -> None:
    if callback.data is None:
        await callback.answer()
        return
    assignment_id = int(callback.data.split(":")[2])
    rows = [
        [
            InlineKeyboardButton(text="✅ Удалить", callback_data=f"m:task:{assignment_id}:delyes"),
            InlineKeyboardButton(text="❌ Отмена", callback_data=f"m:task:{assignment_id}"),
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
        await _render_tasks(callback)
        return
    if status == "forbidden":
        await callback.answer(texts.DELETE_FORBIDDEN, show_alert=True)
    elif status == "already_deleted":
        await callback.answer("Уже удалено")
    else:
        await callback.answer("Не найдено")
    await _render_task_card(callback, assignment_id)


@router.callback_query(F.data.regexp(r"^m:task:\d+:close$"))
async def cb_task_close_open_deadline(callback: CallbackQuery) -> None:
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
        status = await assn_svc.archive_to_passed(
            session,
            group_id=group_id,
            short_id=a.short_id,
            requesting_user_id=user_id,
            is_creator_of_group=creator_flag,
        )

    if status == "forbidden":
        await callback.answer(texts.DELETE_FORBIDDEN, show_alert=True)
    elif status == "has_deadline":
        await callback.answer("У задания уже есть дедлайн")
    elif status == "not_found":
        await callback.answer("Не найдено")
    else:
        await callback.answer("Перенесено в прошедшие")
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
            deadline_iso=a.deadline.isoformat() if a.deadline is not None else None,
            orig_deadline_iso=a.deadline.isoformat() if a.deadline is not None else None,
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

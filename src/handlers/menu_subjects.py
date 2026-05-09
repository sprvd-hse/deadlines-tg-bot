from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, Message

from src import texts
from src.db.session import SessionLocal
from src.handlers.menu import render
from src.keyboards import back_home_kb, with_back_home
from src.services import subjects as subj_svc
from src.services.groups import get_active_group
from src.services.users import get_or_create_user
from src.states import Subjects

router = Router(name="menu_subjects")

_SUBJECT_NAME_MAX = 128


async def _active_group_id(callback_or_message: CallbackQuery | Message) -> int | None:
    u = callback_or_message.from_user
    if u is None:
        return None
    async with SessionLocal() as session, session.begin():
        user = await get_or_create_user(
            session, u.id, u.username, u.full_name or u.username or "user"
        )
        group = await get_active_group(session, user)
        return group.id if group is not None else None


async def open_subjects(target: CallbackQuery | Message) -> None:
    await _render_subjects(target)


async def _render_subjects(target: CallbackQuery | Message) -> None:
    group_id = await _active_group_id(target)
    if group_id is None:
        if isinstance(target, CallbackQuery):
            await render(target, texts.NO_ACTIVE_GROUP, back_home_kb())
        else:
            await target.answer(texts.NO_ACTIVE_GROUP)
        return

    async with SessionLocal() as session:
        subjects = await subj_svc.list_subjects(session, group_id)

    rows: list[list[InlineKeyboardButton]] = []
    for s in subjects:
        rows.append(
            [
                InlineKeyboardButton(text=s.name, callback_data="m:subj:noop"),
                InlineKeyboardButton(text="🗑 Удалить", callback_data=f"m:subj:del:{s.id}"),
            ]
        )
    rows.append([InlineKeyboardButton(text="➕ Добавить предмет", callback_data="m:subj:add")])

    text = texts.SUBJECTS_HEADER if subjects else texts.SUBJECTS_EMPTY
    if isinstance(target, CallbackQuery):
        await render(target, text, with_back_home(rows))
    else:
        await target.answer(text, reply_markup=with_back_home(rows))


@router.callback_query(F.data == "m:subj")
async def cb_subjects(callback: CallbackQuery, state: FSMContext) -> None:
    if await state.get_state() is not None:
        await state.clear()
    await _render_subjects(callback)


@router.callback_query(F.data == "m:subj:noop")
async def cb_subjects_noop(callback: CallbackQuery) -> None:
    await callback.answer()


@router.callback_query(F.data == "m:subj:add")
async def cb_subject_add(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(Subjects.waiting_name)
    await render(callback, "Введи название предмета:", back_home_kb())


@router.message(Subjects.waiting_name, F.text)
async def msg_subject_name(message: Message, state: FSMContext) -> None:
    name = (message.text or "").strip()
    if not name:
        await message.answer("Название не может быть пустым. Попробуй ещё раз или жми /menu.")
        return
    if len(name) > _SUBJECT_NAME_MAX:
        await message.answer(texts.GROUP_NAME_TOO_LONG)
        return

    group_id = await _active_group_id(message)
    if group_id is None:
        await state.clear()
        await message.answer(texts.NO_ACTIVE_GROUP)
        return

    async with SessionLocal() as session, session.begin():
        subject = await subj_svc.add_subject(session, group_id, name)

    await state.clear()
    if subject is None:
        await message.answer(texts.SUBJECT_ALREADY_EXISTS.format(name=name))
    else:
        await message.answer(texts.SUBJECT_ADDED.format(name=subject.name))
    # Сразу показываем актуальный список предметов.
    await _render_subjects(message)


@router.callback_query(F.data.regexp(r"^m:subj:del:\d+$"))
async def cb_subject_del_ask(callback: CallbackQuery) -> None:
    if callback.data is None:
        await callback.answer()
        return
    sid = int(callback.data.rsplit(":", 1)[1])
    group_id = await _active_group_id(callback)
    if group_id is None:
        await render(callback, texts.NO_ACTIVE_GROUP, back_home_kb())
        return
    async with SessionLocal() as session:
        subjects = await subj_svc.list_subjects(session, group_id)
    subject = next((s for s in subjects if s.id == sid), None)
    if subject is None:
        await callback.answer("Предмет не найден")
        await _render_subjects(callback)
        return
    rows = [
        [
            InlineKeyboardButton(
                text="✅ Удалить", callback_data=f"m:subj:delyes:{sid}"
            ),
            InlineKeyboardButton(text="❌ Отмена", callback_data="m:subj"),
        ]
    ]
    await render(
        callback,
        f"Удалить предмет «{subject.name}»? Если на нём есть активные задания — удалить нельзя.",
        with_back_home(rows),
    )


@router.callback_query(F.data.regexp(r"^m:subj:delyes:\d+$"))
async def cb_subject_del_confirm(callback: CallbackQuery) -> None:
    if callback.data is None:
        await callback.answer()
        return
    sid = int(callback.data.rsplit(":", 1)[1])
    group_id = await _active_group_id(callback)
    if group_id is None:
        await render(callback, texts.NO_ACTIVE_GROUP, back_home_kb())
        return

    async with SessionLocal() as session, session.begin():
        subjects = await subj_svc.list_subjects(session, group_id)
        subject = next((s for s in subjects if s.id == sid), None)
        if subject is None:
            await callback.answer("Предмет не найден")
            await _render_subjects(callback)
            return
        status = await subj_svc.delete_subject(session, group_id, subject.name)

    if status == "has_assignments":
        await callback.answer(
            texts.SUBJECT_HAS_ASSIGNMENTS.format(name=subject.name), show_alert=True
        )
    elif status == "not_found":
        await callback.answer("Предмет не найден")
    else:
        await callback.answer("Удалено")
    await _render_subjects(callback)

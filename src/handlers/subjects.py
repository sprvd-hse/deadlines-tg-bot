from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from src.db.session import SessionLocal
from src.handlers.ui import Event, show, with_group
from src.services.groups import GroupManager
from src.states import SubjectInput
from src.ui import keyboards as kb
from src.ui.callbacks import Nav, SubjectAction
from src.views import texts as t
from src.views.common import esc

router = Router(name="subjects")
router.message.filter(F.chat.type == "private")


@router.callback_query(Nav.filter(F.to == "subjects"))
async def open_subjects(event: Event, state: FSMContext, *args, **kwargs) -> None:
    """Экран предметов группы."""
    await state.clear()
    if isinstance(event, CallbackQuery):
        await event.answer()
    await render_subjects(event)


async def render_subjects(event: Event) -> None:
    """Рисует список предметов."""
    ctx = await with_group(event)
    if ctx is None:
        return
    async with SessionLocal() as session:
        subjects = await GroupManager(session).subjects(ctx.group.id)
    header = t.SUBJECTS_HEADER.format(group=esc(ctx.group.name))
    body = header if subjects else f"{header}\n\n{t.SUBJECTS_EMPTY}"
    await show(event, body, kb.subjects_manage_keyboard(subjects))


@router.callback_query(SubjectAction.filter(F.action == "add"))
async def ask_subject_name(callback: CallbackQuery, state: FSMContext) -> None:
    """Просит название предмета."""
    await state.set_state(SubjectInput.name)
    await callback.answer()
    await show(callback, t.SUBJECT_ASK_NAME)


@router.message(SubjectInput.name, F.text)
async def add_subject(message: Message, state: FSMContext) -> None:
    """Создаёт предмет."""
    ctx = await with_group(message)
    if ctx is None:
        return
    async with SessionLocal() as session, session.begin():
        subject, _ = await GroupManager(session).add_subject(
            group_id=ctx.group.id, name=message.text or ""
        )
    if subject is None:
        await message.answer(t.SUBJECT_DUPLICATE)
        return
    await state.clear()
    await message.answer(t.SUBJECT_ADDED.format(name=subject.name))
    await render_subjects(message)


@router.callback_query(SubjectAction.filter(F.action == "del"))
async def delete_subject(callback: CallbackQuery, callback_data: SubjectAction) -> None:
    """Удаляет предмет, если на нём нет активных записей."""
    ctx = await with_group(callback)
    if ctx is None:
        return
    async with SessionLocal() as session, session.begin():
        status = await GroupManager(session).delete_subject(callback_data.subject_id)
    if status == "has_items":
        await callback.answer(t.SUBJECT_HAS_ITEMS, show_alert=True)
        return
    await callback.answer(t.SUBJECT_DELETED)
    await render_subjects(callback)

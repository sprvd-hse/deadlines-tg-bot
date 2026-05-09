from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from src import texts
from src.db.session import SessionLocal
from src.handlers.menu import render
from src.keyboards import assignment_type_kb, back_home_kb
from src.services import subjects as subj_svc
from src.services.groups import get_active_group
from src.services.users import get_or_create_user
from src.states import AddAssignment

router = Router(name="menu_add")


@router.callback_query(F.data == "m:add")
async def cb_add(callback: CallbackQuery, state: FSMContext) -> None:
    u = callback.from_user
    if u is None:
        await callback.answer()
        return
    async with SessionLocal() as session, session.begin():
        user = await get_or_create_user(
            session, u.id, u.username, u.full_name or u.username or "user"
        )
        group = await get_active_group(session, user)
        if group is None:
            await render(callback, texts.NO_ACTIVE_GROUP, back_home_kb())
            return
        subjects = await subj_svc.list_subjects(session, group.id)
        if not subjects:
            await render(callback, texts.ADD_NEED_SUBJECTS, back_home_kb())
            return
        await state.update_data(group_id=group.id, group_tz=group.timezone)

    await state.set_state(AddAssignment.waiting_type)
    # Дальше ввод идёт обычными FSM-хендлерами в src.handlers.assignments
    # (assign_type:* / assign_subj:* / текстовые поля / assign_confirm:yes|no).
    msg = callback.message
    if isinstance(msg, Message):
        try:
            await msg.edit_text(texts.ADD_STEP_TYPE, reply_markup=assignment_type_kb())
        except Exception:
            await msg.answer(texts.ADD_STEP_TYPE, reply_markup=assignment_type_kb())
    await callback.answer()

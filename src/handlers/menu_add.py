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


async def open_add(target: CallbackQuery | Message, state: FSMContext) -> None:
    u = target.from_user
    if u is None:
        return
    async with SessionLocal() as session, session.begin():
        user = await get_or_create_user(
            session, u.id, u.username, u.full_name or u.username or "user"
        )
        group = await get_active_group(session, user)
        if group is None:
            await render(target, texts.NO_ACTIVE_GROUP, back_home_kb())
            return
        subjects = await subj_svc.list_subjects(session, group.id)
        if not subjects:
            await render(target, texts.ADD_NEED_SUBJECTS, back_home_kb())
            return
        await state.update_data(group_id=group.id, group_tz=group.timezone)

    await state.set_state(AddAssignment.waiting_type)
    # Дальше ввод идёт FSM-хендлерами в src.handlers.assignments.
    await render(target, texts.ADD_STEP_TYPE, assignment_type_kb())


@router.callback_query(F.data == "m:add")
async def cb_add(callback: CallbackQuery, state: FSMContext) -> None:
    await open_add(callback, state)

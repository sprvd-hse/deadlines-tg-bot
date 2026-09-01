from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

from src.handlers.groupctl import render_group
from src.handlers.items import render_tasks, start_add
from src.handlers.menu import open_home
from src.handlers.settings import render_settings
from src.handlers.subjects import render_subjects
from src.handlers.ui import show
from src.ui import keyboards as kb
from src.views.texts import HELP

router = Router(name="reply")
router.message.filter(F.chat.type == "private")


@router.message(F.text == kb.BTN_TASKS)
async def btn_tasks(message: Message, state: FSMContext) -> None:
    """Кнопка «Задания»."""
    await state.clear()
    await render_tasks(message)


@router.message(F.text == kb.BTN_ADD)
async def btn_add(message: Message, state: FSMContext) -> None:
    """Кнопка «Добавить»."""
    await start_add(message, state)


@router.message(F.text == kb.BTN_SUBJECTS)
async def btn_subjects(message: Message, state: FSMContext) -> None:
    """Кнопка «Предметы»."""
    await state.clear()
    await render_subjects(message)


@router.message(F.text == kb.BTN_GROUP)
async def btn_group(message: Message, state: FSMContext) -> None:
    """Кнопка «Группа»."""
    await state.clear()
    await render_group(message)


@router.message(F.text == kb.BTN_SETTINGS)
async def btn_settings(message: Message, state: FSMContext) -> None:
    """Кнопка «Настройки»."""
    await state.clear()
    await render_settings(message)


@router.message(F.text == kb.BTN_HELP)
async def btn_help(message: Message, state: FSMContext) -> None:
    """Кнопка «Помощь»."""
    await state.clear()
    await show(message, HELP, kb.back_keyboard("home"))
    await open_home(message)

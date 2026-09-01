from aiogram import F, Router
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from src.handlers.ui import Event, load_context, notify, show
from src.ui import keyboards as kb
from src.ui.callbacks import NOOP, Nav
from src.views.texts import (
    CANCELLED,
    HELP,
    MENU_HOME,
    MENU_HOME_NO_GROUP,
    NOTHING_TO_CANCEL,
    START,
)

router = Router(name="menu")
router.message.filter(F.chat.type == "private")


@router.message(CommandStart(deep_link=True))
async def cmd_start_deep_link(message: Message, command: CommandObject, state: FSMContext) -> None:
    """Вход в группу по ссылке-приглашению."""
    from src.handlers.groupctl import join_by_payload

    await state.clear()
    await message.answer(START, reply_markup=kb.home_reply(), parse_mode="HTML")
    if not await join_by_payload(message, state, command.args or ""):
        await open_home(message)


@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext) -> None:
    """Приветствие и постоянная клавиатура."""
    await state.clear()
    await message.answer(START, reply_markup=kb.home_reply(), parse_mode="HTML")
    await open_home(message)


@router.message(Command("menu"))
async def cmd_menu(message: Message, state: FSMContext) -> None:
    """Главное меню."""
    await state.clear()
    await message.answer("Меню", reply_markup=kb.home_reply())
    await open_home(message)


@router.message(Command("help"))
@router.callback_query(Nav.filter(F.to == "help"))
async def cmd_help(event: Event, *args, **kwargs) -> None:
    """Справка."""
    await show(event, HELP, kb.back_keyboard("home"))


@router.message(Command("cancel"))
async def cmd_cancel(message: Message, state: FSMContext) -> None:
    """Прерывает текущий диалог."""
    if await state.get_state() is None:
        await message.answer(NOTHING_TO_CANCEL)
        return
    await state.clear()
    await message.answer(CANCELLED)
    await open_home(message)


@router.callback_query(Nav.filter(F.to == "home"))
async def cb_home(callback: CallbackQuery, state: FSMContext) -> None:
    """Возврат в главное меню."""
    await state.clear()
    await callback.answer()
    await open_home(callback)


@router.callback_query(F.data == NOOP)
async def cb_noop(callback: CallbackQuery) -> None:
    """Неактивная кнопка."""
    await callback.answer()


async def open_home(event: Event) -> None:
    """Рисует главное меню с учётом активной группы."""
    ctx = await load_context(event)
    if ctx is None:
        await notify(event, MENU_HOME_NO_GROUP)
        return
    active = ctx.activated()
    text = MENU_HOME.format(group=active.group.name) if active else MENU_HOME_NO_GROUP
    await show(event, text)

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message

from src import texts
from src.db.session import SessionLocal
from src.keyboards import back_home_kb, home_kb
from src.services.groups import get_active_group, is_creator
from src.services.users import get_or_create_user

router = Router(name="menu")


async def render(
    target: Message | CallbackQuery,
    text: str,
    kb: InlineKeyboardMarkup,
) -> None:
    """Универсальный рендер: на Message — отправляет, на CallbackQuery — правит то же сообщение."""
    if isinstance(target, CallbackQuery):
        msg = target.message
        if isinstance(msg, Message):
            try:
                await msg.edit_text(text, reply_markup=kb)
            except Exception:
                # Если не получилось отредактировать (старое сообщение и т.п.) — отправим новое.
                await msg.answer(text, reply_markup=kb)
        await target.answer()
        return
    await target.answer(text, reply_markup=kb)


async def _home_state(tg_user_id: int, tg_username: str | None, full_name: str) -> tuple[bool, bool, str]:
    """Возвращает (has_active_group, is_creator, group_name_or_empty)."""
    async with SessionLocal() as session, session.begin():
        user = await get_or_create_user(session, tg_user_id, tg_username, full_name)
        group = await get_active_group(session, user)
        if group is None:
            return False, False, ""
        creator = await is_creator(session, user.id, group.id)
        return True, creator, group.name


def _home_text(has_group: bool, group_name: str) -> str:
    if not has_group:
        return texts.MENU_HOME_NO_GROUP
    return texts.MENU_HOME.format(group=group_name)


async def open_home(target: Message | CallbackQuery, state: FSMContext | None = None) -> None:
    # При входе в главное меню сбрасываем зависший FSM (если был).
    if state is not None and await state.get_state() is not None:
        await state.clear()
    user = target.from_user
    if user is None:
        return
    has_group, creator, group_name = await _home_state(
        user.id, user.username, user.full_name or user.username or "user"
    )
    await render(
        target,
        _home_text(has_group, group_name),
        home_kb(has_active_group=has_group, is_creator=creator),
    )


@router.message(Command("menu"))
async def cmd_menu(message: Message, state: FSMContext) -> None:
    await open_home(message, state)


@router.callback_query(F.data == "m:home")
async def cb_home(callback: CallbackQuery, state: FSMContext) -> None:
    await open_home(callback, state)


@router.callback_query(F.data == "m:help")
async def cb_help(callback: CallbackQuery) -> None:
    await render(callback, texts.HELP, back_home_kb())


# Заглушки на остальные секции — заполним в следующих этапах.
_STUB_SECTIONS = {
    "m:add": "➕ Добавление — пока через команду /add.",
    "m:adm": "🛠 Админ группы — раздел в разработке.",
}


@router.callback_query(F.data.in_(set(_STUB_SECTIONS.keys())))
async def cb_stub(callback: CallbackQuery) -> None:
    text = _STUB_SECTIONS.get(callback.data or "", "В разработке.")
    await render(callback, text, back_home_kb())

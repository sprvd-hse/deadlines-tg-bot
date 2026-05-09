from aiogram import F, Router
from aiogram.types import CallbackQuery, InlineKeyboardButton, Message

from src import texts
from src.db.session import SessionLocal
from src.handlers.menu import render
from src.keyboards import back_home_kb, with_back_home
from src.services.groups import get_active_group, get_member, set_personal_reminders
from src.services.users import get_or_create_user

router = Router(name="menu_settings")


async def open_settings(target: CallbackQuery | Message) -> None:
    await _render_settings(target)


async def _render_settings(callback: CallbackQuery | Message) -> None:
    u = callback.from_user
    if u is None:
        return
    async with SessionLocal() as session, session.begin():
        user = await get_or_create_user(
            session, u.id, u.username, u.full_name or u.username or "user"
        )
        group = await get_active_group(session, user)
        if group is None:
            await render(callback, texts.NO_ACTIVE_GROUP, back_home_kb())
            return
        member = await get_member(session, group_id=group.id, user_id=user.id)
        if member is None:
            await render(callback, texts.NOT_A_MEMBER, back_home_kb())
            return
        enabled = member.personal_reminders_enabled
        group_name = group.name

    state_str = texts.SETTINGS_REMINDERS_ON if enabled else texts.SETTINGS_REMINDERS_OFF
    label = texts.SETTINGS_TOGGLE_OFF if enabled else texts.SETTINGS_TOGGLE_ON
    new_value = "0" if enabled else "1"
    rows = [[InlineKeyboardButton(text=label, callback_data=f"m:set:pr:{new_value}")]]
    await render(
        callback,
        texts.SETTINGS_HEADER.format(group=group_name, state=state_str),
        with_back_home(rows),
    )


@router.callback_query(F.data == "m:set")
async def cb_settings(callback: CallbackQuery) -> None:
    await _render_settings(callback)


@router.callback_query(F.data.regexp(r"^m:set:pr:[01]$"))
async def cb_settings_toggle(callback: CallbackQuery) -> None:
    if callback.data is None or callback.from_user is None:
        await callback.answer()
        return
    new_value = callback.data.rsplit(":", 1)[1] == "1"
    u = callback.from_user
    async with SessionLocal() as session, session.begin():
        user = await get_or_create_user(
            session, u.id, u.username, u.full_name or u.username or "user"
        )
        group = await get_active_group(session, user)
        if group is None:
            await callback.answer(texts.NO_ACTIVE_GROUP, show_alert=True)
            return
        await set_personal_reminders(
            session, group_id=group.id, user_id=user.id, enabled=new_value
        )
    await callback.answer(texts.SETTINGS_UPDATED)
    await _render_settings(callback)

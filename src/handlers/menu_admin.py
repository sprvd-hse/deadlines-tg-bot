from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, Message
from sqlalchemy import select

from src import texts
from src.db.models import Assignment, Group, MemberRole
from src.db.session import SessionLocal
from src.handlers.menu import open_home, render
from src.keyboards import back_home_kb, with_back_home
from src.scheduler import cancel_assignment_reminders
from src.services.groups import (
    delete_group,
    get_active_group,
    is_creator,
    list_members,
    regenerate_invite_code,
    transfer_ownership,
)
from src.services.users import get_or_create_user
from src.states import GroupSettings

router = Router(name="menu_admin")

_WEEKDAYS_RU = ["понедельник", "вторник", "среда", "четверг", "пятница", "суббота", "воскресенье"]


async def _ensure_creator(callback: CallbackQuery) -> tuple[int, Group] | None:
    u = callback.from_user
    if u is None:
        return None
    async with SessionLocal() as session, session.begin():
        user = await get_or_create_user(
            session, u.id, u.username, u.full_name or u.username or "user"
        )
        group = await get_active_group(session, user)
        if group is None:
            await render(callback, texts.NO_ACTIVE_GROUP, back_home_kb())
            return None
        if not await is_creator(session, user.id, group.id):
            await callback.answer(texts.CREATOR_ONLY, show_alert=True)
            return None
        return user.id, group


# ============ Хаб админа ============


@router.callback_query(F.data == "m:adm")
async def cb_admin_hub(callback: CallbackQuery, state: FSMContext) -> None:
    if await state.get_state() is not None:
        await state.clear()
    res = await _ensure_creator(callback)
    if res is None:
        return
    _, group = res
    text = texts.GROUP_SETTINGS_HEADER.format(
        group=group.name,
        tz=group.timezone,
        weekday=_WEEKDAYS_RU[group.digest_day],
        hour=group.digest_hour,
    )
    chat_state = "не привязан" if group.chat_id is None else f"chat_id={group.chat_id}"
    text += f"\nГрупповой чат: {chat_state}"
    rows = [
        [InlineKeyboardButton(text="🌐 TZ", callback_data="m:adm:tz")],
        [InlineKeyboardButton(text="📅 День дайджеста", callback_data="m:adm:day")],
        [InlineKeyboardButton(text="🕒 Час дайджеста", callback_data="m:adm:hour")],
        [InlineKeyboardButton(text="🔄 Перевыпустить invite", callback_data="m:adm:regen")],
        [InlineKeyboardButton(text="👑 Передать роль creator", callback_data="m:adm:transfer")],
        [InlineKeyboardButton(text="🗑 Удалить группу", callback_data="m:adm:del")],
        [InlineKeyboardButton(text="🔗 Как привязать чат?", callback_data="m:adm:bindhelp")],
    ]
    await render(callback, text, with_back_home(rows))


# ============ TZ ============


@router.callback_query(F.data == "m:adm:tz")
async def cb_admin_tz(callback: CallbackQuery, state: FSMContext) -> None:
    res = await _ensure_creator(callback)
    if res is None:
        return
    _, group = res
    await state.update_data(group_id=group.id)
    await state.set_state(GroupSettings.waiting_tz)
    await render(callback, texts.GROUP_SETTINGS_TZ_PROMPT, back_home_kb())


@router.message(GroupSettings.waiting_tz, F.text)
async def msg_admin_tz(message: Message, state: FSMContext) -> None:
    raw = (message.text or "").strip()
    try:
        ZoneInfo(raw)
    except (ZoneInfoNotFoundError, ValueError):
        await message.answer(texts.GROUP_SETTINGS_TZ_INVALID)
        return
    data = await state.get_data()
    group_id = data["group_id"]
    async with SessionLocal() as session, session.begin():
        g = await session.get(Group, group_id)
        if g is not None:
            g.timezone = raw
    await state.clear()
    await message.answer(texts.GROUP_SETTINGS_TZ_OK.format(tz=raw))
    await open_home(message)


# ============ День дайджеста ============


@router.callback_query(F.data == "m:adm:day")
async def cb_admin_day(callback: CallbackQuery) -> None:
    res = await _ensure_creator(callback)
    if res is None:
        return
    rows = [
        [InlineKeyboardButton(text=_WEEKDAYS_RU[i].capitalize(), callback_data=f"m:adm:day:{i}")]
        for i in range(7)
    ]
    await render(callback, texts.GROUP_SETTINGS_DAY_PROMPT, with_back_home(rows))


@router.callback_query(F.data.regexp(r"^m:adm:day:[0-6]$"))
async def cb_admin_day_pick(callback: CallbackQuery, state: FSMContext) -> None:
    if callback.data is None:
        await callback.answer()
        return
    day = int(callback.data.rsplit(":", 1)[1])
    res = await _ensure_creator(callback)
    if res is None:
        return
    _, group = res
    async with SessionLocal() as session, session.begin():
        g = await session.get(Group, group.id)
        if g is not None:
            g.digest_day = day
    await callback.answer(
        texts.GROUP_SETTINGS_DAY_OK.format(weekday=_WEEKDAYS_RU[day])
    )
    await cb_admin_hub(callback, state)


# ============ Час дайджеста ============


@router.callback_query(F.data == "m:adm:hour")
async def cb_admin_hour(callback: CallbackQuery) -> None:
    res = await _ensure_creator(callback)
    if res is None:
        return
    rows: list[list[InlineKeyboardButton]] = []
    for start in range(0, 24, 6):
        rows.append(
            [
                InlineKeyboardButton(text=f"{h:02d}", callback_data=f"m:adm:hour:{h}")
                for h in range(start, start + 6)
            ]
        )
    await render(callback, texts.GROUP_SETTINGS_HOUR_PROMPT, with_back_home(rows))


@router.callback_query(F.data.regexp(r"^m:adm:hour:\d{1,2}$"))
async def cb_admin_hour_pick(callback: CallbackQuery, state: FSMContext) -> None:
    if callback.data is None:
        await callback.answer()
        return
    hour = int(callback.data.rsplit(":", 1)[1])
    if not 0 <= hour <= 23:
        await callback.answer()
        return
    res = await _ensure_creator(callback)
    if res is None:
        return
    _, group = res
    async with SessionLocal() as session, session.begin():
        g = await session.get(Group, group.id)
        if g is not None:
            g.digest_hour = hour
    await callback.answer(texts.GROUP_SETTINGS_HOUR_OK.format(hour=hour))
    await cb_admin_hub(callback, state)


# ============ Перевыпуск invite-кода ============


@router.callback_query(F.data == "m:adm:regen")
async def cb_admin_regen(callback: CallbackQuery) -> None:
    res = await _ensure_creator(callback)
    if res is None:
        return
    _, group = res
    async with SessionLocal() as session, session.begin():
        g = await session.get(Group, group.id)
        if g is None:
            await callback.answer()
            return
        new_code = await regenerate_invite_code(session, g)
    await render(callback, texts.REGENERATE_CODE_OK.format(code=new_code), back_home_kb())


# ============ Передача роли creator ============


def _member_label(full_name: str, username: str | None) -> str:
    return f"{full_name} (@{username})" if username else full_name


@router.callback_query(F.data == "m:adm:transfer")
async def cb_admin_transfer(callback: CallbackQuery) -> None:
    res = await _ensure_creator(callback)
    if res is None:
        return
    _, group = res
    async with SessionLocal() as session:
        members = await list_members(session, group.id)
    candidates = [
        (u.id, _member_label(u.full_name, u.tg_username))
        for u, role in members
        if role != MemberRole.creator
    ]
    if not candidates:
        await render(callback, texts.TRANSFER_NO_OTHER_MEMBERS, back_home_kb())
        return
    rows = [
        [InlineKeyboardButton(text=label, callback_data=f"m:adm:tr:{uid}")]
        for uid, label in candidates
    ]
    await render(callback, texts.TRANSFER_PICK_HEADER, with_back_home(rows))


@router.callback_query(F.data.regexp(r"^m:adm:tr:\d+$"))
async def cb_admin_transfer_pick(callback: CallbackQuery) -> None:
    if callback.data is None:
        await callback.answer()
        return
    new_uid = int(callback.data.rsplit(":", 1)[1])
    res = await _ensure_creator(callback)
    if res is None:
        return
    _, group = res
    async with SessionLocal() as session:
        members = await list_members(session, group.id)
    target = next(((u, r) for u, r in members if u.id == new_uid), None)
    if target is None:
        await callback.answer(texts.NOT_A_MEMBER, show_alert=True)
        return
    label = _member_label(target[0].full_name, target[0].tg_username)
    rows = [
        [
            InlineKeyboardButton(text="✅ Передать", callback_data=f"m:adm:tryes:{new_uid}"),
            InlineKeyboardButton(text="❌ Отмена", callback_data="m:adm"),
        ]
    ]
    await render(callback, texts.TRANSFER_CONFIRM.format(name=label), with_back_home(rows))


@router.callback_query(F.data.regexp(r"^m:adm:tryes:\d+$"))
async def cb_admin_transfer_confirm(callback: CallbackQuery, state: FSMContext) -> None:
    if callback.data is None:
        await callback.answer()
        return
    new_uid = int(callback.data.rsplit(":", 1)[1])
    res = await _ensure_creator(callback)
    if res is None:
        return
    user_id, group = res
    async with SessionLocal() as session, session.begin():
        status = await transfer_ownership(
            session,
            group_id=group.id,
            current_creator_id=user_id,
            new_creator_user_id=new_uid,
        )
        target_label = ""
        if status == "ok":
            from src.db.models import User as _U
            target = await session.get(_U, new_uid)
            if target is not None:
                target_label = _member_label(target.full_name, target.tg_username)

    if status == "not_member":
        await callback.answer(texts.NOT_A_MEMBER, show_alert=True)
        return
    if status == "same_user":
        await callback.answer(texts.TRANSFER_CANCELLED)
        return
    await callback.answer(texts.TRANSFER_OK.format(name=target_label))
    # Теперь пользователь больше не creator — открываем главное меню.
    await open_home(callback, state)


# ============ Удаление группы ============


@router.callback_query(F.data == "m:adm:del")
async def cb_admin_del_step1(callback: CallbackQuery) -> None:
    res = await _ensure_creator(callback)
    if res is None:
        return
    _, group = res
    rows = [
        [
            InlineKeyboardButton(text="✅ Да", callback_data="m:adm:del2"),
            InlineKeyboardButton(text="❌ Отмена", callback_data="m:adm"),
        ]
    ]
    await render(
        callback, texts.DELETE_GROUP_CONFIRM_1.format(name=group.name), with_back_home(rows)
    )


@router.callback_query(F.data == "m:adm:del2")
async def cb_admin_del_step2(callback: CallbackQuery) -> None:
    res = await _ensure_creator(callback)
    if res is None:
        return
    _, group = res
    rows = [
        [
            InlineKeyboardButton(text="✅ Удалить навсегда", callback_data="m:adm:del3"),
            InlineKeyboardButton(text="❌ Отмена", callback_data="m:adm"),
        ]
    ]
    await render(
        callback, texts.DELETE_GROUP_CONFIRM_2.format(name=group.name), with_back_home(rows)
    )


@router.callback_query(F.data == "m:adm:del3")
async def cb_admin_del_confirm(callback: CallbackQuery, state: FSMContext) -> None:
    res = await _ensure_creator(callback)
    if res is None:
        return
    _, group = res
    async with SessionLocal() as session, session.begin():
        assignment_ids = list(
            (
                await session.execute(
                    select(Assignment.id).where(Assignment.group_id == group.id)
                )
            ).scalars()
        )
        await delete_group(session, group.id)
        group_name = group.name

    for aid in assignment_ids:
        cancel_assignment_reminders(aid)
    await callback.answer()
    await render(callback, texts.DELETE_GROUP_OK.format(name=group_name), back_home_kb())
    # Активной группы больше нет — следующий открытый /menu обновит home.
    _ = state


# ============ Подсказка по bind_chat ============


@router.callback_query(F.data == "m:adm:bindhelp")
async def cb_admin_bindhelp(callback: CallbackQuery) -> None:
    text = (
        "Чтобы привязать групповой чат для дайджестов:\n"
        "1. Добавь бота в нужный групповой чат.\n"
        "2. В этом чате (как creator) отправь /bind_chat.\n\n"
        "Отвязать можно командой /unbind_chat в личке."
    )
    await render(callback, text, back_home_kb())

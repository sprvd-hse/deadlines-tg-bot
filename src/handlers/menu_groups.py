from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, Message

from src import texts
from src.config import settings
from src.db.models import MemberRole
from src.db.session import SessionLocal
from src.handlers.menu import open_home, render
from src.keyboards import back_home_kb, with_back_home
from src.scheduler import cancel_assignment_reminders
from src.services.groups import (
    check_join_rate_limit,
    create_group,
    is_creator,
    join_group,
    leave_group,
    list_members,
    list_user_groups,
    switch_active_group,
)
from src.services.users import get_or_create_user
from src.states import GroupOnboarding
from src.utils import normalize_invite_code

router = Router(name="menu_groups")

_GROUP_NAME_MAX = 128


# ============ Хаб группы ============


async def open_group_hub(target: CallbackQuery | Message) -> None:
    await _render_group_hub(target)


async def _render_group_hub(callback: CallbackQuery | Message) -> None:
    u = callback.from_user
    if u is None:
        return
    async with SessionLocal() as session, session.begin():
        user = await get_or_create_user(
            session, u.id, u.username, u.full_name or u.username or "user"
        )
        groups = await list_user_groups(session, user)
        active_id = user.active_group_id
        active = next((g for g, _ in groups if g.id == active_id), None)
        creator_flag = (
            await is_creator(session, user.id, active.id) if active is not None else False
        )

    if active is None:
        rows = [
            [InlineKeyboardButton(text="➕ Создать группу", callback_data="m:grp:new")],
            [InlineKeyboardButton(text="🔑 Присоединиться", callback_data="m:grp:join")],
        ]
        await render(callback, texts.MY_GROUPS_EMPTY, with_back_home(rows))
        return

    role_suffix = " (creator)" if creator_flag else ""
    text = f"👥 Группа «{active.name}»{role_suffix}\nInvite-код: {active.invite_code}"
    rows = [
        [InlineKeyboardButton(text="🔀 Переключить активную", callback_data="m:grp:sw")],
        [InlineKeyboardButton(text="➕ Создать новую", callback_data="m:grp:new")],
        [InlineKeyboardButton(text="🔑 Присоединиться по коду", callback_data="m:grp:join")],
        [InlineKeyboardButton(text="🚪 Выйти из группы", callback_data="m:grp:leave")],
    ]
    await render(callback, text, with_back_home(rows))


@router.callback_query(F.data == "m:grp")
async def cb_group_hub(callback: CallbackQuery, state: FSMContext) -> None:
    if await state.get_state() is not None:
        await state.clear()
    await _render_group_hub(callback)


# ============ Переключение активной группы ============


@router.callback_query(F.data == "m:grp:sw")
async def cb_group_switch_list(callback: CallbackQuery) -> None:
    u = callback.from_user
    if u is None:
        return
    async with SessionLocal() as session, session.begin():
        user = await get_or_create_user(
            session, u.id, u.username, u.full_name or u.username or "user"
        )
        groups = await list_user_groups(session, user)
        active_id = user.active_group_id

    if not groups:
        await render(callback, texts.MY_GROUPS_EMPTY, back_home_kb())
        return

    rows: list[list[InlineKeyboardButton]] = []
    for group, role in groups:
        marker = "✅ " if group.id == active_id else ""
        role_suffix = " (creator)" if role == MemberRole.creator else ""
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"{marker}{group.name}{role_suffix}",
                    callback_data=f"m:grp:sw:{group.id}",
                )
            ]
        )
    await render(callback, texts.MY_GROUPS_HEADER, with_back_home(rows))


@router.callback_query(F.data.regexp(r"^m:grp:sw:\d+$"))
async def cb_group_switch_pick(callback: CallbackQuery) -> None:
    if callback.data is None or callback.from_user is None:
        await callback.answer()
        return
    group_id = int(callback.data.rsplit(":", 1)[1])
    u = callback.from_user
    async with SessionLocal() as session, session.begin():
        user = await get_or_create_user(
            session, u.id, u.username, u.full_name or u.username or "user"
        )
        was_active = user.active_group_id == group_id
        group = await switch_active_group(session, user, group_id)
        group_name = group.name if group is not None else ""

    if group is None:
        await callback.answer(texts.NOT_A_MEMBER, show_alert=True)
        return
    if was_active:
        await callback.answer(texts.ALREADY_ACTIVE)
    else:
        await callback.answer(texts.SWITCHED_GROUP.format(name=group_name))
    # Перерисовываем сам список с обновлённой ✓ — пользователь видит, что активная сменилась.
    await cb_group_switch_list(callback)


# ============ Создание группы (FSM имени) ============


@router.callback_query(F.data == "m:grp:new")
async def cb_group_new(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(GroupOnboarding.waiting_name)
    await render(callback, "Введи название новой группы:", back_home_kb())


@router.message(GroupOnboarding.waiting_name, F.text)
async def msg_group_new_name(message: Message, state: FSMContext) -> None:
    if message.from_user is None:
        return
    name = (message.text or "").strip()
    if not name:
        await message.answer(texts.USAGE_CREATE_GROUP)
        return
    if len(name) > _GROUP_NAME_MAX:
        await message.answer(texts.GROUP_NAME_TOO_LONG)
        return

    u = message.from_user
    async with SessionLocal() as session, session.begin():
        user = await get_or_create_user(
            session, u.id, u.username, u.full_name or u.username or "user"
        )
        group = await create_group(session, user, name, settings.default_timezone)
        group_name, invite_code = group.name, group.invite_code

    await state.clear()
    await message.answer(texts.GROUP_CREATED.format(name=group_name, code=invite_code))
    await open_home(message)


# ============ Join по коду (FSM кода) ============


@router.callback_query(F.data == "m:grp:join")
async def cb_group_join(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(GroupOnboarding.waiting_code)
    await render(callback, texts.USAGE_JOIN, back_home_kb())


@router.message(GroupOnboarding.waiting_code, F.text)
async def msg_group_join_code(message: Message, state: FSMContext) -> None:
    if message.from_user is None:
        return
    raw = (message.text or "").strip()
    u = message.from_user
    if not check_join_rate_limit(u.id):
        await message.answer(texts.JOIN_RATE_LIMIT)
        return
    code = normalize_invite_code(raw)
    if code is None:
        await message.answer(texts.JOIN_NOT_FOUND)
        return

    async with SessionLocal() as session, session.begin():
        user = await get_or_create_user(
            session, u.id, u.username, u.full_name or u.username or "user"
        )
        group, status = await join_group(session, user, code)
        group_name = group.name if group is not None else ""

    await state.clear()
    if status == "not_found":
        await message.answer(texts.JOIN_NOT_FOUND)
    elif status == "already_member":
        await message.answer(texts.JOIN_ALREADY.format(name=group_name))
    else:
        await message.answer(texts.JOIN_SUCCESS.format(name=group_name))
    await open_home(message)


# ============ Выход из группы ============


@router.callback_query(F.data == "m:grp:leave")
async def cb_group_leave_ask(callback: CallbackQuery) -> None:
    rows = [
        [
            InlineKeyboardButton(text="✅ Выйти", callback_data="m:grp:leaveyes"),
            InlineKeyboardButton(text="❌ Отмена", callback_data="m:grp"),
        ]
    ]
    await render(callback, "Выйти из активной группы?", with_back_home(rows))


@router.callback_query(F.data == "m:grp:leaveyes")
async def cb_group_leave_confirm(callback: CallbackQuery) -> None:
    if callback.from_user is None:
        await callback.answer()
        return
    u = callback.from_user
    from sqlalchemy import select

    from src.db.models import Assignment

    async with SessionLocal() as session, session.begin():
        user = await get_or_create_user(
            session, u.id, u.username, u.full_name or u.username or "user"
        )
        active_id = user.active_group_id
        if active_id is None:
            await callback.answer(texts.LEAVE_NO_GROUP, show_alert=True)
            return
        members = await list_members(session, active_id)
        assignment_ids: list[int] = []
        if len(members) == 1:
            assignment_ids = list(
                (
                    await session.execute(
                        select(Assignment.id).where(Assignment.group_id == active_id)
                    )
                ).scalars()
            )
        # group_name берём до возможного удаления
        group_name = next(
            (g.name for g, _ in await list_user_groups(session, user) if g.id == active_id),
            "",
        )
        status = await leave_group(session, group_id=active_id, user_id=user.id)
        if status in ("left", "group_deleted"):
            user.active_group_id = None

    if status == "group_deleted":
        for aid in assignment_ids:
            cancel_assignment_reminders(aid)
        await callback.answer()
        await render(
            callback, texts.LEAVE_DELETED.format(group=group_name), back_home_kb()
        )
        return
    if status == "need_transfer":
        await callback.answer()
        await render(callback, texts.LEAVE_NEED_TRANSFER, back_home_kb())
        return
    if status == "not_member":
        await callback.answer(texts.LEAVE_NO_GROUP, show_alert=True)
        return
    await callback.answer()
    await render(callback, texts.LEAVE_OK.format(group=group_name), back_home_kb())

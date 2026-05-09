from aiogram import F, Router
from aiogram.filters import Command, CommandObject
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)
from aiogram.types import (
    User as TgUser,
)

from bot import texts
from bot.config import settings
from bot.db.models import MemberRole
from bot.db.session import SessionLocal
from bot.keyboards import members_kb, yes_no_kb
from bot.services.groups import (
    check_join_rate_limit,
    create_group,
    delete_group,
    get_active_group,
    is_creator,
    join_group,
    list_members,
    list_user_groups,
    switch_active_group,
    transfer_ownership,
)
from bot.services.users import get_or_create_user
from bot.utils import normalize_invite_code

router = Router(name="groups")

_GROUP_NAME_MAX = 128
_CALLBACK_SET_ACTIVE = "set_active"


def _user_meta(tg_user: TgUser) -> tuple[int, str | None, str]:
    return tg_user.id, tg_user.username, tg_user.full_name or tg_user.username or "user"


@router.message(Command("create_group"))
async def cmd_create_group(message: Message, command: CommandObject) -> None:
    if message.from_user is None:
        return
    name = (command.args or "").strip()
    if not name:
        await message.answer(texts.USAGE_CREATE_GROUP)
        return
    if len(name) > _GROUP_NAME_MAX:
        await message.answer(texts.GROUP_NAME_TOO_LONG)
        return

    tg_id, username, full_name = _user_meta(message.from_user)
    async with SessionLocal() as session, session.begin():
        user = await get_or_create_user(session, tg_id, username, full_name)
        group = await create_group(session, user, name, settings.default_timezone)
        group_name, invite_code = group.name, group.invite_code

    await message.answer(texts.GROUP_CREATED.format(name=group_name, code=invite_code))


@router.message(Command("join"))
async def cmd_join(message: Message, command: CommandObject) -> None:
    if message.from_user is None:
        return
    raw = (command.args or "").strip()
    if not raw:
        await message.answer(texts.USAGE_JOIN)
        return

    tg_id, username, full_name = _user_meta(message.from_user)
    if not check_join_rate_limit(tg_id):
        await message.answer(texts.JOIN_RATE_LIMIT)
        return

    code = normalize_invite_code(raw)
    if code is None:
        await message.answer(texts.JOIN_NOT_FOUND)
        return

    async with SessionLocal() as session, session.begin():
        user = await get_or_create_user(session, tg_id, username, full_name)
        group, status = await join_group(session, user, code)
        group_name = group.name if group is not None else ""

    if status == "not_found":
        await message.answer(texts.JOIN_NOT_FOUND)
    elif status == "already_member":
        await message.answer(texts.JOIN_ALREADY.format(name=group_name))
    else:
        await message.answer(texts.JOIN_SUCCESS.format(name=group_name))


@router.message(Command("my_groups"))
async def cmd_my_groups(message: Message) -> None:
    if message.from_user is None:
        return
    tg_id, username, full_name = _user_meta(message.from_user)

    async with SessionLocal() as session, session.begin():
        user = await get_or_create_user(session, tg_id, username, full_name)
        groups = await list_user_groups(session, user)
        active_id = user.active_group_id

    if not groups:
        await message.answer(texts.MY_GROUPS_EMPTY)
        return

    rows = []
    for group, role in groups:
        marker = "✓ " if group.id == active_id else ""
        role_suffix = " (creator)" if role == MemberRole.creator else ""
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"{marker}{group.name}{role_suffix}",
                    callback_data=f"{_CALLBACK_SET_ACTIVE}:{group.id}",
                )
            ]
        )

    await message.answer(
        texts.MY_GROUPS_HEADER,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )


@router.callback_query(F.data.startswith(f"{_CALLBACK_SET_ACTIVE}:"))
async def cb_set_active(callback: CallbackQuery) -> None:
    if callback.data is None or callback.from_user is None:
        await callback.answer()
        return
    try:
        group_id = int(callback.data.split(":", 1)[1])
    except (ValueError, IndexError):
        await callback.answer()
        return

    tg_id, username, full_name = _user_meta(callback.from_user)
    async with SessionLocal() as session, session.begin():
        user = await get_or_create_user(session, tg_id, username, full_name)
        was_active = user.active_group_id == group_id
        group = await switch_active_group(session, user, group_id)
        group_name = group.name if group is not None else ""

    if group is None:
        await callback.answer(texts.NOT_A_MEMBER, show_alert=True)
    elif was_active:
        await callback.answer(texts.ALREADY_ACTIVE)
    else:
        await callback.answer(texts.SWITCHED_GROUP.format(name=group_name))


# ============ /transfer_ownership ============


def _member_label(full_name: str, username: str | None) -> str:
    return f"{full_name} (@{username})" if username else full_name


@router.message(Command("transfer_ownership"))
async def cmd_transfer_ownership(message: Message) -> None:
    if message.from_user is None:
        return
    tg_id, username, full_name = _user_meta(message.from_user)
    async with SessionLocal() as session, session.begin():
        user = await get_or_create_user(session, tg_id, username, full_name)
        group = await get_active_group(session, user)
        if group is None:
            await message.answer(texts.NO_ACTIVE_GROUP)
            return
        if not await is_creator(session, user.id, group.id):
            await message.answer(texts.CREATOR_ONLY)
            return
        members = await list_members(session, group.id)
        candidates = [(u, _member_label(u.full_name, u.tg_username)) for u, role in members if role != MemberRole.creator]

    if not candidates:
        await message.answer(texts.TRANSFER_NO_OTHER_MEMBERS)
        return

    items = [(u.id, label) for u, label in candidates]
    await message.answer(
        texts.TRANSFER_PICK_HEADER,
        reply_markup=members_kb(items, prefix="transfer_pick"),
    )


@router.callback_query(F.data.startswith("transfer_pick:"))
async def cb_transfer_pick(callback: CallbackQuery) -> None:
    if callback.data is None or callback.from_user is None:
        await callback.answer()
        return
    try:
        new_user_id = int(callback.data.split(":", 1)[1])
    except ValueError:
        await callback.answer()
        return

    tg_id, username, full_name = _user_meta(callback.from_user)
    async with SessionLocal() as session, session.begin():
        user = await get_or_create_user(session, tg_id, username, full_name)
        group = await get_active_group(session, user)
        if group is None or not await is_creator(session, user.id, group.id):
            await callback.answer(texts.CREATOR_ONLY, show_alert=True)
            return
        from sqlalchemy import select

        from bot.db.models import GroupMember
        from bot.db.models import User as _U
        target = (
            await session.execute(
                select(_U).join(GroupMember, GroupMember.user_id == _U.id).where(
                    _U.id == new_user_id, GroupMember.group_id == group.id
                )
            )
        ).scalar_one_or_none()
        if target is None:
            await callback.answer()
            return
        target_label = _member_label(target.full_name, target.tg_username)

    if isinstance(callback.message, Message):
        await callback.message.answer(
            texts.TRANSFER_CONFIRM.format(name=target_label),
            reply_markup=yes_no_kb(prefix=f"transfer_confirm:{new_user_id}"),
        )
    await callback.answer()


@router.callback_query(F.data.regexp(r"^transfer_confirm:\d+:(yes|no)$"))
async def cb_transfer_confirm(callback: CallbackQuery) -> None:
    if callback.data is None or callback.from_user is None:
        await callback.answer()
        return
    _, uid_str, decision = callback.data.split(":")
    new_user_id = int(uid_str)

    if decision == "no":
        if isinstance(callback.message, Message):
            await callback.message.answer(texts.TRANSFER_CANCELLED)
        await callback.answer()
        return

    tg_id, username, full_name = _user_meta(callback.from_user)
    async with SessionLocal() as session, session.begin():
        user = await get_or_create_user(session, tg_id, username, full_name)
        group = await get_active_group(session, user)
        if group is None or not await is_creator(session, user.id, group.id):
            await callback.answer(texts.CREATOR_ONLY, show_alert=True)
            return
        status = await transfer_ownership(
            session,
            group_id=group.id,
            current_creator_id=user.id,
            new_creator_user_id=new_user_id,
        )
        target_label = ""
        if status == "ok":
            from bot.db.models import User as _U
            target = await session.get(_U, new_user_id)
            if target is not None:
                target_label = _member_label(target.full_name, target.tg_username)

    if isinstance(callback.message, Message):
        if status == "not_member":
            await callback.message.answer(texts.NOT_A_MEMBER)
        elif status == "same_user":
            await callback.message.answer(texts.TRANSFER_CANCELLED)
        else:
            await callback.message.answer(texts.TRANSFER_OK.format(name=target_label))
    await callback.answer()


# ============ /delete_group ============


@router.message(Command("delete_group"))
async def cmd_delete_group(message: Message) -> None:
    if message.from_user is None:
        return
    tg_id, username, full_name = _user_meta(message.from_user)
    async with SessionLocal() as session, session.begin():
        user = await get_or_create_user(session, tg_id, username, full_name)
        group = await get_active_group(session, user)
        if group is None:
            await message.answer(texts.NO_ACTIVE_GROUP)
            return
        if not await is_creator(session, user.id, group.id):
            await message.answer(texts.CREATOR_ONLY)
            return
        group_id, group_name = group.id, group.name

    await message.answer(
        texts.DELETE_GROUP_CONFIRM_1.format(name=group_name),
        reply_markup=yes_no_kb(prefix=f"delgroup1:{group_id}"),
    )


@router.callback_query(F.data.regexp(r"^delgroup1:\d+:(yes|no)$"))
async def cb_delete_group_step1(callback: CallbackQuery) -> None:
    if callback.data is None or callback.from_user is None:
        await callback.answer()
        return
    _, gid_str, decision = callback.data.split(":")
    group_id = int(gid_str)

    if decision == "no":
        if isinstance(callback.message, Message):
            await callback.message.answer(texts.DELETE_GROUP_CANCELLED)
        await callback.answer()
        return

    tg_id, username, full_name = _user_meta(callback.from_user)
    async with SessionLocal() as session, session.begin():
        user = await get_or_create_user(session, tg_id, username, full_name)
        if not await is_creator(session, user.id, group_id):
            await callback.answer(texts.CREATOR_ONLY, show_alert=True)
            return
        from bot.db.models import Group as _G
        group = await session.get(_G, group_id)
        group_name = group.name if group is not None else ""

    if isinstance(callback.message, Message):
        await callback.message.answer(
            texts.DELETE_GROUP_CONFIRM_2.format(name=group_name),
            reply_markup=yes_no_kb(prefix=f"delgroup2:{group_id}"),
        )
    await callback.answer()


@router.callback_query(F.data.regexp(r"^delgroup2:\d+:(yes|no)$"))
async def cb_delete_group_step2(callback: CallbackQuery) -> None:
    if callback.data is None or callback.from_user is None:
        await callback.answer()
        return
    _, gid_str, decision = callback.data.split(":")
    group_id = int(gid_str)

    if decision == "no":
        if isinstance(callback.message, Message):
            await callback.message.answer(texts.DELETE_GROUP_CANCELLED)
        await callback.answer()
        return

    tg_id, username, full_name = _user_meta(callback.from_user)
    async with SessionLocal() as session, session.begin():
        user = await get_or_create_user(session, tg_id, username, full_name)
        if not await is_creator(session, user.id, group_id):
            await callback.answer(texts.CREATOR_ONLY, show_alert=True)
            return
        from bot.db.models import Group as _G
        group = await session.get(_G, group_id)
        group_name = group.name if group is not None else ""
        await delete_group(session, group_id)

    if isinstance(callback.message, Message):
        await callback.message.answer(texts.DELETE_GROUP_OK.format(name=group_name))
    await callback.answer()

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
from bot.services.groups import (
    check_join_rate_limit,
    create_group,
    join_group,
    list_user_groups,
    switch_active_group,
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

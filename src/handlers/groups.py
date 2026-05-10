from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from aiogram import F, Router
from aiogram.filters import Command, CommandObject
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)
from aiogram.types import (
    User as TgUser,
)

from src import texts
from src.config import settings
from src.db.models import MemberRole
from src.db.session import SessionLocal
from src.keyboards import members_kb, yes_no_kb
from src.scheduler import cancel_assignment_reminders
from src.services.groups import (
    check_join_rate_limit,
    create_group,
    delete_group,
    get_active_group,
    get_member,
    is_creator,
    join_group,
    leave_group,
    list_members,
    list_user_groups,
    regenerate_invite_code,
    set_personal_reminders,
    switch_active_group,
    transfer_ownership,
)
from src.services.users import get_or_create_user
from src.states import GroupSettings
from src.utils import normalize_invite_code

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

    import html
    await message.answer(
        texts.GROUP_CREATED.format(name=html.escape(group_name), code=invite_code),
        parse_mode="HTML",
    )


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
        marker = "✅ " if group.id == active_id else ""
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

        from src.db.models import GroupMember
        from src.db.models import User as _U
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
            from src.db.models import User as _U
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
        from src.db.models import Group as _G
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
        from sqlalchemy import select as _select

        from src.db.models import Assignment as _A
        from src.db.models import Group as _G
        group = await session.get(_G, group_id)
        group_name = group.name if group is not None else ""
        assignment_ids = list(
            (
                await session.execute(_select(_A.id).where(_A.group_id == group_id))
            ).scalars()
        )
        await delete_group(session, group_id)

    for aid in assignment_ids:
        cancel_assignment_reminders(aid)

    if isinstance(callback.message, Message):
        await callback.message.answer(texts.DELETE_GROUP_OK.format(name=group_name))
    await callback.answer()


# ============ /bind_chat, /unbind_chat ============


async def _bot_can_send_in(bot, chat_id: int) -> bool:
    try:
        me = await bot.get_me()
        member = await bot.get_chat_member(chat_id, me.id)
        chat = await bot.get_chat(chat_id)
    except Exception:
        return False
    status = getattr(member, "status", "")
    if status in ("left", "kicked"):
        return False
    if status == "restricted":
        # У ChatMemberRestricted есть can_send_messages.
        return bool(getattr(member, "can_send_messages", False))
    if status in ("administrator", "creator"):
        return True
    # status == "member" — смотрим chat-wide дефолтные права.
    perms = getattr(chat, "permissions", None)
    if perms is None:
        return True
    return getattr(perms, "can_send_messages", True) is not False


@router.message(Command("bind_chat"))
async def cmd_bind_chat(message: Message) -> None:
    if message.from_user is None:
        return
    if message.chat.type not in ("group", "supergroup"):
        await message.answer(texts.BIND_CHAT_NOT_GROUP)
        return

    if not await _bot_can_send_in(message.bot, message.chat.id):
        await message.answer(texts.BIND_CHAT_BOT_CANT_WRITE)
        return

    tg_id, username, full_name = _user_meta(message.from_user)
    async with SessionLocal() as session, session.begin():
        user = await get_or_create_user(session, tg_id, username, full_name)
        groups = await list_user_groups(session, user)
        creator_groups = [g for g, role in groups if role == MemberRole.creator]

    if not creator_groups:
        await message.answer(texts.BIND_CHAT_NO_CREATOR_GROUPS)
        return

    rows: list[list[InlineKeyboardButton]] = []
    for g in creator_groups:
        suffix = " ✓" if g.chat_id == message.chat.id else ""
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"{g.name}{suffix}",
                    callback_data=f"bindp:{g.id}",
                )
            ]
        )
    await message.answer(
        texts.BIND_CHAT_PICK,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )


@router.callback_query(F.data.regexp(r"^bindp:\d+$"))
async def cb_bind_pick(callback: CallbackQuery) -> None:
    if callback.data is None or callback.from_user is None:
        await callback.answer()
        return
    target_group_id = int(callback.data.rsplit(":", 1)[1])
    msg = callback.message
    if not isinstance(msg, Message):
        await callback.answer()
        return
    chat_id = msg.chat.id
    chat_title = msg.chat.title or str(chat_id)

    tg_id, username, full_name = _user_meta(callback.from_user)
    async with SessionLocal() as session, session.begin():
        user = await get_or_create_user(session, tg_id, username, full_name)
        if not await is_creator(session, user.id, target_group_id):
            await callback.answer(texts.BIND_CHAT_NOT_CREATOR, show_alert=True)
            return
        from sqlalchemy import select as _select

        from src.db.models import Group as _G
        target = await session.get(_G, target_group_id)
        if target is None:
            await callback.answer()
            return
        if target.chat_id == chat_id:
            await msg.edit_text(texts.BIND_CHAT_ALREADY_HERE.format(group=target.name))
            await callback.answer()
            return

        # Если этот же чат уже привязан к ДРУГОЙ группе — отвязываем.
        prev = (
            await session.execute(
                _select(_G).where(_G.chat_id == chat_id, _G.id != target_group_id)
            )
        ).scalar_one_or_none()
        prev_name = prev.name if prev is not None else None
        if prev is not None:
            prev.chat_id = None

        target.chat_id = chat_id
        target_name = target.name

    if prev_name:
        await msg.edit_text(
            texts.BIND_CHAT_MOVED.format(group=target_name, from_group=prev_name)
        )
    else:
        await msg.edit_text(
            texts.BIND_CHAT_OK.format(chat=chat_title, group=target_name)
        )
    await callback.answer()


@router.callback_query(F.data.regexp(r"^bindchat:\d+:-?\d+:(yes|no)$"))
async def cb_bind_chat_confirm(callback: CallbackQuery) -> None:
    if callback.data is None or callback.from_user is None:
        await callback.answer()
        return
    _, gid_str, chat_str, decision = callback.data.split(":")
    group_id = int(gid_str)
    chat_id = int(chat_str)

    if decision == "no":
        if isinstance(callback.message, Message):
            await callback.message.answer(texts.BIND_CHAT_CANCELLED)
        await callback.answer()
        return

    tg_id, username, full_name = _user_meta(callback.from_user)
    async with SessionLocal() as session, session.begin():
        user = await get_or_create_user(session, tg_id, username, full_name)
        if not await is_creator(session, user.id, group_id):
            await callback.answer(texts.BIND_CHAT_NOT_CREATOR, show_alert=True)
            return
        from src.db.models import Group as _G
        g = await session.get(_G, group_id)
        if g is not None:
            g.chat_id = chat_id

    if isinstance(callback.message, Message):
        await callback.message.answer(texts.BIND_CHAT_REPLACED)
    await callback.answer()


@router.message(Command("unbind_chat"))
async def cmd_unbind_chat(message: Message) -> None:
    if message.from_user is None:
        return
    if message.chat.type != "private":
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
        if group.chat_id is None:
            await message.answer(texts.UNBIND_CHAT_NONE)
            return
        group.chat_id = None
        group_name = group.name

    await message.answer(texts.UNBIND_CHAT_OK.format(group=group_name))


# ============ /settings ============


_WEEKDAYS_RU = ["понедельник", "вторник", "среда", "четверг", "пятница", "суббота", "воскресенье"]


def _settings_kb(enabled: bool) -> InlineKeyboardMarkup:
    label = texts.SETTINGS_TOGGLE_OFF if enabled else texts.SETTINGS_TOGGLE_ON
    new_value = "0" if enabled else "1"
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text=label, callback_data=f"settings_pr:{new_value}")]]
    )


@router.message(Command("settings"))
async def cmd_settings(message: Message) -> None:
    if message.from_user is None:
        return
    tg_id, username, full_name = _user_meta(message.from_user)
    async with SessionLocal() as session, session.begin():
        user = await get_or_create_user(session, tg_id, username, full_name)
        group = await get_active_group(session, user)
        if group is None:
            await message.answer(texts.NO_ACTIVE_GROUP)
            return
        member = await get_member(session, group_id=group.id, user_id=user.id)
        if member is None:
            await message.answer(texts.NOT_A_MEMBER)
            return
        enabled = member.personal_reminders_enabled
        group_name = group.name

    state_str = texts.SETTINGS_REMINDERS_ON if enabled else texts.SETTINGS_REMINDERS_OFF
    await message.answer(
        texts.SETTINGS_HEADER.format(group=group_name, state=state_str),
        reply_markup=_settings_kb(enabled),
    )


@router.callback_query(F.data.regexp(r"^settings_pr:[01]$"))
async def cb_settings_toggle(callback: CallbackQuery) -> None:
    if callback.data is None or callback.from_user is None:
        await callback.answer()
        return
    new_value = callback.data.split(":", 1)[1] == "1"
    tg_id, username, full_name = _user_meta(callback.from_user)
    async with SessionLocal() as session, session.begin():
        user = await get_or_create_user(session, tg_id, username, full_name)
        group = await get_active_group(session, user)
        if group is None:
            await callback.answer(texts.NO_ACTIVE_GROUP, show_alert=True)
            return
        await set_personal_reminders(
            session, group_id=group.id, user_id=user.id, enabled=new_value
        )

    state_str = texts.SETTINGS_REMINDERS_ON if new_value else texts.SETTINGS_REMINDERS_OFF
    if isinstance(callback.message, Message):
        await callback.message.edit_text(
            texts.SETTINGS_HEADER.format(group=group.name, state=state_str),
            reply_markup=_settings_kb(new_value),
        )
    await callback.answer(texts.SETTINGS_UPDATED)


# ============ /group_settings, /regenerate_code ============


def _group_settings_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=texts.GROUP_SETTINGS_PICK_TZ, callback_data="gs:tz")],
            [InlineKeyboardButton(text=texts.GROUP_SETTINGS_PICK_DAY, callback_data="gs:day")],
            [InlineKeyboardButton(text=texts.GROUP_SETTINGS_PICK_HOUR, callback_data="gs:hour")],
        ]
    )


def _digest_day_kb() -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text=_WEEKDAYS_RU[i].capitalize(), callback_data=f"gs_day:{i}")]
        for i in range(7)
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _digest_hour_kb() -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for start in range(0, 24, 6):
        rows.append(
            [
                InlineKeyboardButton(text=f"{h:02d}", callback_data=f"gs_hour:{h}")
                for h in range(start, start + 6)
            ]
        )
    return InlineKeyboardMarkup(inline_keyboard=rows)


@router.message(Command("group_settings"))
async def cmd_group_settings(message: Message) -> None:
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
        snapshot = (group.name, group.timezone, group.digest_day, group.digest_hour)

    name, tz, day, hour = snapshot
    await message.answer(
        texts.GROUP_SETTINGS_HEADER.format(
            group=name, tz=tz, weekday=_WEEKDAYS_RU[day], hour=hour
        ),
        reply_markup=_group_settings_kb(),
    )


@router.callback_query(F.data == "gs:tz")
async def cb_gs_tz(callback: CallbackQuery, state: FSMContext) -> None:
    if callback.from_user is None:
        await callback.answer()
        return
    tg_id, username, full_name = _user_meta(callback.from_user)
    async with SessionLocal() as session, session.begin():
        user = await get_or_create_user(session, tg_id, username, full_name)
        group = await get_active_group(session, user)
        if group is None or not await is_creator(session, user.id, group.id):
            await callback.answer(texts.CREATOR_ONLY, show_alert=True)
            return
        await state.update_data(group_id=group.id)
    await state.set_state(GroupSettings.waiting_tz)
    if isinstance(callback.message, Message):
        await callback.message.answer(texts.GROUP_SETTINGS_TZ_PROMPT)
    await callback.answer()


@router.message(GroupSettings.waiting_tz, F.text)
async def gs_tz_input(message: Message, state: FSMContext) -> None:
    raw = (message.text or "").strip()
    try:
        ZoneInfo(raw)
    except (ZoneInfoNotFoundError, ValueError):
        await message.answer(texts.GROUP_SETTINGS_TZ_INVALID)
        return
    data = await state.get_data()
    group_id = data["group_id"]
    async with SessionLocal() as session, session.begin():
        from src.db.models import Group as _G
        g = await session.get(_G, group_id)
        if g is not None:
            g.timezone = raw
    await state.clear()
    await message.answer(texts.GROUP_SETTINGS_TZ_OK.format(tz=raw))


@router.callback_query(F.data == "gs:day")
async def cb_gs_day(callback: CallbackQuery) -> None:
    if isinstance(callback.message, Message):
        await callback.message.answer(
            texts.GROUP_SETTINGS_DAY_PROMPT, reply_markup=_digest_day_kb()
        )
    await callback.answer()


@router.callback_query(F.data.regexp(r"^gs_day:[0-6]$"))
async def cb_gs_day_pick(callback: CallbackQuery) -> None:
    if callback.data is None or callback.from_user is None:
        await callback.answer()
        return
    day = int(callback.data.split(":", 1)[1])
    tg_id, username, full_name = _user_meta(callback.from_user)
    async with SessionLocal() as session, session.begin():
        user = await get_or_create_user(session, tg_id, username, full_name)
        group = await get_active_group(session, user)
        if group is None or not await is_creator(session, user.id, group.id):
            await callback.answer(texts.CREATOR_ONLY, show_alert=True)
            return
        group.digest_day = day

    if isinstance(callback.message, Message):
        await callback.message.answer(
            texts.GROUP_SETTINGS_DAY_OK.format(weekday=_WEEKDAYS_RU[day])
        )
    await callback.answer()


@router.callback_query(F.data == "gs:hour")
async def cb_gs_hour(callback: CallbackQuery) -> None:
    if isinstance(callback.message, Message):
        await callback.message.answer(
            texts.GROUP_SETTINGS_HOUR_PROMPT, reply_markup=_digest_hour_kb()
        )
    await callback.answer()


@router.callback_query(F.data.regexp(r"^gs_hour:\d{1,2}$"))
async def cb_gs_hour_pick(callback: CallbackQuery) -> None:
    if callback.data is None or callback.from_user is None:
        await callback.answer()
        return
    hour = int(callback.data.split(":", 1)[1])
    if not 0 <= hour <= 23:
        await callback.answer()
        return
    tg_id, username, full_name = _user_meta(callback.from_user)
    async with SessionLocal() as session, session.begin():
        user = await get_or_create_user(session, tg_id, username, full_name)
        group = await get_active_group(session, user)
        if group is None or not await is_creator(session, user.id, group.id):
            await callback.answer(texts.CREATOR_ONLY, show_alert=True)
            return
        group.digest_hour = hour

    if isinstance(callback.message, Message):
        await callback.message.answer(texts.GROUP_SETTINGS_HOUR_OK.format(hour=hour))
    await callback.answer()


@router.message(Command("regenerate_code"))
async def cmd_regenerate_code(message: Message) -> None:
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
        new_code = await regenerate_invite_code(session, group)

    await message.answer(texts.REGENERATE_CODE_OK.format(code=new_code))


# ============ /leave ============


@router.message(Command("leave"))
async def cmd_leave(message: Message) -> None:
    if message.from_user is None:
        return
    tg_id, username, full_name = _user_meta(message.from_user)
    async with SessionLocal() as session, session.begin():
        user = await get_or_create_user(session, tg_id, username, full_name)
        group = await get_active_group(session, user)
        if group is None:
            await message.answer(texts.LEAVE_NO_GROUP)
            return
        group_name = group.name
        from sqlalchemy import select as _select

        from src.db.models import Assignment as _A
        assignment_ids_to_cancel: list[int] = []
        # Если уйдём как одиночный creator — соберём id заранее, чтобы потом отменить job-ы.
        members = await list_members(session, group.id)
        if len(members) == 1:
            assignment_ids_to_cancel = list(
                (
                    await session.execute(
                        _select(_A.id).where(_A.group_id == group.id)
                    )
                ).scalars()
            )
        status = await leave_group(session, group_id=group.id, user_id=user.id)
        if status in ("left", "group_deleted"):
            user.active_group_id = None

    if status == "group_deleted":
        for aid in assignment_ids_to_cancel:
            cancel_assignment_reminders(aid)

    if status == "need_transfer":
        await message.answer(texts.LEAVE_NEED_TRANSFER)
    elif status == "not_member":
        await message.answer(texts.LEAVE_NO_GROUP)
    elif status == "group_deleted":
        await message.answer(texts.LEAVE_DELETED.format(group=group_name))
    else:
        await message.answer(texts.LEAVE_OK.format(group=group_name))

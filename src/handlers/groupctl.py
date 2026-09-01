import logging

from aiogram import Bot, F, Router
from aiogram.filters import Command, CommandObject
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from src.db.session import SessionLocal
from src.handlers.menu import open_home
from src.handlers.ui import Event, load_context, show, with_group
from src.services.groups import GroupManager, check_join_rate_limit
from src.states import GroupOnboarding
from src.ui import keyboards as kb
from src.ui.callbacks import GroupAction, Nav
from src.utils import build_invite_link, invite_code_from_payload, normalize_invite_code
from src.views import texts as t

logger = logging.getLogger(__name__)

router = Router(name="groups")
router.message.filter(F.chat.type == "private")


@router.callback_query(Nav.filter(F.to == "group"))
@router.callback_query(GroupAction.filter(F.action == "back"))
async def open_group(event: Event, state: FSMContext, bot: Bot, *args, **kwargs) -> None:
    """Экран группы."""
    await state.clear()
    if isinstance(event, CallbackQuery):
        await event.answer()
    await render_group(event, bot)


async def render_group(event: Event, bot: Bot) -> None:
    """Рисует карточку активной группы."""
    ctx = await load_context(event)
    if ctx is None:
        return
    active = ctx.activated()
    async with SessionLocal() as session:
        manager = GroupManager(session)
        groups = await manager.user_groups(ctx.user.id)
        members = await manager.members(active.group.id) if active else []
    text = (
        t.GROUP_HEADER.format(
            group=active.group.name,
            members=len(members),
            code=active.group.invite_code,
            link=await invite_link(bot, active.group.invite_code),
        )
        if active
        else t.GROUP_NONE
    )
    await show(
        event,
        text,
        kb.group_keyboard(
            has_group=active is not None,
            is_creator=active is not None and active.is_creator,
            group_count=len(groups),
        ),
    )


@router.callback_query(GroupAction.filter(F.action == "switch"))
async def open_switch(callback: CallbackQuery) -> None:
    """Список групп пользователя для переключения."""
    ctx = await load_context(callback)
    if ctx is None:
        return
    async with SessionLocal() as session:
        groups = await GroupManager(session).user_groups(ctx.user.id)
    active = ctx.activated()
    active_id = active.group.id if active else None
    await callback.answer()
    await show(
        callback,
        t.GROUP_SWITCH_HEADER,
        kb.group_switch_keyboard([(g, g.id == active_id) for g, _ in groups]),
    )


@router.callback_query(GroupAction.filter(F.action == "set"))
async def switch_group(callback: CallbackQuery, callback_data: GroupAction, bot: Bot) -> None:
    """Делает выбранную группу активной."""
    ctx = await load_context(callback)
    if ctx is None:
        return
    async with SessionLocal() as session, session.begin():
        group = await GroupManager(session).switch_active(
            user=ctx.user, group_id=callback_data.group_id
        )
    if group is None:
        await callback.answer(t.GROUP_CODE_NOT_FOUND, show_alert=True)
        return
    await callback.answer(t.GROUP_SWITCHED.format(group=group.name))
    await render_group(callback, bot)


@router.callback_query(GroupAction.filter(F.action == "new"))
async def ask_group_name(callback: CallbackQuery, state: FSMContext) -> None:
    """Просит название новой группы."""
    await state.set_state(GroupOnboarding.name)
    await callback.answer()
    await show(callback, t.GROUP_ASK_NAME)


@router.message(GroupOnboarding.name, F.text)
async def create_group(message: Message, state: FSMContext, bot: Bot) -> None:
    """Создаёт группу."""
    ctx = await load_context(message)
    if ctx is None:
        return
    async with SessionLocal() as session, session.begin():
        group = await GroupManager(session).create_group(creator=ctx.user, name=message.text or "")
    await state.clear()
    await message.answer(
        t.GROUP_CREATED.format(group=group.name, link=await invite_link(bot, group.invite_code)),
        parse_mode="HTML",
    )
    await open_home(message)


@router.callback_query(GroupAction.filter(F.action == "join"))
async def ask_invite_code(callback: CallbackQuery, state: FSMContext) -> None:
    """Просит invite-код."""
    await state.set_state(GroupOnboarding.code)
    await callback.answer()
    await show(callback, t.GROUP_ASK_CODE)


@router.message(GroupOnboarding.code, F.text)
async def join_by_code_input(message: Message, state: FSMContext) -> None:
    """Присоединяет по коду, введённому в диалоге."""
    await _join(message, state, message.text or "")


@router.message(Command("join"))
async def cmd_join(message: Message, command: CommandObject, state: FSMContext) -> None:
    """Присоединяет по коду из аргумента команды."""
    if not command.args:
        await state.set_state(GroupOnboarding.code)
        await message.answer(t.GROUP_ASK_CODE)
        return
    await _join(message, state, command.args)


@router.callback_query(GroupAction.filter(F.action == "leave"))
async def confirm_leave(callback: CallbackQuery) -> None:
    """Спрашивает подтверждение выхода."""
    ctx = await with_group(callback)
    if ctx is None:
        return
    await callback.answer()
    await show(
        callback,
        t.CONFIRM_LEAVE.format(group=ctx.group.name),
        kb.confirm_keyboard(
            yes_callback=GroupAction(action="leaveyes").pack(),
            no_callback=GroupAction(action="back").pack(),
            yes_text="🚪 Выйти",
        ),
    )


@router.callback_query(GroupAction.filter(F.action == "leaveyes"))
async def do_leave(callback: CallbackQuery, bot: Bot) -> None:
    """Выводит пользователя из группы."""
    ctx = await with_group(callback)
    if ctx is None:
        return
    async with SessionLocal() as session, session.begin():
        status = await GroupManager(session).leave_group(group_id=ctx.group.id, user_id=ctx.user.id)
    messages = {
        "left": t.GROUP_LEFT,
        "group_deleted": t.GROUP_DELETED_ON_LEAVE,
        "need_transfer": t.GROUP_NEED_TRANSFER,
        "not_member": t.GROUP_NONE,
    }
    await callback.answer(messages[status], show_alert=status == "need_transfer")
    if status == "need_transfer":
        await render_group(callback, bot)
        return
    await open_home(callback)


async def _join(message: Message, state: FSMContext, raw_code: str) -> None:
    code = normalize_invite_code(raw_code)
    if code is None:
        await message.answer(t.GROUP_CODE_INVALID)
        return
    if message.from_user is not None and not check_join_rate_limit(message.from_user.id):
        await message.answer(t.GROUP_RATE_LIMITED)
        return
    ctx = await load_context(message)
    if ctx is None:
        return
    async with SessionLocal() as session, session.begin():
        group, status = await GroupManager(session).join_group(user=ctx.user, invite_code=code)
    if group is None:
        await message.answer(t.GROUP_CODE_NOT_FOUND)
        return
    await state.clear()
    template = t.GROUP_JOINED if status == "ok" else t.GROUP_ALREADY_MEMBER
    await message.answer(template.format(group=group.name), parse_mode="HTML")
    await open_home(message)


async def join_by_payload(message: Message, state: FSMContext, payload: str) -> bool:
    """Пробует войти в группу по payload ссылки-приглашения."""
    code = invite_code_from_payload(payload)
    if code is None:
        return False
    await _join(message, state, code)
    return True


async def invite_link(bot: Bot, invite_code: str) -> str:
    """Ссылка-приглашение в группу по её коду."""
    me = await bot.me()
    return build_invite_link(me.username or "", invite_code)

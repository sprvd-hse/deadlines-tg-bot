import logging
from datetime import UTC, datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from aiogram import Bot, F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from src.db.repositories.items import ItemRepository
from src.db.session import SessionLocal
from src.domain.humanize import WEEKDAYS_RU_FULL
from src.domain.models import Group
from src.domain.reminders import offsets_label
from src.handlers.groupctl import invite_link, render_group
from src.handlers.ui import Event, notify, show, with_group
from src.scheduler import schedule_item
from src.services.groups import ActiveContext, GroupManager
from src.states import AdminInput
from src.ui import keyboards as kb
from src.ui.callbacks import AdminAction, MemberAction, Nav, OffsetToggle
from src.views import texts as t
from src.views.common import esc

logger = logging.getLogger(__name__)

router = Router(name="settings")
router.message.filter(F.chat.type == "private")

_SCOPE_FIELD = {"group_task": "default_task_offsets", "group_event": "default_event_offsets"}
_SCOPE_PROMPT = {"group_task": t.REMINDERS_EDIT_TASKS, "group_event": t.REMINDERS_EDIT_EVENTS}


# ============ Личные настройки ============


@router.callback_query(Nav.filter(F.to == "settings"))
async def open_settings(event: Event, state: FSMContext, *args, **kwargs) -> None:
    """Личные настройки участника."""
    await state.clear()
    if isinstance(event, CallbackQuery):
        await event.answer()
    await render_settings(event)


@router.callback_query(AdminAction.filter(F.action == "personal"))
async def toggle_personal(callback: CallbackQuery, callback_data: AdminAction) -> None:
    """Включает или выключает личные напоминания."""
    ctx = await with_group(callback)
    if ctx is None:
        return
    async with SessionLocal() as session, session.begin():
        await GroupManager(session).set_personal_reminders(
            group_id=ctx.group.id, user_id=ctx.user.id, enabled=callback_data.value == "1"
        )
    await callback.answer(t.ADMIN_SAVED)
    await render_settings(callback)


# ============ Настройки группы ============


@router.callback_query(Nav.filter(F.to == "admin"))
async def open_admin(callback: CallbackQuery, state: FSMContext) -> None:
    """Экран настроек группы."""
    await state.clear()
    ctx = await _creator_only(callback)
    if ctx is None:
        return
    await callback.answer()
    await _render_admin(callback, ctx)


@router.callback_query(Nav.filter(F.to == "reminders"))
async def open_group_reminders(callback: CallbackQuery) -> None:
    """Экран напоминаний группы."""
    ctx = await _creator_only(callback)
    if ctx is None:
        return
    await callback.answer()
    await _render_group_reminders(callback, ctx)


@router.callback_query(OffsetToggle.filter(F.scope.in_(_SCOPE_FIELD) & (F.action == "edit")))
async def edit_group_offsets(
    callback: CallbackQuery, callback_data: OffsetToggle, state: FSMContext
) -> None:
    """Открывает редактор дефолтных сроков группы."""
    ctx = await _creator_only(callback)
    if ctx is None:
        return
    field = _SCOPE_FIELD[callback_data.scope]
    selected = set(getattr(ctx.group.reminders, _settings_attr(field)))
    await state.update_data(offsets=sorted(selected))
    await callback.answer()
    await show(
        callback,
        _SCOPE_PROMPT[callback_data.scope],
        kb.offsets_keyboard(
            scope=callback_data.scope, target_id=0, selected=selected, allow_reset=False
        ),
    )


@router.callback_query(OffsetToggle.filter(F.scope.in_(_SCOPE_FIELD) & (F.action == "toggle")))
async def toggle_group_offset(
    callback: CallbackQuery, callback_data: OffsetToggle, state: FSMContext
) -> None:
    """Переключает один срок в наборе группы."""
    selected = set((await state.get_data()).get("offsets", []))
    selected ^= {callback_data.minutes}
    await state.update_data(offsets=sorted(selected))
    await callback.answer()
    await show(
        callback,
        _SCOPE_PROMPT[callback_data.scope],
        kb.offsets_keyboard(
            scope=callback_data.scope, target_id=0, selected=selected, allow_reset=False
        ),
    )


@router.callback_query(OffsetToggle.filter(F.scope.in_(_SCOPE_FIELD) & (F.action == "done")))
async def save_group_offsets(
    callback: CallbackQuery, callback_data: OffsetToggle, state: FSMContext
) -> None:
    """Сохраняет дефолтные сроки группы."""
    ctx = await _creator_only(callback)
    if ctx is None:
        return
    offsets = sorted((await state.get_data()).get("offsets", []), reverse=True)
    field = _SCOPE_FIELD[callback_data.scope]
    async with SessionLocal() as session, session.begin():
        group = await GroupManager(session).update_settings(ctx.group.id, **{field: offsets})
    await state.update_data(offsets=None)
    await callback.answer(t.REMINDERS_SAVED)
    await _render_group_reminders(callback, ctx, group=group)
    await _reschedule_group(ctx.group.id)


@router.callback_query(AdminAction.filter(F.action == "hour"))
async def ask_reminder_hour(callback: CallbackQuery) -> None:
    """Выбор часа рассылки напоминаний."""
    if await _creator_only(callback) is None:
        return
    await callback.answer()
    await show(callback, t.REMINDERS_HOUR, kb.hours_keyboard("sethour"))


@router.callback_query(AdminAction.filter(F.action == "sethour"))
async def save_reminder_hour(callback: CallbackQuery, callback_data: AdminAction) -> None:
    """Сохраняет час рассылки напоминаний."""
    ctx = await _creator_only(callback)
    if ctx is None:
        return
    async with SessionLocal() as session, session.begin():
        group = await GroupManager(session).update_settings(
            ctx.group.id, reminder_hour=int(callback_data.value)
        )
    await callback.answer(t.ADMIN_SAVED)
    await _render_group_reminders(callback, ctx, group=group)
    await _reschedule_group(ctx.group.id)


@router.callback_query(AdminAction.filter(F.action == "tz"))
async def ask_timezone(callback: CallbackQuery, state: FSMContext) -> None:
    """Просит часовой пояс."""
    if await _creator_only(callback) is None:
        return
    await state.set_state(AdminInput.timezone)
    await callback.answer()
    await show(callback, t.ADMIN_ASK_TZ)


@router.message(AdminInput.timezone, F.text)
async def save_timezone(message: Message, state: FSMContext) -> None:
    """Сохраняет часовой пояс группы."""
    ctx = await _creator_only(message)
    if ctx is None:
        return
    name = (message.text or "").strip()
    try:
        ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        await message.answer(t.ADMIN_TZ_INVALID)
        return
    async with SessionLocal() as session, session.begin():
        await GroupManager(session).update_settings(ctx.group.id, timezone=name)
    await state.clear()
    await message.answer(t.ADMIN_TZ_SAVED.format(tz=name))
    await _render_admin(message, await _reload(message))
    await _reschedule_group(ctx.group.id)


@router.callback_query(AdminAction.filter(F.action == "digest"))
async def ask_digest_day(callback: CallbackQuery) -> None:
    """Выбор дня недели дайджеста."""
    if await _creator_only(callback) is None:
        return
    await callback.answer()
    await show(callback, t.ADMIN_DIGEST_DAY, kb.weekdays_keyboard())


@router.callback_query(AdminAction.filter(F.action == "setday"))
async def save_digest_day(callback: CallbackQuery, callback_data: AdminAction) -> None:
    """Сохраняет день дайджеста и просит час."""
    ctx = await _creator_only(callback)
    if ctx is None:
        return
    async with SessionLocal() as session, session.begin():
        await GroupManager(session).update_settings(
            ctx.group.id, digest_day=int(callback_data.value)
        )
    await callback.answer(t.ADMIN_SAVED)
    await show(callback, t.ADMIN_DIGEST_HOUR, kb.hours_keyboard("sethourdigest"))


@router.callback_query(AdminAction.filter(F.action == "sethourdigest"))
async def save_digest_hour(callback: CallbackQuery, callback_data: AdminAction) -> None:
    """Сохраняет час дайджеста."""
    ctx = await _creator_only(callback)
    if ctx is None:
        return
    async with SessionLocal() as session, session.begin():
        await GroupManager(session).update_settings(
            ctx.group.id, digest_hour=int(callback_data.value)
        )
    await callback.answer(t.ADMIN_SAVED)
    await _render_admin(callback, await _reload(callback))


@router.callback_query(AdminAction.filter(F.action == "regen"))
async def regenerate_code(callback: CallbackQuery, bot: Bot) -> None:
    """Перевыпускает invite-код."""
    ctx = await _creator_only(callback)
    if ctx is None:
        return
    async with SessionLocal() as session, session.begin():
        code = await GroupManager(session).regenerate_invite_code(ctx.group.id)
    await callback.answer()
    await show(
        callback,
        t.ADMIN_CODE_REGENERATED.format(code=code, link=await invite_link(bot, code)),
        kb.back_keyboard("admin"),
    )


@router.callback_query(AdminAction.filter(F.action == "transfer"))
async def ask_transfer(callback: CallbackQuery) -> None:
    """Выбор нового создателя."""
    ctx = await _creator_only(callback)
    if ctx is None:
        return
    async with SessionLocal() as session:
        members = await GroupManager(session).members(ctx.group.id)
    if len(members) < 2:
        await callback.answer(t.ADMIN_TRANSFER_NOBODY, show_alert=True)
        return
    await callback.answer()
    await show(callback, t.ADMIN_TRANSFER_PICK, kb.members_keyboard(members, action="transfer"))


@router.callback_query(MemberAction.filter(F.action == "transfer"))
async def confirm_transfer(callback: CallbackQuery, callback_data: MemberAction) -> None:
    """Подтверждение передачи роли."""
    ctx = await _creator_only(callback)
    if ctx is None:
        return
    name = await _member_name(ctx.group.id, callback_data.user_id)
    await callback.answer()
    await show(
        callback,
        t.CONFIRM_TRANSFER.format(name=esc(name)),
        kb.confirm_keyboard(
            yes_callback=MemberAction(action="transferyes", user_id=callback_data.user_id).pack(),
            no_callback=Nav(to="admin").pack(),
            yes_text="👑 Передать",
        ),
    )


@router.callback_query(MemberAction.filter(F.action == "transferyes"))
async def do_transfer(callback: CallbackQuery, callback_data: MemberAction, bot: Bot) -> None:
    """Передаёт роль создателя."""
    ctx = await _creator_only(callback)
    if ctx is None:
        return
    name = await _member_name(ctx.group.id, callback_data.user_id)
    async with SessionLocal() as session, session.begin():
        status = await GroupManager(session).transfer_ownership(
            group_id=ctx.group.id,
            current_creator_id=ctx.user.id,
            new_creator_id=callback_data.user_id,
        )
    if status != "ok":
        await callback.answer(t.ADMIN_TRANSFER_NOBODY, show_alert=True)
        return
    await callback.answer(t.ADMIN_TRANSFER_DONE.format(name=name))
    await render_group(callback, bot)


@router.callback_query(AdminAction.filter(F.action == "delete"))
async def confirm_delete_group(callback: CallbackQuery) -> None:
    """Подтверждение удаления группы."""
    ctx = await _creator_only(callback)
    if ctx is None:
        return
    await callback.answer()
    await show(
        callback,
        t.CONFIRM_DELETE_GROUP.format(group=esc(ctx.group.name)),
        kb.confirm_keyboard(
            yes_callback=AdminAction(action="deleteyes").pack(),
            no_callback=Nav(to="admin").pack(),
            yes_text="🗑 Удалить",
        ),
    )


@router.callback_query(AdminAction.filter(F.action == "deleteyes"))
async def do_delete_group(callback: CallbackQuery, bot: Bot) -> None:
    """Удаляет группу."""
    ctx = await _creator_only(callback)
    if ctx is None:
        return
    async with SessionLocal() as session, session.begin():
        await GroupManager(session).delete_group(ctx.group.id)
    await callback.answer(t.GROUP_DELETED)
    await render_group(callback, bot)


# ============ Вспомогательные ============


async def render_settings(event: Event) -> None:
    ctx = await with_group(event)
    if ctx is None:
        return
    enabled = ctx.member.personal_reminders_enabled
    await show(
        event,
        t.SETTINGS_HEADER.format(
            group=esc(ctx.group.name), state=t.SETTINGS_ON if enabled else t.SETTINGS_OFF
        ),
        kb.settings_keyboard(personal_enabled=enabled, is_creator=ctx.is_creator),
    )


async def _render_admin(event: Event, ctx: ActiveContext | None) -> None:
    if ctx is None:
        return
    await show(
        event,
        t.ADMIN_HEADER.format(
            group=esc(ctx.group.name),
            tz=ctx.group.timezone,
            weekday=WEEKDAYS_RU_FULL[ctx.group.digest_day],
            hour=ctx.group.digest_hour,
        ),
        kb.admin_keyboard(),
    )


async def _render_group_reminders(
    event: Event, ctx: ActiveContext, *, group: Group | None = None
) -> None:
    target = group or ctx.group
    await show(
        event,
        t.REMINDERS_HEADER.format(
            group=esc(target.name),
            tasks=offsets_label(target.reminders.task_offsets),
            events=offsets_label(target.reminders.event_offsets),
            hour=target.reminders.reminder_hour,
        ),
        kb.group_reminders_keyboard(),
    )


async def _creator_only(event: Event) -> ActiveContext | None:
    ctx = await with_group(event)
    if ctx is None:
        return None
    if not ctx.is_creator:
        await notify(event, t.CREATOR_ONLY, alert=True)
        return None
    return ctx


async def _reload(event: Event) -> ActiveContext | None:
    return await with_group(event)


async def _member_name(group_id: int, user_id: int) -> str:
    async with SessionLocal() as session:
        members = await GroupManager(session).members(group_id)
    for member in members:
        if member.user.id == user_id:
            return member.user.full_name
    return "?"


async def _reschedule_group(group_id: int) -> None:
    """Перепланирует напоминания всех активных записей группы после смены настроек."""
    now = datetime.now(tz=UTC)
    async with SessionLocal() as session:
        group = await GroupManager(session).get_group(group_id)
        if group is None:
            return
        items = [
            i
            for i in await ItemRepository(session).list_scheduled(after=now)
            if i.group_id == group_id
        ]
    for item in items:
        schedule_item(item, group)


def _settings_attr(field: str) -> str:
    return "task_offsets" if field == "default_task_offsets" else "event_offsets"

import contextlib
import logging
from collections.abc import Awaitable, Callable
from typing import Any

from aiogram.exceptions import TelegramBadRequest
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message

from src.db.session import SessionLocal
from src.services.groups import ActiveContext, GroupContext, GroupManager
from src.views.texts import NO_ACTIVE_GROUP

logger = logging.getLogger(__name__)

Event = Message | CallbackQuery


async def show(event: Event, text: str, markup: InlineKeyboardMarkup | None = None) -> None:
    """Показывает экран: правит текущее сообщение для callback, иначе шлёт новое."""
    if isinstance(event, CallbackQuery) and isinstance(event.message, Message):
        try:
            await event.message.edit_text(text, reply_markup=markup, parse_mode="HTML")
            return
        except TelegramBadRequest as exc:
            if "message is not modified" in str(exc):
                return
            with contextlib.suppress(Exception):
                await event.message.answer(text, reply_markup=markup, parse_mode="HTML")
            return
    message = event if isinstance(event, Message) else event.message
    if message is not None:
        await message.answer(text, reply_markup=markup, parse_mode="HTML")


async def notify(event: Event, text: str, *, alert: bool = False) -> None:
    """Короткое уведомление: всплывашка для callback, сообщение для команды."""
    if isinstance(event, CallbackQuery):
        await event.answer(text, show_alert=alert)
        return
    await event.answer(text, parse_mode="HTML")


def sender(event: Event) -> Any:
    """Пользователь, инициировавший событие."""
    return event.from_user


async def load_context(event: Event) -> GroupContext | None:
    """Загружает пользователя и его активную группу в отдельной транзакции."""
    user = sender(event)
    if user is None:
        return None
    async with SessionLocal() as session, session.begin():
        return await GroupManager(session).context(
            tg_user_id=user.id,
            tg_username=user.username,
            full_name=user.full_name or str(user.id),
        )


async def with_group(event: Event) -> ActiveContext | None:
    """Загружает контекст и сам сообщает пользователю, если активной группы нет."""
    ctx = await load_context(event)
    active = ctx.activated() if ctx is not None else None
    if active is None:
        await show(event, NO_ACTIVE_GROUP)
        return None
    return active


async def in_session(action: Callable[[Any], Awaitable[Any]]) -> Any:
    """Выполняет действие в транзакции и возвращает его результат."""
    async with SessionLocal() as session, session.begin():
        return await action(session)

import contextlib
import logging
from collections.abc import Awaitable, Callable
from typing import Any

from aiogram.exceptions import TelegramBadRequest
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardMarkup,
    InputRichMessage,
    LinkPreviewOptions,
    Message,
)

from src.db.session import SessionLocal
from src.services.groups import ActiveContext, GroupContext, GroupManager
from src.views.texts import NO_ACTIVE_GROUP

logger = logging.getLogger(__name__)

Event = Message | CallbackQuery

# Ссылки в заданиях не должны разворачиваться превью во весь экран.
NO_PREVIEW = LinkPreviewOptions(is_disabled=True)


async def show(event: Event, text: str, markup: InlineKeyboardMarkup | None = None) -> None:
    """Показывает экран: правит текущее сообщение для callback, иначе шлёт новое."""
    if isinstance(event, CallbackQuery) and isinstance(event.message, Message):
        try:
            await event.message.edit_text(
                text,
                reply_markup=markup,
                parse_mode="HTML",
                link_preview_options=NO_PREVIEW,
            )
            return
        except TelegramBadRequest as exc:
            if "message is not modified" in str(exc):
                return
            with contextlib.suppress(Exception):
                await event.message.answer(
                    text, reply_markup=markup, parse_mode="HTML", link_preview_options=NO_PREVIEW
                )
            return
    message = event if isinstance(event, Message) else event.message
    if message is not None:
        await message.answer(
            text, reply_markup=markup, parse_mode="HTML", link_preview_options=NO_PREVIEW
        )


async def show_rich(
    event: Event, rich_html: str, fallback: str, markup: InlineKeyboardMarkup | None = None
) -> None:
    """Показывает экран как rich-сообщение; при отказе Telegram — обычным HTML."""
    rich = InputRichMessage(html=rich_html)
    message = event.message if isinstance(event, CallbackQuery) else event
    if not isinstance(message, Message) or message.bot is None:
        return
    try:
        if isinstance(event, CallbackQuery):
            await message.edit_text(rich_message=rich, reply_markup=markup)
        else:
            await message.bot.send_rich_message(
                chat_id=message.chat.id, rich_message=rich, reply_markup=markup
            )
        return
    except TelegramBadRequest as exc:
        if "message is not modified" in str(exc):
            return
        logger.warning("Rich-сообщение отклонено, показываю обычный текст: %s", exc)
    except Exception:
        logger.exception("Не удалось отправить rich-сообщение")
    await show(event, fallback, markup)


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

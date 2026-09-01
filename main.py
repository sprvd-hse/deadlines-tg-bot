import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.types import (
    BotCommand,
    BotCommandScopeAllGroupChats,
    BotCommandScopeAllPrivateChats,
)

from src.config import settings
from src.handlers import chat, groupctl, items, menu, reply, subjects
from src.handlers import settings as settings_handlers
from src.scheduler import shutdown_scheduler, start_scheduler


async def main() -> None:
    """Точка входа: поднимает бота, планировщик и polling."""
    logging.basicConfig(
        level=settings.log_level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    logger = logging.getLogger(__name__)

    session = AiohttpSession(proxy=settings.bot_proxy_url) if settings.bot_proxy_url else None
    bot = Bot(token=settings.bot_token.get_secret_value(), session=session)
    dp = Dispatcher()
    for router in (
        menu.router,
        # reply-роутер обязан быть выше FSM-роутеров:
        # нажатие нижней кнопки прерывает любой незаконченный ввод.
        reply.router,
        items.router,
        subjects.router,
        groupctl.router,
        settings_handlers.router,
        chat.router,
    ):
        dp.include_router(router)

    me = await bot.get_me()
    logger.info("Запуск бота @%s (id=%s)", me.username, me.id)
    await _setup_bot_commands(bot)
    await start_scheduler(bot)
    try:
        await dp.start_polling(bot)
    finally:
        await shutdown_scheduler()
        await bot.session.close()


async def _setup_bot_commands(bot: Bot) -> None:
    """Регистрирует список команд, показываемый при вводе «/»."""
    private = [
        BotCommand(command="menu", description="Главное меню"),
        BotCommand(command="join", description="Войти в группу по коду"),
        BotCommand(command="cancel", description="Отменить текущий ввод"),
        BotCommand(command="help", description="Справка"),
    ]
    group = [
        BotCommand(command="bind_chat", description="Привязать чат к группе"),
        BotCommand(command="digest", description="Ближайшие дедлайны"),
        BotCommand(command="help", description="Справка"),
    ]
    await bot.set_my_commands(private, scope=BotCommandScopeAllPrivateChats())
    await bot.set_my_commands(group, scope=BotCommandScopeAllGroupChats())


if __name__ == "__main__":
    asyncio.run(main())

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
from src.handlers import (
    assignments,
    common,
    groups,
    menu,
    menu_add,
    menu_admin,
    menu_groups,
    menu_reply,
    menu_settings,
    menu_subjects,
    menu_tasks,
    subjects,
)
from src.scheduler import shutdown_scheduler, start_scheduler


async def main() -> None:
    logging.basicConfig(
        level=settings.log_level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    logger = logging.getLogger(__name__)

    session = AiohttpSession(proxy=settings.bot_proxy_url) if settings.bot_proxy_url else None
    bot = Bot(token=settings.bot_token.get_secret_value(), session=session)
    dp = Dispatcher()
    dp.include_router(common.router)
    dp.include_router(menu.router)
    # menu_reply должен быть до FSM-роутеров — нажатие reply-кнопки отменяет ввод.
    dp.include_router(menu_reply.router)
    dp.include_router(menu_tasks.router)
    dp.include_router(menu_subjects.router)
    dp.include_router(menu_settings.router)
    dp.include_router(menu_groups.router)
    dp.include_router(menu_add.router)
    dp.include_router(menu_admin.router)
    dp.include_router(groups.router)
    dp.include_router(subjects.router)
    dp.include_router(assignments.router)

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
    """Регистрирует список команд (показываются при вводе '/')."""
    private_cmds = [
        BotCommand(command="menu", description="Главное меню"),
        BotCommand(command="add", description="Добавить задание"),
        BotCommand(command="list", description="Список заданий"),
        BotCommand(command="digest", description="Ближайшие дедлайны (14 дней)"),
        BotCommand(command="my_groups", description="Мои группы / переключить"),
        BotCommand(command="settings", description="Личные настройки"),
        BotCommand(command="cancel", description="Отменить текущий ввод"),
        BotCommand(command="help", description="Справка"),
    ]
    group_cmds = [
        BotCommand(command="bind_chat", description="Привязать этот чат к группе"),
        BotCommand(command="digest", description="Ближайшие дедлайны (14 дней)"),
        BotCommand(command="help", description="Справка"),
    ]
    await bot.set_my_commands(private_cmds, scope=BotCommandScopeAllPrivateChats())
    await bot.set_my_commands(group_cmds, scope=BotCommandScopeAllGroupChats())


if __name__ == "__main__":
    asyncio.run(main())

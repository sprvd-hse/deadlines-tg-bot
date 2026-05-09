import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.session.aiohttp import AiohttpSession

from src.config import settings
from src.handlers import (
    assignments,
    common,
    groups,
    menu,
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
    dp.include_router(menu_tasks.router)
    dp.include_router(menu_subjects.router)
    dp.include_router(groups.router)
    dp.include_router(subjects.router)
    dp.include_router(assignments.router)

    me = await bot.get_me()
    logger.info("Запуск бота @%s (id=%s)", me.username, me.id)
    await start_scheduler(bot)
    try:
        await dp.start_polling(bot)
    finally:
        await shutdown_scheduler()
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())

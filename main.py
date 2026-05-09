import asyncio
import logging

from aiogram import Bot, Dispatcher

from src.config import settings
from src.handlers import assignments, common, groups, subjects
from src.scheduler import shutdown_scheduler, start_scheduler


async def main() -> None:
    logging.basicConfig(
        level=settings.log_level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    logger = logging.getLogger(__name__)

    bot = Bot(token=settings.bot_token.get_secret_value())
    dp = Dispatcher()
    dp.include_router(common.router)
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

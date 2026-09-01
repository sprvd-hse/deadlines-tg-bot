import os
import tempfile
from collections.abc import AsyncIterator

_DB_PATH = os.path.join(tempfile.gettempdir(), "group_deadlines_tests.db")
os.environ.setdefault("BOT_TOKEN", "42:test")
os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{_DB_PATH}"

import pytest  # noqa: E402
from sqlalchemy.ext.asyncio import (  # noqa: E402
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from src.db.models import Base  # noqa: E402


@pytest.fixture
async def session() -> AsyncIterator[AsyncSession]:
    """Чистая in-memory база на каждый тест."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with maker() as db:
        yield db
    await engine.dispose()


@pytest.fixture
async def app_db() -> AsyncIterator[None]:
    """Пересоздаёт файловую базу, с которой работают хендлеры через SessionLocal."""
    from src.db.session import engine

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    yield
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)

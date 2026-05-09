from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from src.config import settings


def _apply_sqlite_pragmas(dbapi_connection, _connection_record) -> None:
    """Включаем WAL/foreign_keys/busy_timeout на каждом коннекте SQLite."""
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA synchronous=NORMAL")
    cursor.execute("PRAGMA busy_timeout=5000")
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()


engine = create_async_engine(settings.database_url, echo=False, future=True)

if settings.database_url.startswith("sqlite"):
    event.listen(engine.sync_engine, "connect", _apply_sqlite_pragmas)


SessionLocal = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

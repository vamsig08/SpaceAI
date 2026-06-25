"""Async SQLAlchemy database engine, session factory, and WAL mode configuration.

Provides the async engine with SQLite-specific optimizations (WAL mode, mmap,
cache sizing) and an async session factory for dependency injection.
"""

from collections.abc import AsyncGenerator
from pathlib import Path
from typing import Any

from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.config import Settings
from app.core.logging import get_logger

logger = get_logger(__name__)


def _configure_sqlite_connection(dbapi_connection: Any, connection_record: Any) -> None:
    """Apply SQLite PRAGMA settings on every new connection.

    These PRAGMAs are connection-level and must be set on each new connection,
    not just once at engine creation.
    """
    cursor = dbapi_connection.cursor()
    # WAL mode: enables concurrent readers during writes
    cursor.execute("PRAGMA journal_mode=WAL")
    # 64MB page cache for fast reads
    cursor.execute("PRAGMA cache_size=-65536")
    # 5 second busy timeout to retry on lock contention
    cursor.execute("PRAGMA busy_timeout=5000")
    # Balanced durability: safe against app crashes, not OS crashes
    cursor.execute("PRAGMA synchronous=NORMAL")
    # Temp tables in memory for performance
    cursor.execute("PRAGMA temp_store=MEMORY")
    # 256MB memory-mapped I/O for read performance
    cursor.execute("PRAGMA mmap_size=268435456")
    # Enable foreign key enforcement (off by default in SQLite)
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()


def create_engine(settings: Settings) -> AsyncEngine:
    """Create and configure the async SQLAlchemy engine.

    Ensures the database directory exists and applies SQLite-specific
    connection-level PRAGMAs via event listeners.

    Args:
        settings: Application settings with database path.

    Returns:
        Configured AsyncEngine instance.
    """
    # Ensure database directory exists
    db_path = settings.db_path.resolve()
    db_path.parent.mkdir(parents=True, exist_ok=True)

    engine = create_async_engine(
        settings.database_url,
        echo=settings.debug and settings.env.value == "development",
        pool_pre_ping=True,
        # SQLite doesn't support connection pooling in the traditional sense,
        # but we use StaticPool for single-connection guarantees in async mode
        connect_args={"check_same_thread": False},
    )

    # Register PRAGMA configuration on every new raw connection
    event.listen(engine.sync_engine, "connect", _configure_sqlite_connection)

    logger.info(
        "database_engine_created",
        db_path=str(db_path),
        database_url=settings.database_url,
    )

    return engine


def create_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    """Create an async session factory bound to the given engine.

    Sessions created by this factory:
    - Do NOT auto-commit (explicit commit required)
    - Do NOT auto-flush (explicit flush or commit triggers flush)
    - Expire instances on commit (forces reload on next access)

    Args:
        engine: The async engine to bind sessions to.

    Returns:
        An async_sessionmaker that produces AsyncSession instances.
    """
    return async_sessionmaker(
        bind=engine,
        class_=AsyncSession,
        expire_on_commit=True,
        autocommit=False,
        autoflush=False,
    )


async def get_session(
    session_factory: async_sessionmaker[AsyncSession],
) -> AsyncGenerator[AsyncSession, None]:
    """Dependency that yields a database session and handles cleanup.

    Usage with FastAPI:
        @app.get("/items")
        async def get_items(session: AsyncSession = Depends(get_session)):
            ...

    The session is committed on success and rolled back on exception.

    Args:
        session_factory: The session factory to create sessions from.

    Yields:
        An AsyncSession instance.
    """
    async with session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def verify_database_connection(engine: AsyncEngine) -> bool:
    """Verify the database is accessible and WAL mode is active.

    Args:
        engine: The async engine to test.

    Returns:
        True if connection is healthy, False otherwise.
    """
    try:
        async with engine.begin() as conn:
            result = await conn.execute(text("PRAGMA journal_mode"))
            row = result.fetchone()
            journal_mode = row[0] if row else "unknown"

            result = await conn.execute(text("SELECT sqlite_version()"))
            row = result.fetchone()
            sqlite_version = row[0] if row else "unknown"

        logger.info(
            "database_connection_verified",
            journal_mode=journal_mode,
            sqlite_version=sqlite_version,
        )
        return journal_mode == "wal"
    except Exception as e:
        logger.error("database_connection_failed", error=str(e))
        return False


async def dispose_engine(engine: AsyncEngine) -> None:
    """Gracefully dispose the engine and close all connections.

    Should be called during application shutdown.

    Args:
        engine: The engine to dispose.
    """
    await engine.dispose()
    logger.info("database_engine_disposed")

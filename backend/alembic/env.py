"""Alembic environment configuration for async SQLAlchemy with SQLite.

Supports both offline (SQL generation) and online (live DB) migration modes.
Uses render_as_batch=True for SQLite ALTER TABLE compatibility.
Applies SQLite PRAGMAs (WAL mode, cache, etc.) on connection to ensure
the database file is correctly configured regardless of creation order.
"""

import asyncio
from logging.config import fileConfig
from typing import Any

from alembic import context
from sqlalchemy import event, pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

from app.models.base import Base

# Import all models so Alembic can detect them for autogenerate
from app.models import scan, file, folder, exclusion_rule  # noqa: F401

# Alembic Config object
config = context.config

# Setup logging from alembic.ini
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Target metadata for autogenerate support
target_metadata = Base.metadata


def _configure_sqlite_pragmas(dbapi_connection: Any, connection_record: Any) -> None:
    """Apply critical SQLite PRAGMAs during Alembic migrations.

    WAL mode is persistent on the database file. Setting it here ensures
    the DB is in WAL mode from creation, regardless of whether the app
    has connected yet.
    """
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA busy_timeout=5000")
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode.

    Generates SQL scripts without connecting to the database.
    Uses render_as_batch for SQLite compatibility.
    """
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        render_as_batch=True,
    )

    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    """Execute migrations within a connection context.

    render_as_batch=True is critical for SQLite: it handles ALTER TABLE
    operations by creating a temp table, copying data, dropping original,
    and renaming temp.
    """
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        render_as_batch=True,
        compare_type=True,
        compare_server_default=True,
    )

    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    """Run migrations in 'online' mode using async engine."""
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    # Attach PRAGMA configuration so WAL mode is set on DB creation
    event.listen(connectable.sync_engine, "connect", _configure_sqlite_pragmas)

    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)

    await connectable.dispose()


def run_migrations_online() -> None:
    """Entry point for online migrations — delegates to async runner."""
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()

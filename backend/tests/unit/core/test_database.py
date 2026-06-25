"""Unit tests for database engine creation and session management."""

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.core.database import (
    create_engine,
    create_session_factory,
    dispose_engine,
    verify_database_connection,
)


class TestCreateEngine:
    """Tests for engine creation with SQLite PRAGMAs."""

    async def test_creates_async_engine(self, tmp_path) -> None:
        settings = Settings(db_path=tmp_path / "test.db", debug=False, env="testing")
        engine = create_engine(settings)
        try:
            assert isinstance(engine, AsyncEngine)
            assert "aiosqlite" in str(engine.url)
        finally:
            await engine.dispose()

    async def test_creates_parent_directory(self, tmp_path) -> None:
        settings = Settings(db_path=tmp_path / "nested" / "dir" / "test.db", env="testing")
        engine = create_engine(settings)
        try:
            assert (tmp_path / "nested" / "dir").exists()
        finally:
            await engine.dispose()


class TestVerifyConnection:
    """Tests for database health verification."""

    async def test_returns_true_for_wal_mode(self, tmp_path) -> None:
        settings = Settings(db_path=tmp_path / "wal.db", env="testing")
        engine = create_engine(settings)
        try:
            result = await verify_database_connection(engine)
            assert result is True
        finally:
            await engine.dispose()


class TestSessionFactory:
    """Tests for session factory creation."""

    async def test_creates_working_sessions(self, tmp_path) -> None:
        settings = Settings(db_path=tmp_path / "sess.db", env="testing")
        engine = create_engine(settings)
        try:
            factory = create_session_factory(engine)
            assert isinstance(factory, async_sessionmaker)

            async with factory() as session:
                assert isinstance(session, AsyncSession)
        finally:
            await engine.dispose()


class TestDisposeEngine:
    """Tests for graceful engine disposal."""

    async def test_dispose_does_not_raise(self, tmp_path) -> None:
        settings = Settings(db_path=tmp_path / "disp.db", env="testing")
        engine = create_engine(settings)
        await dispose_engine(engine)

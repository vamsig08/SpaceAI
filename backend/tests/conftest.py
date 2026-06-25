"""Shared test fixtures for all test modules.

Provides:
- In-memory async SQLite database with full schema
- Async session factory
- Pre-populated test data (scan, files, folders)
- FastAPI test client with dependency overrides
"""

import json
import uuid
from collections.abc import AsyncGenerator

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.config import Settings
from app.dependencies import get_session
from app.main import create_app
from app.models.base import Base, utc_now

# Import all models so Base.metadata knows about all tables
import app.models.scan  # noqa: F401
import app.models.file  # noqa: F401
import app.models.folder  # noqa: F401
import app.models.exclusion_rule  # noqa: F401
import app.models.storage_snapshot  # noqa: F401
import app.models.duplicate  # noqa: F401
import app.models.dev_workspace  # noqa: F401
import app.models.recommendation  # noqa: F401
import app.models.prediction  # noqa: F401
import app.models.cleanup_action  # noqa: F401
import app.models.audit_log  # noqa: F401


@pytest.fixture
async def engine() -> AsyncGenerator[AsyncEngine, None]:
    """Create an in-memory SQLite engine with full schema."""
    eng = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        echo=False,
        connect_args={"check_same_thread": False},
    )
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield eng
    await eng.dispose()


@pytest.fixture
async def session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    """Create a session factory bound to the test engine."""
    return async_sessionmaker(
        bind=engine,
        class_=AsyncSession,
        expire_on_commit=False,
        autocommit=False,
        autoflush=False,
    )


@pytest.fixture
async def session(
    session_factory: async_sessionmaker[AsyncSession],
) -> AsyncGenerator[AsyncSession, None]:
    """Yield a session that rolls back after each test."""
    async with session_factory() as sess:
        yield sess
        await sess.rollback()


@pytest.fixture
async def api_client(
    session_factory: async_sessionmaker[AsyncSession],
) -> AsyncGenerator[AsyncClient, None]:
    """Provide an httpx AsyncClient with test DB injected."""
    app = create_app()

    # Override the session dependency to use test DB
    async def _override_session() -> AsyncGenerator[AsyncSession, None]:
        async with session_factory() as sess:
            try:
                yield sess
                await sess.commit()
            except Exception:
                await sess.rollback()
                raise

    app.dependency_overrides[get_session] = _override_session

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client

    app.dependency_overrides.clear()


# ─── Test Data Helpers ─────────────────────────────────────────────────────


def make_scan_id() -> str:
    return str(uuid.uuid4())


def make_file_id() -> str:
    return str(uuid.uuid4())


@pytest.fixture
async def sample_scan(session: AsyncSession) -> dict:
    """Insert a completed scan record and return its data."""
    from sqlalchemy import text

    scan_id = make_scan_id()
    now = utc_now()
    await session.execute(
        text(
            """
            INSERT INTO scans (id, root_path, status, scan_type, started_at, completed_at,
                               total_files, total_dirs, total_size_bytes, files_per_second, created_at)
            VALUES (:id, :root, 'completed', 'full', :started, :completed, 150, 20, 5000000, 1500.0, :created)
            """
        ),
        {
            "id": scan_id,
            "root": "/tmp/test-scan",
            "started": now,
            "completed": now,
            "created": now,
        },
    )
    await session.commit()
    return {"id": scan_id, "root_path": "/tmp/test-scan", "total_files": 150}


@pytest.fixture
async def sample_files(session: AsyncSession, sample_scan: dict) -> list[dict]:
    """Insert sample file records across multiple categories."""
    from sqlalchemy import text

    scan_id = sample_scan["id"]
    now = utc_now()
    files = [
        # Videos (large)
        {"ext": ".mp4", "size": 1000000, "cat": "video", "dir": "/tmp/test-scan/videos"},
        {"ext": ".mkv", "size": 2000000, "cat": "video", "dir": "/tmp/test-scan/videos"},
        # Images
        {"ext": ".jpg", "size": 50000, "cat": "image", "dir": "/tmp/test-scan/photos"},
        {"ext": ".png", "size": 80000, "cat": "image", "dir": "/tmp/test-scan/photos"},
        {"ext": ".png", "size": 60000, "cat": "image", "dir": "/tmp/test-scan/screenshots"},
        # Code
        {"ext": ".py", "size": 5000, "cat": "code", "dir": "/tmp/test-scan/src"},
        {"ext": ".py", "size": 3000, "cat": "code", "dir": "/tmp/test-scan/src"},
        {"ext": ".ts", "size": 7000, "cat": "code", "dir": "/tmp/test-scan/frontend"},
        # Documents
        {"ext": ".pdf", "size": 500000, "cat": "document", "dir": "/tmp/test-scan/docs"},
        {"ext": ".docx", "size": 200000, "cat": "document", "dir": "/tmp/test-scan/docs"},
        # Archives
        {"ext": ".zip", "size": 900000, "cat": "archive", "dir": "/tmp/test-scan/downloads"},
    ]

    rows = []
    for f in files:
        fid = make_file_id()
        rows.append({
            "id": fid,
            "scan_id": scan_id,
            "path": f"{f['dir']}/file{fid[:6]}{f['ext']}",
            "directory": f["dir"],
            "filename": f"file{fid[:6]}{f['ext']}",
            "extension": f["ext"],
            "size_bytes": f["size"],
            "category": f["cat"],
            "modified_at": "2026-01-15T10:00:00.000000Z",
            "accessed_at": "2026-06-01T10:00:00.000000Z",
            "discovered_at": now,
        })

    await session.execute(
        text(
            """
            INSERT INTO files (id, scan_id, path, directory, filename, extension,
                               size_bytes, category, modified_at, accessed_at, discovered_at)
            VALUES (:id, :scan_id, :path, :directory, :filename, :extension,
                    :size_bytes, :category, :modified_at, :accessed_at, :discovered_at)
            """
        ),
        rows,
    )
    await session.commit()
    return rows


@pytest.fixture
async def sample_folders(session: AsyncSession, sample_scan: dict) -> list[dict]:
    """Insert sample folder records."""
    from sqlalchemy import text

    scan_id = sample_scan["id"]
    now = utc_now()
    folders = [
        {"path": "/tmp/test-scan", "name": "test-scan", "parent": None, "depth": 0, "size": 4805000, "files": 11},
        {"path": "/tmp/test-scan/videos", "name": "videos", "parent": "/tmp/test-scan", "depth": 1, "size": 3000000, "files": 2},
        {"path": "/tmp/test-scan/photos", "name": "photos", "parent": "/tmp/test-scan", "depth": 1, "size": 130000, "files": 2},
        {"path": "/tmp/test-scan/src", "name": "src", "parent": "/tmp/test-scan", "depth": 1, "size": 8000, "files": 2},
        {"path": "/tmp/test-scan/docs", "name": "docs", "parent": "/tmp/test-scan", "depth": 1, "size": 700000, "files": 2},
        {"path": "/tmp/test-scan/downloads", "name": "downloads", "parent": "/tmp/test-scan", "depth": 1, "size": 900000, "files": 1},
    ]

    rows = [
        {
            "id": str(uuid.uuid4()),
            "scan_id": scan_id,
            "path": f["path"],
            "name": f["name"],
            "parent_path": f["parent"],
            "depth": f["depth"],
            "total_size_bytes": f["size"],
            "file_count": f["files"],
            "discovered_at": now,
        }
        for f in folders
    ]

    await session.execute(
        text(
            """
            INSERT INTO folders (id, scan_id, path, name, parent_path, depth,
                                 total_size_bytes, file_count, discovered_at)
            VALUES (:id, :scan_id, :path, :name, :parent_path, :depth,
                    :total_size_bytes, :file_count, :discovered_at)
            """
        ),
        rows,
    )
    await session.commit()
    return rows

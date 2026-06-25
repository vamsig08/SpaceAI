"""Unit tests for BatchWriter — batched DB inserts for scan results."""

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.scanner.batch_writer import BatchWriter
from app.scanner.file_info import DirInfo, FileInfo


def _make_file_info(index: int, size: int = 1000) -> FileInfo:
    """Create a FileInfo instance for testing."""
    return FileInfo(
        path=f"/tmp/scan/dir/file_{index}.py",
        directory="/tmp/scan/dir",
        filename=f"file_{index}.py",
        extension=".py",
        size_bytes=size,
        category="code",
        created_at="2026-01-01T00:00:00.000000Z",
        modified_at="2026-01-15T00:00:00.000000Z",
        accessed_at="2026-06-01T00:00:00.000000Z",
        owner="testuser",
        permissions="644",
    )


def _make_dir_info(path: str, depth: int = 0) -> DirInfo:
    """Create a DirInfo instance for testing."""
    name = path.split("/")[-1]
    parent = "/".join(path.split("/")[:-1]) or None
    return DirInfo(path=path, name=name, parent_path=parent, depth=depth)


class TestBatchWriterBuffering:
    """Tests for buffer accumulation logic."""

    async def test_add_file_returns_false_when_not_full(
        self, session_factory: async_sessionmaker[AsyncSession], sample_scan: dict
    ) -> None:
        writer = BatchWriter(session_factory, sample_scan["id"], batch_size=100)
        result = writer.add_file(_make_file_info(1))
        assert result is False
        assert writer.pending_count == 1

    async def test_add_file_returns_true_when_batch_full(
        self, session_factory: async_sessionmaker[AsyncSession], sample_scan: dict
    ) -> None:
        writer = BatchWriter(session_factory, sample_scan["id"], batch_size=5)
        for i in range(4):
            writer.add_file(_make_file_info(i))
        result = writer.add_file(_make_file_info(4))
        assert result is True
        assert writer.pending_count == 5


class TestBatchWriterFlush:
    """Tests for flush operations."""

    async def test_flush_writes_files_to_db(
        self, session: AsyncSession, session_factory: async_sessionmaker[AsyncSession], sample_scan: dict
    ) -> None:
        writer = BatchWriter(session_factory, sample_scan["id"], batch_size=100)
        for i in range(10):
            writer.add_file(_make_file_info(i, size=500 + i))

        count = await writer.flush()

        assert count == 10
        assert writer.total_files_written == 10
        assert writer.pending_count == 0

        # Verify in DB
        async with session_factory() as s:
            result = await s.execute(
                text("SELECT COUNT(*) FROM files WHERE scan_id = :sid"),
                {"sid": sample_scan["id"]},
            )
            db_count = result.scalar_one()
            assert db_count == 10

    async def test_flush_writes_directories_to_db(
        self, session: AsyncSession, session_factory: async_sessionmaker[AsyncSession], sample_scan: dict
    ) -> None:
        writer = BatchWriter(session_factory, sample_scan["id"], batch_size=100)
        writer.add_directory(_make_dir_info("/tmp/scan", depth=0))
        writer.add_directory(_make_dir_info("/tmp/scan/src", depth=1))

        await writer.flush()

        assert writer.total_dirs_written == 2

        async with session_factory() as s:
            result = await s.execute(
                text("SELECT COUNT(*) FROM folders WHERE scan_id = :sid"),
                {"sid": sample_scan["id"]},
            )
            assert result.scalar_one() == 2

    async def test_flush_empty_buffer_returns_zero(
        self, session_factory: async_sessionmaker[AsyncSession], sample_scan: dict
    ) -> None:
        writer = BatchWriter(session_factory, sample_scan["id"], batch_size=100)
        count = await writer.flush()
        assert count == 0

    async def test_total_bytes_tracks_cumulative_size(
        self, session_factory: async_sessionmaker[AsyncSession], sample_scan: dict
    ) -> None:
        writer = BatchWriter(session_factory, sample_scan["id"], batch_size=100)
        writer.add_file(_make_file_info(1, size=1000))
        writer.add_file(_make_file_info(2, size=2000))
        writer.add_file(_make_file_info(3, size=3000))

        await writer.flush()

        assert writer.total_bytes_written == 6000

    async def test_flush_remaining_clears_partial_buffer(
        self, session_factory: async_sessionmaker[AsyncSession], sample_scan: dict
    ) -> None:
        writer = BatchWriter(session_factory, sample_scan["id"], batch_size=100)
        writer.add_file(_make_file_info(1))
        writer.add_file(_make_file_info(2))

        count = await writer.flush_remaining()

        assert count == 2
        assert writer.total_files_written == 2
        assert writer.pending_count == 0

    async def test_flush_remaining_on_empty_buffer_returns_zero(
        self, session_factory: async_sessionmaker[AsyncSession], sample_scan: dict
    ) -> None:
        writer = BatchWriter(session_factory, sample_scan["id"], batch_size=100)
        count = await writer.flush_remaining()
        assert count == 0


class TestBatchWriterMultipleBatches:
    """Tests for multi-batch accumulation."""

    async def test_multiple_flushes_accumulate_totals(
        self, session_factory: async_sessionmaker[AsyncSession], sample_scan: dict
    ) -> None:
        writer = BatchWriter(session_factory, sample_scan["id"], batch_size=5)

        for i in range(5):
            writer.add_file(_make_file_info(i, size=100))
        await writer.flush()

        for i in range(5, 10):
            writer.add_file(_make_file_info(i, size=200))
        await writer.flush()

        assert writer.total_files_written == 10
        assert writer.total_bytes_written == 1500  # 5*100 + 5*200

"""Integration tests for the full duplicate detection pipeline.

Tests the complete 3-stage pipeline with real files on disk:
1. Creates actual duplicate files in a temp directory
2. Inserts file records matching those paths into the test DB
3. Runs the background detection pipeline
4. Verifies correct duplicate groups are discovered
"""

import asyncio
import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models.base import generate_uuid, utc_now
from app.services.duplicate_service import run_duplicate_detection
from app.workers.progress import ProgressReporter
from app.workers.task_manager import TaskState, TaskStatus, TaskType


async def _create_scan_and_files(
    session_factory: async_sessionmaker[AsyncSession],
    tmp_path: Path,
    file_specs: list[tuple[str, bytes]],
) -> tuple[str, list[str]]:
    """Create real files and matching DB records.

    Args:
        session_factory: DB session factory.
        tmp_path: Directory to create files in.
        file_specs: List of (relative_path, content) tuples.

    Returns:
        Tuple of (scan_id, list of file_ids).
    """
    scan_id = generate_uuid()
    now = utc_now()

    async with session_factory() as session:
        await session.execute(
            text(
                """
                INSERT INTO scans (id, root_path, status, scan_type, total_files, created_at)
                VALUES (:id, :root, 'completed', 'full', :count, :now)
                """
            ),
            {"id": scan_id, "root": str(tmp_path), "count": len(file_specs), "now": now},
        )

        file_ids = []
        for rel_path, content in file_specs:
            full_path = tmp_path / rel_path
            full_path.parent.mkdir(parents=True, exist_ok=True)
            full_path.write_bytes(content)

            file_id = generate_uuid()
            file_ids.append(file_id)
            ext = Path(rel_path).suffix or None

            await session.execute(
                text(
                    """
                    INSERT INTO files (id, scan_id, path, directory, filename, extension,
                        size_bytes, category, discovered_at)
                    VALUES (:id, :scan_id, :path, :dir, :name, :ext, :size, 'other', :now)
                    """
                ),
                {
                    "id": file_id,
                    "scan_id": scan_id,
                    "path": str(full_path),
                    "dir": str(full_path.parent),
                    "name": full_path.name,
                    "ext": ext,
                    "size": len(content),
                    "now": now,
                },
            )

        await session.commit()

    return scan_id, file_ids


class TestFullPipeline:
    """Tests for the complete detection pipeline."""

    async def test_detects_exact_duplicates(
        self, session_factory: async_sessionmaker[AsyncSession], tmp_path: Path
    ) -> None:
        """Two files with identical content should form a duplicate group."""
        content = os.urandom(5000)  # 5KB file

        scan_id, file_ids = await _create_scan_and_files(
            session_factory, tmp_path,
            [
                ("dir_a/data.bin", content),
                ("dir_b/data.bin", content),  # Duplicate
                ("unique.txt", b"unique content"),  # Not a duplicate
            ],
        )

        state = TaskState(task_id=scan_id, task_type=TaskType.HASH)
        reporter = ProgressReporter()
        pool = ThreadPoolExecutor(max_workers=2)

        try:
            await run_duplicate_detection(
                task_state=state,
                scan_id=scan_id,
                session_factory=session_factory,
                thread_pool=pool,
                reporter=reporter,
            )
        finally:
            pool.shutdown(wait=False)

        assert state.status == TaskStatus.COMPLETED

        # Verify one duplicate group was created
        async with session_factory() as session:
            result = await session.execute(
                text("SELECT COUNT(*) FROM duplicate_groups WHERE scan_id = :sid"),
                {"sid": scan_id},
            )
            assert result.scalar_one() == 1

            # Verify wasted bytes calculation
            result = await session.execute(
                text("SELECT wasted_bytes, member_count FROM duplicate_groups WHERE scan_id = :sid"),
                {"sid": scan_id},
            )
            row = result.one()
            assert row[1] == 2  # 2 members
            assert row[0] == 5000  # wasted = size * (count - 1) = 5000 * 1

    async def test_detects_multiple_groups(
        self, session_factory: async_sessionmaker[AsyncSession], tmp_path: Path
    ) -> None:
        """Multiple sets of duplicates should form separate groups."""
        content_a = b"A" * 2000
        content_b = b"B" * 3000

        scan_id, _ = await _create_scan_and_files(
            session_factory, tmp_path,
            [
                ("group_a/file1.bin", content_a),
                ("group_a/file2.bin", content_a),
                ("group_a/file3.bin", content_a),
                ("group_b/file1.bin", content_b),
                ("group_b/file2.bin", content_b),
            ],
        )

        state = TaskState(task_id=scan_id, task_type=TaskType.HASH)
        reporter = ProgressReporter()
        pool = ThreadPoolExecutor(max_workers=2)

        try:
            await run_duplicate_detection(
                task_state=state,
                scan_id=scan_id,
                session_factory=session_factory,
                thread_pool=pool,
                reporter=reporter,
            )
        finally:
            pool.shutdown(wait=False)

        async with session_factory() as session:
            result = await session.execute(
                text("SELECT COUNT(*) FROM duplicate_groups WHERE scan_id = :sid"),
                {"sid": scan_id},
            )
            assert result.scalar_one() == 2

            # Group A: 3 members, wasted = 2000 * 2 = 4000
            result = await session.execute(
                text(
                    "SELECT wasted_bytes FROM duplicate_groups "
                    "WHERE scan_id = :sid ORDER BY wasted_bytes DESC"
                ),
                {"sid": scan_id},
            )
            wasted = [row[0] for row in result.all()]
            assert wasted == [4000, 3000]  # A: 2000*2, B: 3000*1

    async def test_no_duplicates_when_all_unique(
        self, session_factory: async_sessionmaker[AsyncSession], tmp_path: Path
    ) -> None:
        """Unique files should produce no duplicate groups."""
        scan_id, _ = await _create_scan_and_files(
            session_factory, tmp_path,
            [
                ("a.bin", os.urandom(5000)),
                ("b.bin", os.urandom(5000)),  # Same size, different content
                ("c.txt", b"unique text content"),
            ],
        )

        state = TaskState(task_id=scan_id, task_type=TaskType.HASH)
        reporter = ProgressReporter()
        pool = ThreadPoolExecutor(max_workers=2)

        try:
            await run_duplicate_detection(
                task_state=state,
                scan_id=scan_id,
                session_factory=session_factory,
                thread_pool=pool,
                reporter=reporter,
            )
        finally:
            pool.shutdown(wait=False)

        async with session_factory() as session:
            result = await session.execute(
                text("SELECT COUNT(*) FROM duplicate_groups WHERE scan_id = :sid"),
                {"sid": scan_id},
            )
            assert result.scalar_one() == 0

    async def test_skips_small_files(
        self, session_factory: async_sessionmaker[AsyncSession], tmp_path: Path
    ) -> None:
        """Files below MIN_DUPLICATE_SIZE (1KB) should be ignored."""
        tiny_content = b"x" * 500  # 500 bytes, below threshold

        scan_id, _ = await _create_scan_and_files(
            session_factory, tmp_path,
            [
                ("tiny_a.txt", tiny_content),
                ("tiny_b.txt", tiny_content),  # Same content but below threshold
            ],
        )

        state = TaskState(task_id=scan_id, task_type=TaskType.HASH)
        reporter = ProgressReporter()
        pool = ThreadPoolExecutor(max_workers=2)

        try:
            await run_duplicate_detection(
                task_state=state,
                scan_id=scan_id,
                session_factory=session_factory,
                thread_pool=pool,
                reporter=reporter,
            )
        finally:
            pool.shutdown(wait=False)

        async with session_factory() as session:
            result = await session.execute(
                text("SELECT COUNT(*) FROM duplicate_groups WHERE scan_id = :sid"),
                {"sid": scan_id},
            )
            assert result.scalar_one() == 0

    async def test_cancellation_stops_pipeline(
        self, session_factory: async_sessionmaker[AsyncSession], tmp_path: Path
    ) -> None:
        """Setting cancel_event should stop processing."""
        content = b"X" * 2000
        scan_id, _ = await _create_scan_and_files(
            session_factory, tmp_path,
            [
                ("a.bin", content),
                ("b.bin", content),
            ],
        )

        state = TaskState(task_id=scan_id, task_type=TaskType.HASH)
        state.cancel_event.set()  # Pre-cancel

        reporter = ProgressReporter()
        pool = ThreadPoolExecutor(max_workers=2)

        try:
            await run_duplicate_detection(
                task_state=state,
                scan_id=scan_id,
                session_factory=session_factory,
                thread_pool=pool,
                reporter=reporter,
            )
        finally:
            pool.shutdown(wait=False)

        assert state.status == TaskStatus.CANCELLED

    async def test_updates_is_duplicate_flag_on_files(
        self, session_factory: async_sessionmaker[AsyncSession], tmp_path: Path
    ) -> None:
        """Files identified as duplicates should have is_duplicate=1."""
        content = os.urandom(3000)

        scan_id, file_ids = await _create_scan_and_files(
            session_factory, tmp_path,
            [
                ("dup1.bin", content),
                ("dup2.bin", content),
                ("unique.bin", os.urandom(4000)),
            ],
        )

        state = TaskState(task_id=scan_id, task_type=TaskType.HASH)
        reporter = ProgressReporter()
        pool = ThreadPoolExecutor(max_workers=2)

        try:
            await run_duplicate_detection(
                task_state=state,
                scan_id=scan_id,
                session_factory=session_factory,
                thread_pool=pool,
                reporter=reporter,
            )
        finally:
            pool.shutdown(wait=False)

        async with session_factory() as session:
            result = await session.execute(
                text("SELECT COUNT(*) FROM files WHERE scan_id = :sid AND is_duplicate = 1"),
                {"sid": scan_id},
            )
            assert result.scalar_one() == 2  # The two duplicates

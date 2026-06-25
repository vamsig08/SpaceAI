"""Integration tests for the filesystem crawler with a real temp directory."""

import asyncio
import os
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.scanner.crawler import CycleDetector, run_scan, _scan_directory_sync, _collect_file_info_sync
from app.scanner.exclusions import ExclusionEngine
from app.workers.progress import ProgressReporter
from app.workers.task_manager import TaskState, TaskStatus, TaskType


class TestCycleDetector:
    """Tests for symlink cycle detection."""

    def test_first_visit_returns_false(self, tmp_path: Path) -> None:
        detector = CycleDetector()
        assert detector.check_and_record(tmp_path) is False

    def test_second_visit_returns_true(self, tmp_path: Path) -> None:
        detector = CycleDetector()
        detector.check_and_record(tmp_path)
        assert detector.check_and_record(tmp_path) is True

    def test_different_dirs_not_cycles(self, tmp_path: Path) -> None:
        dir_a = tmp_path / "a"
        dir_b = tmp_path / "b"
        dir_a.mkdir()
        dir_b.mkdir()

        detector = CycleDetector()
        assert detector.check_and_record(dir_a) is False
        assert detector.check_and_record(dir_b) is False

    def test_nonexistent_path_returns_false(self) -> None:
        detector = CycleDetector()
        assert detector.check_and_record(Path("/nonexistent/path/xyz")) is False


class TestScanDirectorySync:
    """Tests for the synchronous directory listing function."""

    def test_lists_files_and_dirs(self, tmp_path: Path) -> None:
        (tmp_path / "file1.txt").write_text("hello")
        (tmp_path / "file2.py").write_text("x = 1")
        (tmp_path / "subdir").mkdir()

        entries = _scan_directory_sync(str(tmp_path))
        names = {e.name for e in entries}

        assert "file1.txt" in names
        assert "file2.py" in names
        assert "subdir" in names

    def test_returns_empty_for_nonexistent_dir(self) -> None:
        entries = _scan_directory_sync("/nonexistent/path/xyz")
        assert entries == []

    def test_returns_empty_for_permission_denied(self, tmp_path: Path) -> None:
        restricted = tmp_path / "restricted"
        restricted.mkdir()
        restricted.chmod(0o000)
        try:
            entries = _scan_directory_sync(str(restricted))
            assert entries == []
        finally:
            restricted.chmod(0o755)


class TestCollectFileInfoSync:
    """Tests for batch metadata collection."""

    def test_collects_file_entries_only(self, tmp_path: Path) -> None:
        (tmp_path / "file.py").write_text("code")
        (tmp_path / "subdir").mkdir()

        entries = list(os.scandir(str(tmp_path)))
        infos = _collect_file_info_sync(entries)

        # Should only include files, not directories
        assert len(infos) == 1
        assert infos[0].filename == "file.py"

    def test_handles_multiple_files(self, tmp_path: Path) -> None:
        for i in range(10):
            (tmp_path / f"file_{i}.txt").write_text(f"content {i}")

        entries = list(os.scandir(str(tmp_path)))
        infos = _collect_file_info_sync(entries)
        assert len(infos) == 10


class TestRunScan:
    """Integration tests for full scan execution."""

    async def test_scans_simple_directory(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        sample_scan: dict,
        tmp_path: Path,
    ) -> None:
        # Create a realistic directory structure
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "main.py").write_text("print('hello')")
        (tmp_path / "src" / "utils.py").write_text("def helper(): pass")
        (tmp_path / "docs").mkdir()
        (tmp_path / "docs" / "readme.md").write_text("# Project")
        (tmp_path / "data.csv").write_text("a,b,c\n1,2,3")

        # Update the scan record to point to our tmp_path
        async with session_factory() as sess:
            await sess.execute(
                text("UPDATE scans SET root_path = :path WHERE id = :id"),
                {"path": str(tmp_path), "id": sample_scan["id"]},
            )
            await sess.commit()

        state = TaskState(task_id=sample_scan["id"], task_type=TaskType.SCAN)
        reporter = ProgressReporter()
        exclusion_engine = ExclusionEngine.create(db_rules=[], include_platform_defaults=False)

        from concurrent.futures import ThreadPoolExecutor
        pool = ThreadPoolExecutor(max_workers=2)

        try:
            await run_scan(
                task_state=state,
                root_path=str(tmp_path),
                scan_id=sample_scan["id"],
                session_factory=session_factory,
                thread_pool=pool,
                exclusion_engine=exclusion_engine,
                reporter=reporter,
                batch_size=10,
                checkpoint_interval=100,
            )
        finally:
            pool.shutdown(wait=False)

        assert state.status == TaskStatus.COMPLETED

        # Verify files were inserted
        async with session_factory() as sess:
            result = await sess.execute(
                text("SELECT COUNT(*) FROM files WHERE scan_id = :sid"),
                {"sid": sample_scan["id"]},
            )
            file_count = result.scalar_one()
            assert file_count == 4  # 4 files created

            # Verify scan record updated
            result = await sess.execute(
                text("SELECT status, total_files FROM scans WHERE id = :id"),
                {"id": sample_scan["id"]},
            )
            row = result.one()
            assert row[0] == "completed"
            assert row[1] == 4

    async def test_respects_exclusions(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        sample_scan: dict,
        tmp_path: Path,
    ) -> None:
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "app.py").write_text("x")
        (tmp_path / "node_modules").mkdir()
        (tmp_path / "node_modules" / "pkg.js").write_text("y")
        (tmp_path / ".git").mkdir()
        (tmp_path / ".git" / "config").write_text("z")

        async with session_factory() as sess:
            await sess.execute(
                text("UPDATE scans SET root_path = :path WHERE id = :id"),
                {"path": str(tmp_path), "id": sample_scan["id"]},
            )
            await sess.commit()

        state = TaskState(task_id=sample_scan["id"], task_type=TaskType.SCAN)
        reporter = ProgressReporter()
        exclusion_engine = ExclusionEngine.create(
            db_rules=[("node_modules", "name"), (".git", "name")],
            include_platform_defaults=False,
        )

        from concurrent.futures import ThreadPoolExecutor
        pool = ThreadPoolExecutor(max_workers=2)

        try:
            await run_scan(
                task_state=state,
                root_path=str(tmp_path),
                scan_id=sample_scan["id"],
                session_factory=session_factory,
                thread_pool=pool,
                exclusion_engine=exclusion_engine,
                reporter=reporter,
                batch_size=10,
                checkpoint_interval=100,
            )
        finally:
            pool.shutdown(wait=False)

        # Only src/app.py should be included
        async with session_factory() as sess:
            result = await sess.execute(
                text("SELECT COUNT(*) FROM files WHERE scan_id = :sid"),
                {"sid": sample_scan["id"]},
            )
            assert result.scalar_one() == 1

    async def test_handles_nonexistent_root(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        sample_scan: dict,
    ) -> None:
        state = TaskState(task_id=sample_scan["id"], task_type=TaskType.SCAN)
        reporter = ProgressReporter()
        exclusion_engine = ExclusionEngine.create(db_rules=[], include_platform_defaults=False)

        from concurrent.futures import ThreadPoolExecutor
        pool = ThreadPoolExecutor(max_workers=2)

        try:
            with pytest.raises(FileNotFoundError):
                await run_scan(
                    task_state=state,
                    root_path="/nonexistent/path/xyz",
                    scan_id=sample_scan["id"],
                    session_factory=session_factory,
                    thread_pool=pool,
                    exclusion_engine=exclusion_engine,
                    reporter=reporter,
                )
        finally:
            pool.shutdown(wait=False)

    async def test_cancellation_stops_scan(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        sample_scan: dict,
        tmp_path: Path,
    ) -> None:
        # Create many subdirectories to give time for cancellation
        for i in range(20):
            d = tmp_path / f"dir_{i}"
            d.mkdir()
            (d / f"file_{i}.txt").write_text(f"content {i}")

        async with session_factory() as sess:
            await sess.execute(
                text("UPDATE scans SET root_path = :path WHERE id = :id"),
                {"path": str(tmp_path), "id": sample_scan["id"]},
            )
            await sess.commit()

        state = TaskState(task_id=sample_scan["id"], task_type=TaskType.SCAN)
        # Pre-set cancellation
        state.cancel_event.set()

        reporter = ProgressReporter()
        exclusion_engine = ExclusionEngine.create(db_rules=[], include_platform_defaults=False)

        from concurrent.futures import ThreadPoolExecutor
        pool = ThreadPoolExecutor(max_workers=2)

        try:
            await run_scan(
                task_state=state,
                root_path=str(tmp_path),
                scan_id=sample_scan["id"],
                session_factory=session_factory,
                thread_pool=pool,
                exclusion_engine=exclusion_engine,
                reporter=reporter,
                batch_size=10,
                checkpoint_interval=5,
            )
        finally:
            pool.shutdown(wait=False)

        assert state.status == TaskStatus.CANCELLED

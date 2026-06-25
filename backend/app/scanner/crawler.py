"""High-performance filesystem crawler with checkpoint recovery.

Uses os.scandir() for optimal performance (3-5x faster than os.walk)
with multi-threaded stat collection via ThreadPoolExecutor.
Supports cooperative cancellation, checkpoint-based resume, and
configurable depth limits.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import TYPE_CHECKING

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.logging import get_logger
from app.models.base import utc_now
from app.scanner.batch_writer import BatchWriter
from app.scanner.exclusions import ExclusionEngine
from app.scanner.file_info import DirInfo, FileInfo
from app.workers.progress import ProgressReporter
from app.workers.task_manager import TaskState, TaskStatus

if TYPE_CHECKING:
    pass

logger = get_logger(__name__)


class CycleDetector:
    """Detects filesystem cycles via inode/device tracking.

    Prevents infinite loops when following symlinks that create
    circular directory references.
    """

    def __init__(self) -> None:
        self._visited: set[tuple[int, int]] = set()  # (device, inode)

    def check_and_record(self, path: Path) -> bool:
        """Check if a directory has already been visited.

        Args:
            path: Directory path to check.

        Returns:
            True if this is a cycle (already visited), False if new.
        """
        try:
            stat = path.stat()
            key = (stat.st_dev, stat.st_ino)
            if key in self._visited:
                return True
            self._visited.add(key)
            return False
        except OSError:
            return False


def _scan_directory_sync(directory: str) -> list[os.DirEntry[str]]:
    """Synchronous directory listing via os.scandir (runs in thread pool).

    Args:
        directory: Absolute path to scan.

    Returns:
        List of DirEntry objects, or empty list on error.
    """
    try:
        return list(os.scandir(directory))
    except PermissionError:
        return []
    except OSError:
        return []


def _collect_file_info_sync(entries: list[os.DirEntry[str]]) -> list[FileInfo]:
    """Collect FileInfo from DirEntry objects (runs in thread pool).

    Processes all entries in a batch, skipping any that fail.

    Args:
        entries: List of DirEntry objects representing files.

    Returns:
        List of successfully collected FileInfo objects.
    """
    results: list[FileInfo] = []
    for entry in entries:
        try:
            if not entry.is_file(follow_symlinks=True):
                continue
            info = FileInfo.from_dir_entry(entry)
            if info is not None:
                results.append(info)
        except (OSError, ValueError):
            continue
    return results


async def run_scan(
    task_state: TaskState,
    root_path: str,
    scan_id: str,
    session_factory: async_sessionmaker[AsyncSession],
    thread_pool: ThreadPoolExecutor,
    exclusion_engine: ExclusionEngine,
    reporter: ProgressReporter,
    batch_size: int = 1000,
    checkpoint_interval: int = 10000,
    max_depth: int | None = None,
    resume_from: str | None = None,
) -> None:
    """Execute a full filesystem scan as a background task.

    This is the main scan coroutine submitted to the TaskManager. It:
    1. Walks the directory tree using BFS (breadth-first)
    2. Collects file metadata via thread pool workers
    3. Writes results in batches to the database
    4. Emits progress events for SSE subscribers
    5. Saves checkpoints for crash recovery
    6. Checks for cancellation every directory

    Args:
        task_state: Mutable task state (for cancellation and progress).
        root_path: Absolute path to start scanning from.
        scan_id: Database scan record ID.
        session_factory: Factory to create DB sessions for writes.
        thread_pool: Thread pool for blocking filesystem I/O.
        exclusion_engine: Configured exclusion rules.
        reporter: Progress reporter for SSE fan-out.
        batch_size: Records per DB batch insert.
        checkpoint_interval: Files between checkpoint saves.
        max_depth: Maximum directory depth (None = unlimited).
        resume_from: Directory path to resume from (checkpoint recovery).
    """
    loop = asyncio.get_event_loop()
    writer = BatchWriter(session_factory, scan_id, batch_size)
    cycle_detector = CycleDetector()

    root = Path(root_path).resolve()
    if not root.exists() or not root.is_dir():
        raise FileNotFoundError(f"Scan root does not exist or is not a directory: {root}")

    # Mark scan as running
    async with session_factory() as session:
        await session.execute(
            text("UPDATE scans SET status = :status, started_at = :started_at WHERE id = :id"),
            {"status": "running", "started_at": utc_now(), "id": scan_id},
        )
        await session.commit()

    start_time = time.time()
    last_progress_time = start_time
    files_since_last_checkpoint = 0
    last_wal_checkpoint = start_time
    skipping_for_resume = resume_from is not None

    # ── Disk space preflight check ──
    try:
        disk_usage = os.statvfs(root_path)
        free_bytes = disk_usage.f_bavail * disk_usage.f_frsize
        min_required = 500 * 1024 * 1024  # 500 MB minimum
        if free_bytes < min_required:
            logger.warning(
                "scan_low_disk_space",
                scan_id=scan_id,
                free_bytes=free_bytes,
                required_bytes=min_required,
            )
            # Don't block — just warn. The scan may still succeed for small dirs.
    except OSError:
        pass  # statvfs not available (Windows) — skip check

    # BFS queue: (directory_path, depth)
    queue: deque[tuple[Path, int]] = deque()
    queue.append((root, 0))

    try:
        while queue:
            # ── Cancellation check ──
            if task_state.cancel_event.is_set():
                task_state.status = TaskStatus.CANCELLED
                await _save_checkpoint(
                    session_factory,
                    scan_id,
                    task_state.progress.current_directory,
                    writer.total_files_written,
                )
                await writer.flush_remaining()
                await reporter.emit(
                    scan_id,
                    __import__("app.workers.progress", fromlist=["ProgressEvent"]).ProgressEvent(
                        event_type="cancelled",
                        data={
                            "files_scanned_so_far": writer.total_files_written,
                            "checkpoint_saved": True,
                        },
                    ),
                )
                return

            current_dir, depth = queue.popleft()

            # ── Depth limit check ──
            if max_depth is not None and depth > max_depth:
                continue

            # ── Resume: skip already-processed directories ──
            if skipping_for_resume:
                if str(current_dir) == resume_from:
                    skipping_for_resume = False
                else:
                    continue

            # ── Exclusion check ──
            if exclusion_engine.should_exclude_directory(current_dir):
                continue

            # ── Cycle detection ──
            if cycle_detector.check_and_record(current_dir):
                logger.debug("cycle_detected", path=str(current_dir))
                continue

            # ── List directory contents (in thread pool) ──
            entries = await loop.run_in_executor(
                thread_pool, _scan_directory_sync, str(current_dir)
            )

            if not entries:
                # Permission denied or empty — always non-fatal
                task_state.progress.errors_skipped += 1
                continue

            # Separate files and subdirectories
            file_entries: list[os.DirEntry[str]] = []
            sub_dirs: list[Path] = []

            for entry in entries:
                try:
                    if entry.is_dir(follow_symlinks=True):
                        sub_dir = Path(entry.path)
                        if not exclusion_engine.should_exclude_directory(sub_dir):
                            sub_dirs.append(sub_dir)
                    elif entry.is_file(follow_symlinks=True):
                        if not exclusion_engine.should_exclude_file(Path(entry.path)):
                            file_entries.append(entry)
                except OSError:
                    task_state.progress.errors_skipped += 1

            # ── Collect file metadata (in thread pool) ──
            file_infos: list[FileInfo] = []
            if file_entries:
                file_infos = await loop.run_in_executor(
                    thread_pool, _collect_file_info_sync, file_entries
                )

            # ── Buffer files for batch write ──
            for info in file_infos:
                needs_flush = writer.add_file(info)
                if needs_flush:
                    await writer.flush()

            # ── Record directory ──
            dir_path_str = str(current_dir).replace("\\", "/")
            parent_str = str(current_dir.parent).replace("\\", "/") if current_dir != root else None
            dir_info = DirInfo(
                path=dir_path_str,
                name=current_dir.name or dir_path_str,
                parent_path=parent_str,
                depth=depth,
            )
            writer.add_directory(dir_info)

            # ── Enqueue subdirectories ──
            for sub_dir in sub_dirs:
                queue.append((sub_dir, depth + 1))

            # ── Update progress state ──
            task_state.progress.files_scanned = writer.total_files_written + writer.pending_count
            task_state.progress.dirs_scanned = writer.total_dirs_written + 1
            task_state.progress.total_bytes_scanned = writer.total_bytes_written
            task_state.progress.current_directory = dir_path_str

            # ── Emit progress (time-based: every 1 second) ──
            now = time.time()
            elapsed = now - start_time
            if now - last_progress_time >= 1.0:
                fps = writer.total_files_written / elapsed if elapsed > 0 else 0
                await reporter.emit_progress(
                    task_id=scan_id,
                    files_scanned=writer.total_files_written + writer.pending_count,
                    dirs_scanned=writer.total_dirs_written,
                    current_directory=dir_path_str,
                    total_bytes_scanned=writer.total_bytes_written,
                    files_per_second=fps,
                    errors_skipped=task_state.progress.errors_skipped,
                )
                last_progress_time = now

            # ── Checkpoint (every N files) ──
            files_since_last_checkpoint += len(file_infos)
            if files_since_last_checkpoint >= checkpoint_interval:
                await writer.flush()
                await _save_checkpoint(
                    session_factory, scan_id, dir_path_str, writer.total_files_written
                )
                files_since_last_checkpoint = 0
                task_state.progress.checkpoint_count += 1

                # ── WAL checkpoint (every 100K files to prevent unbounded growth) ──
                if writer.total_files_written % 100000 < checkpoint_interval:
                    try:
                        async with session_factory() as session:
                            await session.execute(text("PRAGMA wal_checkpoint(PASSIVE)"))
                            await session.commit()
                    except Exception:
                        pass  # Non-fatal — WAL checkpoint failure doesn't affect data

        # ── Scan complete: flush remaining and finalize ──
        await writer.flush_remaining()

        elapsed = time.time() - start_time
        fps = writer.total_files_written / elapsed if elapsed > 0 else 0

        # Determine final status based on errors encountered
        final_status = "completed"
        if task_state.progress.errors_skipped > 0:
            final_status = "completed_with_warnings"

        # Update scan record with final stats
        async with session_factory() as session:
            await session.execute(
                text(
                    """
                    UPDATE scans SET
                        status = :status,
                        completed_at = :completed_at,
                        total_files = :total_files,
                        total_dirs = :total_dirs,
                        total_size_bytes = :total_size_bytes,
                        files_per_second = :fps,
                        checkpoint_data = NULL
                    WHERE id = :id
                    """
                ),
                {
                    "status": final_status,
                    "completed_at": utc_now(),
                    "total_files": writer.total_files_written,
                    "total_dirs": writer.total_dirs_written,
                    "total_size_bytes": writer.total_bytes_written,
                    "fps": round(fps, 1),
                    "id": scan_id,
                },
            )
            await session.commit()

        # Emit completion event
        await reporter.emit_completed(
            task_id=scan_id,
            scan_id=scan_id,
            total_files=writer.total_files_written,
            total_dirs=writer.total_dirs_written,
            total_bytes=writer.total_bytes_written,
            duration_seconds=elapsed,
            files_per_second=fps,
            errors_skipped=task_state.progress.errors_skipped,
        )

        task_state.status = TaskStatus.COMPLETED
        logger.info(
            "scan_completed",
            scan_id=scan_id,
            total_files=writer.total_files_written,
            total_dirs=writer.total_dirs_written,
            total_bytes=writer.total_bytes_written,
            duration_seconds=round(elapsed, 2),
            files_per_second=round(fps, 1),
            errors_skipped=task_state.progress.errors_skipped,
        )

    except Exception as e:
        # Flush whatever we have and mark scan as failed
        try:
            await writer.flush_remaining()
        except Exception:
            pass

        async with session_factory() as session:
            await session.execute(
                text(
                    """
                    UPDATE scans SET
                        status = 'failed',
                        completed_at = :completed_at,
                        error_message = :error,
                        total_files = :total_files,
                        total_dirs = :total_dirs,
                        total_size_bytes = :total_size_bytes
                    WHERE id = :id
                    """
                ),
                {
                    "completed_at": utc_now(),
                    "error": f"{type(e).__name__}: {e}",
                    "total_files": writer.total_files_written,
                    "total_dirs": writer.total_dirs_written,
                    "total_size_bytes": writer.total_bytes_written,
                    "id": scan_id,
                },
            )
            await session.commit()

        await reporter.emit_failed(
            task_id=scan_id,
            error_type=type(e).__name__,
            message=str(e),
            files_scanned=writer.total_files_written,
        )

        raise


async def _save_checkpoint(
    session_factory: async_sessionmaker[AsyncSession],
    scan_id: str,
    last_directory: str,
    files_so_far: int,
) -> None:
    """Persist checkpoint data to the scan record for crash recovery.

    Args:
        session_factory: Session factory for DB access.
        scan_id: The scan to checkpoint.
        last_directory: Last fully processed directory path.
        files_so_far: Total files written at checkpoint time.
    """
    checkpoint = json.dumps({
        "last_directory": last_directory,
        "files_so_far": files_so_far,
        "timestamp": utc_now(),
    })

    async with session_factory() as session:
        await session.execute(
            text("UPDATE scans SET checkpoint_data = :checkpoint WHERE id = :id"),
            {"checkpoint": checkpoint, "id": scan_id},
        )
        await session.commit()

    logger.debug(
        "scan_checkpoint_saved",
        scan_id=scan_id,
        last_directory=last_directory,
        files_so_far=files_so_far,
    )

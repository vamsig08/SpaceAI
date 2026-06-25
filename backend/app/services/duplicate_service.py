"""Duplicate detection service — orchestrates the 3-stage pipeline.

Stage 1: Size grouping (SQL query to find files sharing the same size)
Stage 2: Partial hash (first 4KB + last 4KB) to narrow candidates
Stage 3: Full SHA256 hash for definitive duplicate confirmation

Integrates with TaskManager for background execution and ProgressReporter
for real-time SSE progress updates.
"""

from __future__ import annotations

import asyncio
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from sqlalchemy import func, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.exceptions import ConflictError, NotFoundError
from app.core.logging import get_logger
from app.models.base import generate_uuid, utc_now
from app.models.duplicate import DuplicateGroup, DuplicateMember
from app.models.file import File
from app.repositories.duplicate_repository import DuplicateRepository
from app.scanner.hasher import compute_full_hash, compute_partial_hash
from app.workers.progress import ProgressReporter
from app.workers.task_manager import TaskState, TaskStatus, TaskType

logger = get_logger(__name__)

# Minimum file size to consider for duplicate detection (skip tiny files)
MIN_DUPLICATE_SIZE = 1024  # 1KB — files smaller than this aren't worth deduplicating


class DuplicateService:
    """Orchestrates duplicate detection across the 3-stage pipeline."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._repo = DuplicateRepository(session)

    async def get_summary(self, scan_id: str) -> dict[str, Any]:
        """Get duplicate detection summary for a scan.

        Args:
            scan_id: Scan to summarize.

        Returns:
            Summary dict with group count, file count, wasted bytes, top extensions.
        """
        summary = await self._repo.get_summary(scan_id)
        top_ext = await self._repo.get_top_extensions(scan_id, limit=10)
        summary["top_extensions"] = top_ext
        return summary

    async def list_groups(
        self,
        scan_id: str,
        *,
        page: int = 1,
        page_size: int = 50,
        min_wasted: int | None = None,
        status: str | None = None,
    ) -> dict[str, Any]:
        """List duplicate groups with pagination.

        Args:
            scan_id: Scan to query.
            page: Page number (1-indexed).
            page_size: Results per page.
            min_wasted: Minimum wasted bytes filter.
            status: Status filter.

        Returns:
            Dict with groups list, pagination meta.
        """
        offset = (page - 1) * page_size
        groups = await self._repo.find_groups_by_scan(
            scan_id, offset=offset, limit=page_size,
            min_wasted=min_wasted, status=status,
        )
        total = await self._repo.count_groups(scan_id)
        total_pages = (total + page_size - 1) // page_size

        return {
            "groups": [
                {
                    "id": g.id,
                    "sha256_hash": g.sha256_hash,
                    "file_size_bytes": g.file_size_bytes,
                    "member_count": g.member_count,
                    "wasted_bytes": g.wasted_bytes,
                    "status": g.status,
                    "created_at": g.created_at,
                }
                for g in groups
            ],
            "meta": {
                "page": page,
                "page_size": page_size,
                "total_items": total,
                "total_pages": total_pages,
            },
        }

    async def get_group_detail(self, group_id: str) -> dict[str, Any]:
        """Get full details of a duplicate group including all members.

        Args:
            group_id: Group ID.

        Returns:
            Dict with group metadata and member list.

        Raises:
            NotFoundError: If group doesn't exist.
        """
        group = await self._repo.get_group_with_members(group_id)
        if group is None:
            raise NotFoundError("duplicate_group", group_id)

        return {
            "id": group.id,
            "sha256_hash": group.sha256_hash,
            "file_size_bytes": group.file_size_bytes,
            "member_count": group.member_count,
            "wasted_bytes": group.wasted_bytes,
            "status": group.status,
            "created_at": group.created_at,
            "members": [
                {
                    "id": m.id,
                    "file_id": m.file_id,
                    "path": m.path,
                    "is_keeper": bool(m.is_keeper),
                }
                for m in group.members
            ],
        }

    async def resolve_group(
        self, group_id: str, keep_file_id: str
    ) -> dict[str, Any]:
        """Mark a file as the keeper in a duplicate group.

        Args:
            group_id: The duplicate group to resolve.
            keep_file_id: The file to keep.

        Returns:
            Dict confirming resolution.

        Raises:
            NotFoundError: If group or file not found.
        """
        group = await self._repo.get_group_with_members(group_id)
        if group is None:
            raise NotFoundError("duplicate_group", group_id)

        success = await self._repo.mark_keeper(group_id, keep_file_id)
        if not success:
            raise NotFoundError("duplicate_member", keep_file_id)

        return {
            "group_id": group_id,
            "keeper_file_id": keep_file_id,
            "status": "resolved",
            "files_to_cleanup": group.member_count - 1,
            "recoverable_bytes": group.wasted_bytes,
        }


async def run_duplicate_detection(
    task_state: TaskState,
    scan_id: str,
    session_factory: async_sessionmaker[AsyncSession],
    thread_pool: ThreadPoolExecutor,
    reporter: ProgressReporter,
) -> None:
    """Execute the full 3-stage duplicate detection pipeline as a background task.

    This is the task function submitted to TaskManager. It:
    1. Finds size-group candidates (Stage 1)
    2. Computes partial hashes to narrow candidates (Stage 2)
    3. Computes full SHA256 hashes for confirmation (Stage 3)
    4. Creates duplicate groups and members in the database

    Args:
        task_state: Mutable state for progress tracking and cancellation.
        scan_id: The scan to analyze for duplicates.
        session_factory: DB session factory.
        thread_pool: Thread pool for blocking I/O (hashing).
        reporter: Progress reporter for SSE updates.
    """
    loop = asyncio.get_event_loop()
    now = utc_now()

    # ── Stage 1: Find size-group candidates ─────────────────────────────────
    logger.info("duplicate_stage1_start", scan_id=scan_id)

    async with session_factory() as session:
        # Find file sizes that appear more than once (duplicate candidates)
        stmt = text(
            """
            SELECT size_bytes, COUNT(*) as cnt
            FROM files
            WHERE scan_id = :scan_id AND size_bytes >= :min_size
            GROUP BY size_bytes
            HAVING COUNT(*) > 1
            ORDER BY size_bytes DESC
            """
        )
        result = await session.execute(
            stmt, {"scan_id": scan_id, "min_size": MIN_DUPLICATE_SIZE}
        )
        size_groups = result.all()

    total_candidates = sum(row[1] for row in size_groups)
    logger.info(
        "duplicate_stage1_complete",
        scan_id=scan_id,
        size_groups=len(size_groups),
        total_candidates=total_candidates,
    )

    if not size_groups:
        # No duplicates possible
        task_state.status = TaskStatus.COMPLETED
        await reporter.emit_completed(
            task_id=scan_id, scan_id=scan_id,
            total_files=0, total_dirs=0, total_bytes=0,
            duration_seconds=0, files_per_second=0,
        )
        return

    # ── Stage 2: Partial hash candidates ────────────────────────────────────
    logger.info("duplicate_stage2_start", scan_id=scan_id, candidates=total_candidates)

    files_processed = 0
    partial_hash_groups: dict[str, list[dict[str, Any]]] = defaultdict(list)

    for size_bytes, count in size_groups:
        if task_state.cancel_event.is_set():
            task_state.status = TaskStatus.CANCELLED
            return

        # Fetch file paths for this size group
        async with session_factory() as session:
            stmt = text(
                """
                SELECT id, path, size_bytes FROM files
                WHERE scan_id = :scan_id AND size_bytes = :size
                """
            )
            result = await session.execute(
                stmt, {"scan_id": scan_id, "size": size_bytes}
            )
            candidates = result.all()

        # Compute partial hashes in thread pool
        for file_id, file_path, fsize in candidates:
            if task_state.cancel_event.is_set():
                task_state.status = TaskStatus.CANCELLED
                return

            partial_hash = await loop.run_in_executor(
                thread_pool, compute_partial_hash, file_path, fsize
            )

            if partial_hash is not None:
                partial_hash_groups[partial_hash].append({
                    "id": file_id,
                    "path": file_path,
                    "size_bytes": fsize,
                })

            files_processed += 1
            if files_processed % 1000 == 0:
                await reporter.emit_progress(
                    task_id=scan_id,
                    files_scanned=files_processed,
                    dirs_scanned=0,
                    current_directory=f"Stage 2: Partial hash ({files_processed}/{total_candidates})",
                    total_bytes_scanned=0,
                    files_per_second=0,
                )

    # Filter to groups with 2+ matching partial hashes
    confirmed_candidates = {
        ph: members for ph, members in partial_hash_groups.items()
        if len(members) > 1
    }

    stage2_candidates = sum(len(m) for m in confirmed_candidates.values())
    logger.info(
        "duplicate_stage2_complete",
        scan_id=scan_id,
        partial_groups=len(confirmed_candidates),
        remaining_candidates=stage2_candidates,
    )

    # ── Stage 3: Full SHA256 hash ───────────────────────────────────────────
    logger.info("duplicate_stage3_start", scan_id=scan_id, candidates=stage2_candidates)

    full_hash_groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    files_hashed = 0

    for partial_hash, members in confirmed_candidates.items():
        if task_state.cancel_event.is_set():
            task_state.status = TaskStatus.CANCELLED
            return

        for member in members:
            if task_state.cancel_event.is_set():
                task_state.status = TaskStatus.CANCELLED
                return

            full_hash = await loop.run_in_executor(
                thread_pool, compute_full_hash, member["path"]
            )

            if full_hash is not None:
                full_hash_groups[full_hash].append(member)

                # Persist hash to files table
                async with session_factory() as session:
                    await session.execute(
                        text("UPDATE files SET sha256_hash = :hash WHERE id = :id"),
                        {"hash": full_hash, "id": member["id"]},
                    )
                    await session.commit()

            files_hashed += 1
            if files_hashed % 500 == 0:
                await reporter.emit_progress(
                    task_id=scan_id,
                    files_scanned=files_hashed,
                    dirs_scanned=0,
                    current_directory=f"Stage 3: Full hash ({files_hashed}/{stage2_candidates})",
                    total_bytes_scanned=0,
                    files_per_second=0,
                )

    # Filter to actual duplicates (2+ files with same full hash)
    true_duplicates = {
        h: members for h, members in full_hash_groups.items()
        if len(members) > 1
    }

    logger.info(
        "duplicate_stage3_complete",
        scan_id=scan_id,
        duplicate_groups=len(true_duplicates),
        total_duplicate_files=sum(len(m) for m in true_duplicates.values()),
    )

    # ── Stage 4: Create duplicate groups in database ────────────────────────
    total_wasted = 0
    groups_created = 0

    # Clear existing groups for this scan (re-detection)
    async with session_factory() as session:
        await session.execute(
            text(
                """
                DELETE FROM duplicate_members WHERE group_id IN (
                    SELECT id FROM duplicate_groups WHERE scan_id = :scan_id
                )
                """
            ),
            {"scan_id": scan_id},
        )
        await session.execute(
            text("DELETE FROM duplicate_groups WHERE scan_id = :scan_id"),
            {"scan_id": scan_id},
        )
        await session.commit()

    # Insert new groups
    async with session_factory() as session:
        for full_hash, members in true_duplicates.items():
            file_size = members[0]["size_bytes"]
            wasted = file_size * (len(members) - 1)
            total_wasted += wasted

            group_id = generate_uuid()
            await session.execute(
                text(
                    """
                    INSERT INTO duplicate_groups (id, scan_id, sha256_hash, file_size_bytes,
                        member_count, wasted_bytes, status, created_at)
                    VALUES (:id, :scan_id, :hash, :size, :count, :wasted, 'unresolved', :now)
                    """
                ),
                {
                    "id": group_id,
                    "scan_id": scan_id,
                    "hash": full_hash,
                    "size": file_size,
                    "count": len(members),
                    "wasted": wasted,
                    "now": now,
                },
            )

            for member in members:
                await session.execute(
                    text(
                        """
                        INSERT INTO duplicate_members (id, group_id, file_id, path, is_keeper, created_at)
                        VALUES (:id, :group_id, :file_id, :path, 0, :now)
                        """
                    ),
                    {
                        "id": generate_uuid(),
                        "group_id": group_id,
                        "file_id": member["id"],
                        "path": member["path"],
                        "now": now,
                    },
                )

            groups_created += 1

        # Update is_duplicate flags on files
        await session.execute(
            text(
                """
                UPDATE files SET is_duplicate = 1
                WHERE id IN (SELECT file_id FROM duplicate_members)
                AND scan_id = :scan_id
                """
            ),
            {"scan_id": scan_id},
        )

        await session.commit()

    logger.info(
        "duplicate_detection_complete",
        scan_id=scan_id,
        groups_created=groups_created,
        total_duplicate_files=sum(len(m) for m in true_duplicates.values()),
        total_wasted_bytes=total_wasted,
    )

    task_state.status = TaskStatus.COMPLETED
    await reporter.emit_completed(
        task_id=scan_id,
        scan_id=scan_id,
        total_files=sum(len(m) for m in true_duplicates.values()),
        total_dirs=groups_created,
        total_bytes=total_wasted,
        duration_seconds=0,
        files_per_second=0,
    )

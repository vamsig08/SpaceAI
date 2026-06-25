"""Scanner service — orchestrates scan creation, listing, and background execution.

Bridges the API layer to the task manager and scanner engine. Handles:
- Creating scan records
- Launching background scan tasks
- Querying scan status and history
- SSE progress streaming
"""

from __future__ import annotations

import json
import sys
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.exceptions import ConflictError, NotFoundError, ValidationError
from app.core.logging import get_logger
from app.models.base import generate_uuid, utc_now
from app.scanner.crawler import run_scan
from app.scanner.exclusions import ExclusionEngine
from app.workers.progress import ProgressReporter
from app.workers.task_manager import TaskManager, TaskType

logger = get_logger(__name__)


class ScannerService:
    """Manages scan lifecycle: creation, execution, and querying."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create_scan(
        self,
        root_path: str,
        scan_type: str,
        exclusions: list[str],
        max_depth: int | None,
        *,
        task_manager: TaskManager,
        session_factory: async_sessionmaker[AsyncSession],
        reporter: ProgressReporter,
        batch_size: int = 1000,
        checkpoint_interval: int = 10000,
        thread_pool_size: int = 4,
    ) -> dict[str, Any]:
        """Create a new scan record and launch the background scan task.

        Args:
            root_path: Absolute path to scan.
            scan_type: 'full' or 'incremental'.
            exclusions: Additional exclusion patterns.
            max_depth: Max directory depth (None = unlimited).
            task_manager: TaskManager for background execution.
            session_factory: Session factory for the scan worker.
            reporter: Progress reporter for SSE.
            batch_size: DB batch insert size.
            checkpoint_interval: Files between checkpoints.
            thread_pool_size: Thread pool workers.

        Returns:
            Created scan record dict.

        Raises:
            ValidationError: If root_path is invalid.
            ConflictError: If a scan is already running.
        """
        from pathlib import Path

        # Validate path
        path = Path(root_path)
        if not path.is_absolute():
            raise ValidationError("root_path must be an absolute path", field="root_path")
        if not path.exists():
            raise ValidationError(f"Path does not exist: {root_path}", field="root_path")
        if not path.is_dir():
            raise ValidationError(f"Path is not a directory: {root_path}", field="root_path")

        # Check for running scan
        if task_manager.has_running_task(TaskType.SCAN):
            raise ConflictError("A scan is already running. Cancel it or wait for completion.")

        # Determine platform
        platform = "macos" if sys.platform == "darwin" else "linux" if sys.platform == "linux" else "windows"

        # Create scan record
        scan_id = generate_uuid()
        now = utc_now()

        await self._session.execute(
            text(
                """
                INSERT INTO scans (id, root_path, status, scan_type, exclusion_patterns,
                                   platform, created_at)
                VALUES (:id, :root, 'pending', :type, :exclusions, :platform, :now)
                """
            ),
            {
                "id": scan_id,
                "root": root_path,
                "type": scan_type,
                "exclusions": json.dumps(exclusions) if exclusions else None,
                "platform": platform,
                "now": now,
            },
        )
        await self._session.flush()

        # Load exclusion rules from DB
        result = await self._session.execute(
            text("SELECT pattern, rule_type FROM exclusion_rules WHERE is_active = 1")
        )
        db_rules = [(row[0], row[1]) for row in result.all()]

        exclusion_engine = ExclusionEngine.create(
            db_rules=db_rules,
            additional_patterns=exclusions,
            include_platform_defaults=True,
        )

        # Submit background task
        await task_manager.submit(
            TaskType.SCAN,
            run_scan,
            root_path=root_path,
            scan_id=scan_id,
            session_factory=session_factory,
            thread_pool=task_manager.thread_pool,
            exclusion_engine=exclusion_engine,
            reporter=reporter,
            batch_size=batch_size,
            checkpoint_interval=checkpoint_interval,
            max_depth=max_depth,
        )

        # Submit post-scan analysis pipeline (waits for scan semaphore to release)
        from app.services.post_scan_pipeline import run_post_scan_pipeline
        await task_manager.submit(
            TaskType.ANALYTICS,
            run_post_scan_pipeline,
            scan_id=scan_id,
            session_factory=session_factory,
            thread_pool=task_manager.thread_pool,
            reporter=reporter,
        )

        logger.info("scan_created", scan_id=scan_id, root_path=root_path, scan_type=scan_type)

        return {
            "id": scan_id,
            "root_path": root_path,
            "status": "pending",
            "scan_type": scan_type,
            "platform": platform,
            "created_at": now,
        }

    async def get_scan(self, scan_id: str) -> dict[str, Any]:
        """Get a single scan by ID.

        Args:
            scan_id: Scan UUID.

        Returns:
            Scan record dict.

        Raises:
            NotFoundError: If scan doesn't exist.
        """
        result = await self._session.execute(
            text(
                """
                SELECT id, root_path, status, scan_type, started_at, completed_at,
                       total_files, total_dirs, total_size_bytes, files_per_second,
                       error_message, platform, created_at
                FROM scans WHERE id = :id
                """
            ),
            {"id": scan_id},
        )
        row = result.one_or_none()
        if row is None:
            raise NotFoundError("scan", scan_id)

        return {
            "id": row[0],
            "root_path": row[1],
            "status": row[2],
            "scan_type": row[3],
            "started_at": row[4],
            "completed_at": row[5],
            "total_files": row[6],
            "total_dirs": row[7],
            "total_size_bytes": row[8],
            "files_per_second": row[9],
            "error_message": row[10],
            "platform": row[11],
            "created_at": row[12],
        }

    async def list_scans(
        self, page: int = 1, page_size: int = 20
    ) -> dict[str, Any]:
        """List all scans ordered by creation date (newest first).

        Args:
            page: Page number (1-indexed).
            page_size: Results per page.

        Returns:
            Dict with scans list and pagination meta.
        """
        offset = (page - 1) * page_size

        count_result = await self._session.execute(
            text("SELECT COUNT(*) FROM scans")
        )
        total = count_result.scalar_one()

        result = await self._session.execute(
            text(
                """
                SELECT id, root_path, status, scan_type, started_at, completed_at,
                       total_files, total_dirs, total_size_bytes, files_per_second,
                       error_message, platform, created_at
                FROM scans ORDER BY created_at DESC
                LIMIT :limit OFFSET :offset
                """
            ),
            {"limit": page_size, "offset": offset},
        )

        scans = [
            {
                "id": row[0],
                "root_path": row[1],
                "status": row[2],
                "scan_type": row[3],
                "started_at": row[4],
                "completed_at": row[5],
                "total_files": row[6],
                "total_dirs": row[7],
                "total_size_bytes": row[8],
                "files_per_second": row[9],
                "error_message": row[10],
                "platform": row[11],
                "created_at": row[12],
            }
            for row in result.all()
        ]

        return {
            "scans": scans,
            "meta": {
                "page": page,
                "page_size": page_size,
                "total_items": total,
                "total_pages": (total + page_size - 1) // page_size,
            },
        }

    async def cancel_scan(
        self, scan_id: str, task_manager: TaskManager
    ) -> dict[str, Any]:
        """Cancel a running scan.

        Args:
            scan_id: Scan to cancel.
            task_manager: TaskManager to signal cancellation.

        Returns:
            Updated scan status.

        Raises:
            NotFoundError: If scan doesn't exist.
            ConflictError: If scan isn't running.
        """
        scan = await self.get_scan(scan_id)
        if scan["status"] not in ("pending", "running"):
            raise ConflictError(f"Cannot cancel scan in state '{scan['status']}'")

        # Signal task cancellation
        await task_manager.cancel(scan_id)

        # Update DB status
        await self._session.execute(
            text("UPDATE scans SET status = 'cancelled', completed_at = :now WHERE id = :id"),
            {"now": utc_now(), "id": scan_id},
        )
        await self._session.flush()

        return {"id": scan_id, "status": "cancelled"}

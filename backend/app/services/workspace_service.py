"""Developer workspace analysis service — Phase 5.

Orchestrates workspace detection, intelligence analysis, and recommendation
generation. Integrates with the task framework for background execution
and writes results to the dev_workspaces table.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.exceptions import NotFoundError
from app.core.logging import get_logger
from app.models.base import generate_uuid, utc_now
from app.scanner.workspace_detector import (
    WorkspaceResult,
    detect_abandoned_projects,
    detect_duplicate_projects,
    detect_workspaces_from_files,
)
from app.workers.progress import ProgressReporter
from app.workers.task_manager import TaskState, TaskStatus

logger = get_logger(__name__)


class WorkspaceService:
    """Provides developer workspace analysis queries."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_workspace_summary(self, scan_id: str) -> dict[str, Any]:
        """Get summary of detected developer workspaces.

        Args:
            scan_id: Scan to summarize.

        Returns:
            Summary with counts, recoverable space, and per-type breakdown.
        """
        stmt = text(
            """
            SELECT workspace_type,
                   COUNT(*) as count,
                   COALESCE(SUM(total_size_bytes), 0) as total_bytes,
                   COALESCE(SUM(recoverable_bytes), 0) as recoverable,
                   COALESCE(SUM(safe_recoverable_bytes), 0) as safe_recoverable
            FROM dev_workspaces
            WHERE scan_id = :scan_id
            GROUP BY workspace_type
            ORDER BY recoverable DESC
            """
        )
        result = await self._session.execute(stmt, {"scan_id": scan_id})
        rows = result.all()

        by_type = {
            row[0]: {
                "count": row[1],
                "total_bytes": row[2],
                "recoverable_bytes": row[3],
                "safe_recoverable_bytes": row[4],
            }
            for row in rows
        }

        total_recoverable = sum(r[3] for r in rows)
        total_safe = sum(r[4] for r in rows)
        total_workspaces = sum(r[1] for r in rows)

        # Count inactive
        inactive_result = await self._session.execute(
            text("SELECT COUNT(*) FROM dev_workspaces WHERE scan_id = :sid AND is_active = 0"),
            {"sid": scan_id},
        )
        inactive_count = inactive_result.scalar_one()

        return {
            "scan_id": scan_id,
            "total_workspaces": total_workspaces,
            "total_recoverable_bytes": total_recoverable,
            "safe_recoverable_bytes": total_safe,
            "inactive_workspaces": inactive_count,
            "by_type": by_type,
        }

    async def list_workspaces(
        self,
        scan_id: str,
        *,
        workspace_type: str | None = None,
        is_active: bool | None = None,
        min_size: int | None = None,
        page: int = 1,
        page_size: int = 50,
    ) -> dict[str, Any]:
        """List detected workspaces with filtering and pagination.

        Args:
            scan_id: Scan to query.
            workspace_type: Filter by type.
            is_active: Filter active/inactive.
            min_size: Minimum total size filter.
            page: Page number.
            page_size: Results per page.

        Returns:
            Dict with workspaces and pagination metadata.
        """
        conditions = ["scan_id = :scan_id"]
        params: dict[str, Any] = {"scan_id": scan_id}

        if workspace_type:
            conditions.append("workspace_type = :ws_type")
            params["ws_type"] = workspace_type
        if is_active is not None:
            conditions.append("is_active = :active")
            params["active"] = 1 if is_active else 0
        if min_size:
            conditions.append("total_size_bytes >= :min_size")
            params["min_size"] = min_size

        where = " AND ".join(conditions)
        offset = (page - 1) * page_size

        # Count
        count_result = await self._session.execute(
            text(f"SELECT COUNT(*) FROM dev_workspaces WHERE {where}"), params
        )
        total = count_result.scalar_one()

        # Fetch
        params["limit"] = page_size
        params["offset"] = offset
        result = await self._session.execute(
            text(
                f"""
                SELECT id, path, name, workspace_type, total_size_bytes,
                       recoverable_bytes, safe_recoverable_bytes, last_modified_at,
                       is_active, days_inactive, risk_level, artifacts
                FROM dev_workspaces
                WHERE {where}
                ORDER BY recoverable_bytes DESC
                LIMIT :limit OFFSET :offset
                """
            ),
            params,
        )

        import json
        workspaces = [
            {
                "id": row[0],
                "path": row[1],
                "name": row[2],
                "workspace_type": row[3],
                "total_size_bytes": row[4],
                "recoverable_bytes": row[5],
                "safe_recoverable_bytes": row[6],
                "last_modified_at": row[7],
                "is_active": bool(row[8]),
                "days_inactive": row[9],
                "risk_level": row[10],
                "artifacts": json.loads(row[11]) if row[11] else [],
            }
            for row in result.all()
        ]

        return {
            "workspaces": workspaces,
            "meta": {
                "page": page,
                "page_size": page_size,
                "total_items": total,
                "total_pages": (total + page_size - 1) // page_size,
            },
        }

    async def get_abandoned_projects(self, scan_id: str) -> dict[str, Any]:
        """Get workspaces classified as abandoned (inactive > 180 days).

        Args:
            scan_id: Scan to query.

        Returns:
            Dict with abandoned project list and summary.
        """
        result = await self._session.execute(
            text(
                """
                SELECT path, name, workspace_type, total_size_bytes, recoverable_bytes,
                       days_inactive, last_modified_at
                FROM dev_workspaces
                WHERE scan_id = :scan_id AND is_active = 0
                ORDER BY recoverable_bytes DESC
                """
            ),
            {"scan_id": scan_id},
        )
        rows = result.all()

        projects = [
            {
                "path": row[0],
                "name": row[1],
                "workspace_type": row[2],
                "total_size_bytes": row[3],
                "recoverable_bytes": row[4],
                "days_inactive": row[5],
                "last_modified_at": row[6],
            }
            for row in rows
        ]

        total_recoverable = sum(p["recoverable_bytes"] for p in projects)

        return {
            "scan_id": scan_id,
            "abandoned_count": len(projects),
            "total_recoverable_bytes": total_recoverable,
            "projects": projects,
        }


# ─── Background Task ──────────────────────────────────────────────────────────


async def run_workspace_analysis(
    task_state: TaskState,
    scan_id: str,
    session_factory: async_sessionmaker[AsyncSession],
    reporter: ProgressReporter,
) -> None:
    """Run workspace detection and analysis as a background task.

    Processes files in batches, detects workspaces, identifies abandoned
    projects, and persists results to dev_workspaces table.

    Args:
        task_state: Task state for progress/cancellation.
        scan_id: Scan to analyze.
        session_factory: DB session factory.
        reporter: Progress reporter for SSE.
    """
    logger.info("workspace_analysis_start", scan_id=scan_id)
    batch_size = 10000
    offset = 0
    all_records: list[tuple[str, str, int, str | None]] = []

    # Load file records in batches (id, path, size_bytes, modified_at)
    async with session_factory() as session:
        total_result = await session.execute(
            text("SELECT COUNT(*) FROM files WHERE scan_id = :sid"),
            {"sid": scan_id},
        )
        total_files = total_result.scalar_one()

    while True:
        if task_state.cancel_event.is_set():
            task_state.status = TaskStatus.CANCELLED
            return

        async with session_factory() as session:
            result = await session.execute(
                text(
                    """
                    SELECT id, path, size_bytes, modified_at
                    FROM files WHERE scan_id = :sid
                    ORDER BY id LIMIT :limit OFFSET :offset
                    """
                ),
                {"sid": scan_id, "limit": batch_size, "offset": offset},
            )
            rows = result.all()

        if not rows:
            break

        all_records.extend(rows)
        offset += batch_size

        await reporter.emit_progress(
            task_id=scan_id,
            files_scanned=len(all_records),
            dirs_scanned=0,
            current_directory=f"Loading files ({len(all_records)}/{total_files})",
            total_bytes_scanned=0,
            files_per_second=0,
        )

    if task_state.cancel_event.is_set():
        task_state.status = TaskStatus.CANCELLED
        return

    # Detect workspaces
    workspaces = detect_workspaces_from_files(all_records)

    # Classify abandoned projects
    detect_abandoned_projects(workspaces, inactive_threshold_days=180)

    # Detect duplicate projects
    dup_groups = detect_duplicate_projects(workspaces)

    # Clear existing results for this scan
    async with session_factory() as session:
        await session.execute(
            text("DELETE FROM dev_workspaces WHERE scan_id = :sid"),
            {"sid": scan_id},
        )
        await session.commit()

    # Persist results
    import json
    now = utc_now()

    async with session_factory() as session:
        for ws in workspaces:
            await session.execute(
                text(
                    """
                    INSERT INTO dev_workspaces (
                        id, scan_id, path, name, workspace_type, total_size_bytes,
                        recoverable_bytes, safe_recoverable_bytes, last_modified_at,
                        is_active, days_inactive, risk_level, artifacts, created_at
                    ) VALUES (
                        :id, :scan_id, :path, :name, :ws_type, :size,
                        :recoverable, :safe, :modified, :active, :days, :risk, :artifacts, :now
                    )
                    """
                ),
                {
                    "id": generate_uuid(),
                    "scan_id": scan_id,
                    "path": ws.path,
                    "name": ws.name,
                    "ws_type": ws.workspace_type,
                    "size": ws.total_size_bytes,
                    "recoverable": ws.recoverable_bytes,
                    "safe": ws.safe_recoverable_bytes,
                    "modified": ws.last_modified_at,
                    "active": 1 if ws.is_active else 0,
                    "days": ws.days_inactive,
                    "risk": ws.risk_level,
                    "artifacts": json.dumps(ws.artifacts),
                    "now": now,
                },
            )
        await session.commit()

    task_state.status = TaskStatus.COMPLETED
    logger.info(
        "workspace_analysis_complete",
        scan_id=scan_id,
        workspaces_detected=len(workspaces),
        duplicate_groups=len(dup_groups),
        total_recoverable=sum(w.recoverable_bytes for w in workspaces),
    )

    await reporter.emit_completed(
        task_id=scan_id,
        scan_id=scan_id,
        total_files=total_files,
        total_dirs=len(workspaces),
        total_bytes=sum(w.recoverable_bytes for w in workspaces),
        duration_seconds=0,
        files_per_second=0,
    )

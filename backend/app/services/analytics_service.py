"""Storage analytics service — business logic for Phase 2.

Provides:
- Storage snapshot generation (post-scan)
- Dashboard overview (reads from pre-computed snapshots)
- Category/extension breakdowns
- Largest files and folders
- Historical growth tracking
"""

from __future__ import annotations

import json
import shutil
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import NotFoundError
from app.core.logging import get_logger
from app.models.base import generate_uuid, utc_now
from app.models.storage_snapshot import StorageSnapshot
from app.repositories.file_repository import FileRepository
from app.repositories.folder_repository import FolderRepository
from app.repositories.scan_repository import ScanRepository
from app.repositories.snapshot_repository import SnapshotRepository

logger = get_logger(__name__)


class AnalyticsService:
    """Orchestrates storage analytics operations.

    All business logic for computing, storing, and retrieving analytics
    lives here. API routes delegate to this service without performing
    any data processing themselves.
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._file_repo = FileRepository(session)
        self._folder_repo = FolderRepository(session)
        self._scan_repo = ScanRepository(session)
        self._snapshot_repo = SnapshotRepository(session)

    async def generate_snapshot(self, scan_id: str) -> StorageSnapshot:
        """Generate a pre-computed analytics snapshot for a completed scan.

        This is called after a scan completes to materialize aggregated
        metrics. Subsequent dashboard queries read from this snapshot
        instead of aggregating the raw files table.

        Args:
            scan_id: ID of the completed scan.

        Returns:
            The created StorageSnapshot record.

        Raises:
            NotFoundError: If scan doesn't exist or isn't completed.
        """
        scan = await self._scan_repo.get_by_id(scan_id)
        if scan is None:
            raise NotFoundError("scan", scan_id)
        if scan.status not in ("completed", "completed_with_warnings"):
            raise NotFoundError("scan", scan_id)

        # Compute aggregates from raw data
        file_stats = await self._file_repo.get_total_stats(scan_id)
        category_breakdown = await self._file_repo.get_category_breakdown(scan_id)
        extension_breakdown = await self._file_repo.get_extension_breakdown(scan_id, limit=20)
        largest_files = await self._file_repo.find_largest(scan_id, limit=20)
        largest_dirs = await self._folder_repo.find_largest(scan_id, limit=20)
        dir_count = await self._folder_repo.get_total_count(scan_id)

        # Get disk usage info for total/free space
        total_storage, free_storage = _get_disk_usage(scan.root_path)
        used_storage = total_storage - free_storage if total_storage > 0 else file_stats["total_bytes"]

        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

        # Check if snapshot for today already exists (upsert)
        existing = await self._snapshot_repo.get_by_date(today)
        if existing:
            # Update existing snapshot
            existing.scan_id = scan_id
            existing.total_size_bytes = total_storage
            existing.used_size_bytes = used_storage
            existing.file_count = file_stats["file_count"]
            existing.dir_count = dir_count
            existing.category_breakdown = json.dumps(category_breakdown)
            existing.extension_breakdown = json.dumps(extension_breakdown)
            existing.largest_files = json.dumps(largest_files)
            existing.largest_dirs = json.dumps(largest_dirs)
            await self._session.flush()
            logger.info(
                "snapshot_updated",
                scan_id=scan_id,
                date=today,
                file_count=file_stats["file_count"],
            )
            return existing

        # Create new snapshot
        snapshot = StorageSnapshot(
            id=generate_uuid(),
            scan_id=scan_id,
            snapshot_date=today,
            total_size_bytes=total_storage,
            used_size_bytes=used_storage,
            file_count=file_stats["file_count"],
            dir_count=dir_count,
            category_breakdown=json.dumps(category_breakdown),
            extension_breakdown=json.dumps(extension_breakdown),
            largest_files=json.dumps(largest_files),
            largest_dirs=json.dumps(largest_dirs),
            created_at=utc_now(),
        )
        await self._snapshot_repo.create(snapshot)

        logger.info(
            "snapshot_generated",
            scan_id=scan_id,
            date=today,
            file_count=file_stats["file_count"],
            total_bytes=file_stats["total_bytes"],
        )
        return snapshot

    async def get_overview(self) -> dict[str, Any]:
        """Get the storage overview dashboard data.

        Reads from the latest pre-computed snapshot for speed (<50ms).
        Enriches with live data from analysis tables (duplicates, stale, workspaces).

        Returns:
            Dict with total_storage, used_storage, free_storage,
            file_count, dir_count, recovery_opportunities, last_scan.
        """
        snapshot = await self._snapshot_repo.get_latest()
        scan = await self._scan_repo.get_latest_completed()

        if snapshot is None:
            # No snapshots yet — return zeros or live data
            if scan is None:
                return _empty_overview()
            # Generate snapshot on-the-fly
            snapshot = await self.generate_snapshot(scan.id)

        free_storage = snapshot.total_size_bytes - snapshot.used_size_bytes
        if free_storage < 0:
            free_storage = 0

        scan_id = snapshot.scan_id

        # Query live analysis data for this scan
        duplicate_waste = await self._get_duplicate_waste(scan_id)
        stale_size = await self._get_stale_size(scan_id)
        workspace_recoverable = await self._get_workspace_recoverable(scan_id)

        recovery_opportunities = duplicate_waste + stale_size + workspace_recoverable

        return {
            "total_storage": snapshot.total_size_bytes,
            "used_storage": snapshot.used_size_bytes,
            "free_storage": free_storage,
            "file_count": snapshot.file_count,
            "dir_count": snapshot.dir_count,
            "duplicate_waste": duplicate_waste,
            "stale_files_size": stale_size,
            "recovery_opportunities": recovery_opportunities,
            "last_scan": scan.completed_at if scan else None,
            "snapshot_date": snapshot.snapshot_date,
        }

    async def _get_duplicate_waste(self, scan_id: str) -> int:
        """Get total wasted bytes from duplicate groups for a scan."""
        from sqlalchemy import text
        result = await self._session.execute(
            text("SELECT COALESCE(SUM(wasted_bytes), 0) FROM duplicate_groups WHERE scan_id = :sid"),
            {"sid": scan_id},
        )
        return result.scalar_one()

    async def _get_stale_size(self, scan_id: str) -> int:
        """Get total bytes in stale files (stale_score >= 0.5) for a scan."""
        from sqlalchemy import text
        result = await self._session.execute(
            text("SELECT COALESCE(SUM(size_bytes), 0) FROM files WHERE scan_id = :sid AND is_stale = 1"),
            {"sid": scan_id},
        )
        return result.scalar_one()

    async def _get_workspace_recoverable(self, scan_id: str) -> int:
        """Get total recoverable bytes from developer workspaces for a scan."""
        from sqlalchemy import text
        result = await self._session.execute(
            text("SELECT COALESCE(SUM(recoverable_bytes), 0) FROM dev_workspaces WHERE scan_id = :sid"),
            {"sid": scan_id},
        )
        return result.scalar_one()

    async def get_categories(self, scan_id: str | None = None) -> dict[str, Any]:
        """Get file category breakdown.

        Args:
            scan_id: Specific scan to analyze, or None for latest.

        Returns:
            Dict with category breakdown and totals.
        """
        if scan_id is None:
            snapshot = await self._snapshot_repo.get_latest()
            if snapshot:
                return {
                    "breakdown": json.loads(snapshot.category_breakdown),
                    "total_bytes": snapshot.used_size_bytes,
                    "file_count": snapshot.file_count,
                    "scan_id": snapshot.scan_id,
                    "snapshot_date": snapshot.snapshot_date,
                }
            scan = await self._scan_repo.get_latest_completed()
            if scan is None:
                return {"breakdown": {}, "total_bytes": 0, "file_count": 0, "scan_id": None, "snapshot_date": None}
            scan_id = scan.id

        breakdown = await self._file_repo.get_category_breakdown(scan_id)
        stats = await self._file_repo.get_total_stats(scan_id)
        return {
            "breakdown": breakdown,
            "total_bytes": stats["total_bytes"],
            "file_count": stats["file_count"],
            "scan_id": scan_id,
            "snapshot_date": None,
        }

    async def get_extensions(self, scan_id: str | None = None, limit: int = 20) -> dict[str, Any]:
        """Get top file extensions by total size.

        Args:
            scan_id: Specific scan, or None for latest.
            limit: Number of extensions to return.

        Returns:
            Dict with extensions list and metadata.
        """
        if scan_id is None:
            snapshot = await self._snapshot_repo.get_latest()
            if snapshot and snapshot.extension_breakdown:
                return {
                    "extensions": json.loads(snapshot.extension_breakdown),
                    "scan_id": snapshot.scan_id,
                }
            scan = await self._scan_repo.get_latest_completed()
            if scan is None:
                return {"extensions": [], "scan_id": None}
            scan_id = scan.id

        extensions = await self._file_repo.get_extension_breakdown(scan_id, limit=limit)
        return {"extensions": extensions, "scan_id": scan_id}

    async def get_largest_files(
        self, scan_id: str | None = None, limit: int = 100
    ) -> dict[str, Any]:
        """Get the largest files across a scan.

        Args:
            scan_id: Specific scan, or None for latest.
            limit: Max files to return.

        Returns:
            Dict with files list and metadata.
        """
        if scan_id is None:
            scan = await self._scan_repo.get_latest_completed()
            if scan is None:
                return {"files": [], "scan_id": None, "total_count": 0}
            scan_id = scan.id

        files = await self._file_repo.find_largest(scan_id, limit=limit)
        total_count = (await self._file_repo.get_total_stats(scan_id))["file_count"]
        return {"files": files, "scan_id": scan_id, "total_count": total_count}

    async def get_largest_folders(
        self, scan_id: str | None = None, limit: int = 50
    ) -> dict[str, Any]:
        """Get the largest folders across a scan.

        Args:
            scan_id: Specific scan, or None for latest.
            limit: Max folders to return.

        Returns:
            Dict with folders list and metadata.
        """
        if scan_id is None:
            scan = await self._scan_repo.get_latest_completed()
            if scan is None:
                return {"folders": [], "scan_id": None, "total_count": 0}
            scan_id = scan.id

        folders = await self._folder_repo.find_largest(scan_id, limit=limit)
        total_count = await self._folder_repo.get_total_count(scan_id)
        return {"folders": folders, "scan_id": scan_id, "total_count": total_count}

    async def get_growth_history(
        self,
        days: int = 30,
        from_date: str | None = None,
        to_date: str | None = None,
    ) -> dict[str, Any]:
        """Get storage growth history for trend charting.

        Args:
            days: Number of days of history.
            from_date: Start date filter.
            to_date: End date filter.

        Returns:
            Dict with data points and growth rate.
        """
        if from_date or to_date:
            snapshots = await self._snapshot_repo.get_history(
                limit=days, from_date=from_date, to_date=to_date
            )
            data_points = [
                {
                    "date": s.snapshot_date,
                    "total_size_bytes": s.total_size_bytes,
                    "used_size_bytes": s.used_size_bytes,
                    "file_count": s.file_count,
                }
                for s in reversed(snapshots)  # chronological order
            ]
        else:
            data_points_raw = await self._snapshot_repo.get_growth_data(days=days)
            data_points = [
                {
                    "date": dp["date"],
                    "total_size_bytes": dp["total_size_bytes"],
                    "used_size_bytes": 0,  # Not in growth_data query
                    "file_count": dp["file_count"],
                }
                for dp in data_points_raw
            ]

        # Compute growth rate if we have at least 2 data points
        growth_rate = _compute_growth_rate(data_points)

        return {
            "data_points": data_points,
            "period_days": days,
            "data_point_count": len(data_points),
            "daily_growth_bytes": growth_rate,
        }


def _get_disk_usage(path: str) -> tuple[int, int]:
    """Get total and free disk space for the filesystem containing path.

    Uses shutil.disk_usage which works cross-platform.

    Args:
        path: Any path on the target filesystem.

    Returns:
        Tuple of (total_bytes, free_bytes). Returns (0, 0) on error.
    """
    try:
        usage = shutil.disk_usage(path)
        return usage.total, usage.free
    except (OSError, FileNotFoundError):
        return 0, 0


def _compute_growth_rate(data_points: list[dict[str, Any]]) -> float:
    """Compute average daily growth rate from time series data.

    Uses simple linear difference between first and last data points.

    Args:
        data_points: Chronologically ordered list with total_size_bytes.

    Returns:
        Average bytes per day growth, or 0.0 if insufficient data.
    """
    if len(data_points) < 2:
        return 0.0

    first = data_points[0]
    last = data_points[-1]

    try:
        date_first = datetime.strptime(first["date"], "%Y-%m-%d")
        date_last = datetime.strptime(last["date"], "%Y-%m-%d")
        days_diff = (date_last - date_first).days
        if days_diff <= 0:
            return 0.0

        size_diff = last["total_size_bytes"] - first["total_size_bytes"]
        return size_diff / days_diff
    except (ValueError, KeyError):
        return 0.0


def _empty_overview() -> dict[str, Any]:
    """Return an empty overview response when no data exists."""
    return {
        "total_storage": 0,
        "used_storage": 0,
        "free_storage": 0,
        "file_count": 0,
        "dir_count": 0,
        "duplicate_waste": 0,
        "stale_files_size": 0,
        "recovery_opportunities": 0,
        "last_scan": None,
        "snapshot_date": None,
    }

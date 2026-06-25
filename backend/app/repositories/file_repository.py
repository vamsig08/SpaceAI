"""Repository for file record queries — optimized for analytics."""

from typing import Any

from sqlalchemy import Integer, func, select, text, cast
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.file import File
from app.repositories.base import BaseRepository


class FileRepository(BaseRepository[File]):
    """Data access layer for File records with analytics-optimized queries."""

    def __init__(self, session: AsyncSession) -> None:
        super().__init__(File, session)

    async def find_largest(
        self, scan_id: str, limit: int = 100
    ) -> list[dict[str, Any]]:
        """Find the largest files in a scan, ordered by size descending.

        Returns lightweight dicts instead of full ORM objects to reduce memory.

        Args:
            scan_id: Scan to query within.
            limit: Maximum results.

        Returns:
            List of dicts with id, path, filename, size_bytes, category, modified_at.
        """
        stmt = (
            select(
                File.id,
                File.path,
                File.filename,
                File.size_bytes,
                File.category,
                File.modified_at,
                File.extension,
            )
            .where(File.scan_id == scan_id)
            .order_by(File.size_bytes.desc())
            .limit(limit)
        )
        result = await self._session.execute(stmt)
        return [
            {
                "id": row.id,
                "path": row.path,
                "filename": row.filename,
                "size_bytes": row.size_bytes,
                "category": row.category,
                "modified_at": row.modified_at,
                "extension": row.extension,
            }
            for row in result.all()
        ]

    async def get_category_breakdown(self, scan_id: str) -> dict[str, int]:
        """Compute total bytes per file category for a scan.

        Args:
            scan_id: Scan to aggregate.

        Returns:
            Dict mapping category name to total bytes.
        """
        stmt = (
            select(
                File.category,
                func.sum(File.size_bytes).label("total_bytes"),
            )
            .where(File.scan_id == scan_id)
            .group_by(File.category)
        )
        result = await self._session.execute(stmt)
        return {
            row.category or "other": int(row.total_bytes)
            for row in result.all()
        }

    async def get_extension_breakdown(
        self, scan_id: str, limit: int = 20
    ) -> list[dict[str, Any]]:
        """Compute top extensions by total size.

        Args:
            scan_id: Scan to aggregate.
            limit: Number of top extensions to return.

        Returns:
            List of dicts with extension, total_bytes, file_count.
        """
        stmt = (
            select(
                File.extension,
                func.sum(File.size_bytes).label("total_bytes"),
                func.count().label("file_count"),
            )
            .where(File.scan_id == scan_id, File.extension.isnot(None))
            .group_by(File.extension)
            .order_by(func.sum(File.size_bytes).desc())
            .limit(limit)
        )
        result = await self._session.execute(stmt)
        return [
            {
                "extension": row.extension,
                "total_bytes": int(row.total_bytes),
                "file_count": int(row.file_count),
            }
            for row in result.all()
        ]

    async def get_total_stats(self, scan_id: str) -> dict[str, int]:
        """Get aggregate stats for a scan: total files, total bytes.

        Args:
            scan_id: Scan to aggregate.

        Returns:
            Dict with file_count and total_bytes.
        """
        stmt = (
            select(
                func.count(File.id).label("file_count"),
                func.coalesce(func.sum(File.size_bytes), 0).label("total_bytes"),
            )
            .where(File.scan_id == scan_id)
        )
        result = await self._session.execute(stmt)
        row = result.one()
        return {
            "file_count": int(row.file_count),
            "total_bytes": int(row.total_bytes),
        }

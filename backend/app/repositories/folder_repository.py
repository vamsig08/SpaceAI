"""Repository for folder record queries — optimized for analytics."""

from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.folder import Folder
from app.repositories.base import BaseRepository


class FolderRepository(BaseRepository[Folder]):
    """Data access layer for Folder records with analytics queries."""

    def __init__(self, session: AsyncSession) -> None:
        super().__init__(Folder, session)

    async def find_largest(
        self, scan_id: str, limit: int = 50
    ) -> list[dict[str, Any]]:
        """Find the largest directories by total size.

        Args:
            scan_id: Scan to query within.
            limit: Maximum results.

        Returns:
            List of dicts with id, path, name, total_size_bytes, file_count, depth.
        """
        stmt = (
            select(
                Folder.id,
                Folder.path,
                Folder.name,
                Folder.total_size_bytes,
                Folder.file_count,
                Folder.depth,
            )
            .where(Folder.scan_id == scan_id)
            .order_by(Folder.total_size_bytes.desc())
            .limit(limit)
        )
        result = await self._session.execute(stmt)
        return [
            {
                "id": row.id,
                "path": row.path,
                "name": row.name,
                "total_size_bytes": row.total_size_bytes,
                "file_count": row.file_count,
                "depth": row.depth,
            }
            for row in result.all()
        ]

    async def get_total_count(self, scan_id: str) -> int:
        """Count total directories in a scan.

        Args:
            scan_id: Scan to count.

        Returns:
            Directory count.
        """
        stmt = (
            select(func.count(Folder.id))
            .where(Folder.scan_id == scan_id)
        )
        result = await self._session.execute(stmt)
        return result.scalar_one()

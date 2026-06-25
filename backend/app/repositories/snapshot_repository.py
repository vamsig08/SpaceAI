"""Repository for storage snapshot operations."""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.storage_snapshot import StorageSnapshot
from app.repositories.base import BaseRepository


class SnapshotRepository(BaseRepository[StorageSnapshot]):
    """Data access layer for StorageSnapshot records."""

    def __init__(self, session: AsyncSession) -> None:
        super().__init__(StorageSnapshot, session)

    async def get_latest(self) -> StorageSnapshot | None:
        """Get the most recent storage snapshot.

        Returns:
            Latest snapshot or None if no snapshots exist.
        """
        stmt = (
            select(StorageSnapshot)
            .order_by(StorageSnapshot.snapshot_date.desc())
            .limit(1)
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_by_date(self, date: str) -> StorageSnapshot | None:
        """Get snapshot for a specific date.

        Args:
            date: ISO date string (YYYY-MM-DD).

        Returns:
            Snapshot for that date or None.
        """
        stmt = select(StorageSnapshot).where(
            StorageSnapshot.snapshot_date == date
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_history(
        self, limit: int = 90, from_date: str | None = None, to_date: str | None = None
    ) -> list[StorageSnapshot]:
        """Get historical snapshots ordered by date descending.

        Args:
            limit: Maximum records to return.
            from_date: Start date filter (inclusive).
            to_date: End date filter (inclusive).

        Returns:
            List of snapshots ordered newest first.
        """
        stmt = select(StorageSnapshot).order_by(StorageSnapshot.snapshot_date.desc())

        if from_date:
            stmt = stmt.where(StorageSnapshot.snapshot_date >= from_date)
        if to_date:
            stmt = stmt.where(StorageSnapshot.snapshot_date <= to_date)

        stmt = stmt.limit(limit)
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def get_growth_data(self, days: int = 30) -> list[dict[str, int | str]]:
        """Get date-ordered snapshot data for growth chart rendering.

        Args:
            days: Number of most recent days to include.

        Returns:
            List of dicts with date, total_size_bytes, file_count.
        """
        stmt = (
            select(
                StorageSnapshot.snapshot_date,
                StorageSnapshot.total_size_bytes,
                StorageSnapshot.file_count,
            )
            .order_by(StorageSnapshot.snapshot_date.desc())
            .limit(days)
        )
        result = await self._session.execute(stmt)
        rows = result.all()
        # Return in chronological order (oldest first) for charting
        return [
            {
                "date": row.snapshot_date,
                "total_size_bytes": row.total_size_bytes,
                "file_count": row.file_count,
            }
            for row in reversed(rows)
        ]

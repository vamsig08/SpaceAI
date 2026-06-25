"""Repository for scan record operations."""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.scan import Scan
from app.repositories.base import BaseRepository


class ScanRepository(BaseRepository[Scan]):
    """Data access layer for Scan records."""

    def __init__(self, session: AsyncSession) -> None:
        super().__init__(Scan, session)

    async def get_latest_completed(self) -> Scan | None:
        """Get the most recently completed scan.

        Returns:
            The latest completed Scan or None.
        """
        stmt = (
            select(Scan)
            .where(Scan.status.in_(["completed", "completed_with_warnings"]))
            .order_by(Scan.completed_at.desc())
            .limit(1)
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def find_by_status(self, status: str) -> list[Scan]:
        """Find all scans with a given status.

        Args:
            status: Status to filter by.

        Returns:
            List of matching scans.
        """
        stmt = select(Scan).where(Scan.status == status).order_by(Scan.created_at.desc())
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

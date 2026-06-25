"""Repository for duplicate group and member queries."""

from __future__ import annotations

from typing import Any

from sqlalchemy import func, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.duplicate import DuplicateGroup, DuplicateMember
from app.repositories.base import BaseRepository


class DuplicateRepository(BaseRepository[DuplicateGroup]):
    """Data access layer for duplicate detection results."""

    def __init__(self, session: AsyncSession) -> None:
        super().__init__(DuplicateGroup, session)

    async def get_summary(self, scan_id: str) -> dict[str, Any]:
        """Get aggregate duplicate statistics for a scan.

        Args:
            scan_id: Scan to summarize.

        Returns:
            Dict with total_groups, total_duplicate_files, total_wasted_bytes.
        """
        stmt = (
            select(
                func.count(DuplicateGroup.id).label("total_groups"),
                func.coalesce(func.sum(DuplicateGroup.member_count), 0).label("total_files"),
                func.coalesce(func.sum(DuplicateGroup.wasted_bytes), 0).label("total_wasted"),
            )
            .where(DuplicateGroup.scan_id == scan_id)
        )
        result = await self._session.execute(stmt)
        row = result.one()
        return {
            "total_groups": int(row.total_groups),
            "total_duplicate_files": int(row.total_files),
            "total_wasted_bytes": int(row.total_wasted),
        }

    async def find_groups_by_scan(
        self,
        scan_id: str,
        *,
        offset: int = 0,
        limit: int = 50,
        min_wasted: int | None = None,
        status: str | None = None,
    ) -> list[DuplicateGroup]:
        """List duplicate groups for a scan with optional filters.

        Args:
            scan_id: Scan to query.
            offset: Pagination offset.
            limit: Max results.
            min_wasted: Minimum wasted bytes filter.
            status: Group status filter (unresolved|resolved).

        Returns:
            List of DuplicateGroup records ordered by wasted_bytes desc.
        """
        stmt = (
            select(DuplicateGroup)
            .where(DuplicateGroup.scan_id == scan_id)
            .order_by(DuplicateGroup.wasted_bytes.desc())
            .offset(offset)
            .limit(limit)
        )
        if min_wasted is not None:
            stmt = stmt.where(DuplicateGroup.wasted_bytes >= min_wasted)
        if status is not None:
            stmt = stmt.where(DuplicateGroup.status == status)

        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def count_groups(self, scan_id: str) -> int:
        """Count total duplicate groups for a scan.

        Args:
            scan_id: Scan to count.

        Returns:
            Group count.
        """
        stmt = (
            select(func.count(DuplicateGroup.id))
            .where(DuplicateGroup.scan_id == scan_id)
        )
        result = await self._session.execute(stmt)
        return result.scalar_one()

    async def get_group_with_members(self, group_id: str) -> DuplicateGroup | None:
        """Get a group with all its members loaded.

        Args:
            group_id: Group ID to fetch.

        Returns:
            DuplicateGroup with members populated, or None.
        """
        stmt = select(DuplicateGroup).where(DuplicateGroup.id == group_id)
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def mark_keeper(self, group_id: str, file_id: str) -> bool:
        """Mark a specific file as the keeper in a duplicate group.

        Clears any existing keeper flag and sets the new one.

        Args:
            group_id: The duplicate group.
            file_id: The file to mark as keeper.

        Returns:
            True if successful, False if member not found.
        """
        # Clear existing keeper
        await self._session.execute(
            update(DuplicateMember)
            .where(DuplicateMember.group_id == group_id)
            .values(is_keeper=0)
        )

        # Set new keeper
        result = await self._session.execute(
            update(DuplicateMember)
            .where(
                DuplicateMember.group_id == group_id,
                DuplicateMember.file_id == file_id,
            )
            .values(is_keeper=1)
        )
        if result.rowcount == 0:
            return False

        # Update group status
        await self._session.execute(
            update(DuplicateGroup)
            .where(DuplicateGroup.id == group_id)
            .values(status="resolved")
        )
        await self._session.flush()
        return True

    async def delete_groups_for_scan(self, scan_id: str) -> int:
        """Delete all duplicate groups and members for a scan.

        Used when re-running duplicate detection to clear stale results.

        Args:
            scan_id: Scan whose groups to delete.

        Returns:
            Number of groups deleted.
        """
        groups = await self.find_groups_by_scan(scan_id, limit=100000)
        count = len(groups)
        for group in groups:
            await self._session.delete(group)
        await self._session.flush()
        return count

    async def get_all_non_keeper_paths(self, scan_id: str) -> list[str]:
        """Get filesystem paths of all duplicate files that are NOT marked as keepers.

        For unresolved groups, keeps the first member and returns the rest.
        For resolved groups, returns all non-keeper members.

        Args:
            scan_id: Scan to query.

        Returns:
            List of absolute file paths eligible for cleanup.
        """
        # Get all members with their keeper status
        result = await self._session.execute(
            text(
                """
                SELECT dm.path, dm.is_keeper, dm.group_id,
                       ROW_NUMBER() OVER (PARTITION BY dm.group_id ORDER BY dm.id) as rn
                FROM duplicate_members dm
                JOIN duplicate_groups dg ON dm.group_id = dg.id
                WHERE dg.scan_id = :scan_id
                """
            ),
            {"scan_id": scan_id},
        )
        rows = result.all()

        # For each group: keep the designated keeper (or first if unresolved)
        paths_to_remove: list[str] = []
        for path, is_keeper, group_id, row_num in rows:
            if is_keeper:
                continue  # Explicitly marked as keeper — skip
            if row_num == 1:
                continue  # First in group — default keeper
            paths_to_remove.append(path)

        return paths_to_remove

    async def get_top_extensions(self, scan_id: str, limit: int = 10) -> list[str]:
        """Get the most common file extensions among duplicates.

        Args:
            scan_id: Scan to query.
            limit: Max extensions to return.

        Returns:
            List of extension strings.
        """
        stmt = text(
            """
            SELECT f.extension, COUNT(*) as cnt
            FROM duplicate_members dm
            JOIN duplicate_groups dg ON dm.group_id = dg.id
            JOIN files f ON dm.file_id = f.id
            WHERE dg.scan_id = :scan_id AND f.extension IS NOT NULL
            GROUP BY f.extension
            ORDER BY cnt DESC
            LIMIT :limit
            """
        )
        result = await self._session.execute(stmt, {"scan_id": scan_id, "limit": limit})
        return [row[0] for row in result.all()]

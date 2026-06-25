"""Unit tests for FolderRepository analytics queries."""

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.repositories.folder_repository import FolderRepository


class TestFolderRepositoryLargest:
    """Tests for find_largest query."""

    async def test_returns_folders_ordered_by_size_desc(
        self, session: AsyncSession, sample_scan: dict, sample_folders: list
    ) -> None:
        repo = FolderRepository(session)
        results = await repo.find_largest(sample_scan["id"], limit=10)

        sizes = [r["total_size_bytes"] for r in results]
        assert sizes == sorted(sizes, reverse=True)

    async def test_largest_folder_is_root(
        self, session: AsyncSession, sample_scan: dict, sample_folders: list
    ) -> None:
        repo = FolderRepository(session)
        results = await repo.find_largest(sample_scan["id"], limit=1)

        assert results[0]["path"] == "/tmp/test-scan"
        assert results[0]["total_size_bytes"] == 4805000

    async def test_returns_correct_fields(
        self, session: AsyncSession, sample_scan: dict, sample_folders: list
    ) -> None:
        repo = FolderRepository(session)
        results = await repo.find_largest(sample_scan["id"], limit=1)

        entry = results[0]
        assert "id" in entry
        assert "path" in entry
        assert "name" in entry
        assert "total_size_bytes" in entry
        assert "file_count" in entry
        assert "depth" in entry


class TestFolderRepositoryCount:
    """Tests for get_total_count query."""

    async def test_returns_correct_count(
        self, session: AsyncSession, sample_scan: dict, sample_folders: list
    ) -> None:
        repo = FolderRepository(session)
        count = await repo.get_total_count(sample_scan["id"])
        assert count == 6  # 6 folders in sample_folders

    async def test_nonexistent_scan_returns_zero(self, session: AsyncSession) -> None:
        repo = FolderRepository(session)
        count = await repo.get_total_count("nonexistent")
        assert count == 0

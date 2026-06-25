"""Unit tests for FileRepository analytics queries."""

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.repositories.file_repository import FileRepository


class TestFileRepositoryLargest:
    """Tests for find_largest query."""

    async def test_returns_files_ordered_by_size_desc(
        self, session: AsyncSession, sample_scan: dict, sample_files: list
    ) -> None:
        repo = FileRepository(session)
        results = await repo.find_largest(sample_scan["id"], limit=5)

        assert len(results) == 5
        sizes = [r["size_bytes"] for r in results]
        assert sizes == sorted(sizes, reverse=True)

    async def test_respects_limit(
        self, session: AsyncSession, sample_scan: dict, sample_files: list
    ) -> None:
        repo = FileRepository(session)
        results = await repo.find_largest(sample_scan["id"], limit=3)
        assert len(results) == 3

    async def test_returns_correct_fields(
        self, session: AsyncSession, sample_scan: dict, sample_files: list
    ) -> None:
        repo = FileRepository(session)
        results = await repo.find_largest(sample_scan["id"], limit=1)

        entry = results[0]
        assert "id" in entry
        assert "path" in entry
        assert "filename" in entry
        assert "size_bytes" in entry
        assert "category" in entry
        assert "extension" in entry

    async def test_empty_scan_returns_empty_list(
        self, session: AsyncSession, sample_scan: dict
    ) -> None:
        repo = FileRepository(session)
        results = await repo.find_largest("nonexistent-scan-id", limit=10)
        assert results == []


class TestFileRepositoryCategoryBreakdown:
    """Tests for get_category_breakdown query."""

    async def test_returns_all_categories_present(
        self, session: AsyncSession, sample_scan: dict, sample_files: list
    ) -> None:
        repo = FileRepository(session)
        breakdown = await repo.get_category_breakdown(sample_scan["id"])

        assert "video" in breakdown
        assert "image" in breakdown
        assert "code" in breakdown
        assert "document" in breakdown
        assert "archive" in breakdown

    async def test_video_total_is_sum_of_video_files(
        self, session: AsyncSession, sample_scan: dict, sample_files: list
    ) -> None:
        repo = FileRepository(session)
        breakdown = await repo.get_category_breakdown(sample_scan["id"])

        # Two video files: 1000000 + 2000000
        assert breakdown["video"] == 3000000

    async def test_returns_empty_dict_for_nonexistent_scan(
        self, session: AsyncSession
    ) -> None:
        repo = FileRepository(session)
        breakdown = await repo.get_category_breakdown("nonexistent")
        assert breakdown == {}


class TestFileRepositoryExtensionBreakdown:
    """Tests for get_extension_breakdown query."""

    async def test_returns_extensions_ordered_by_size(
        self, session: AsyncSession, sample_scan: dict, sample_files: list
    ) -> None:
        repo = FileRepository(session)
        extensions = await repo.get_extension_breakdown(sample_scan["id"], limit=10)

        sizes = [e["total_bytes"] for e in extensions]
        assert sizes == sorted(sizes, reverse=True)

    async def test_groups_same_extension(
        self, session: AsyncSession, sample_scan: dict, sample_files: list
    ) -> None:
        repo = FileRepository(session)
        extensions = await repo.get_extension_breakdown(sample_scan["id"], limit=20)

        # .py appears twice: 5000 + 3000
        py_entry = next((e for e in extensions if e["extension"] == ".py"), None)
        assert py_entry is not None
        assert py_entry["total_bytes"] == 8000
        assert py_entry["file_count"] == 2

    async def test_respects_limit(
        self, session: AsyncSession, sample_scan: dict, sample_files: list
    ) -> None:
        repo = FileRepository(session)
        extensions = await repo.get_extension_breakdown(sample_scan["id"], limit=3)
        assert len(extensions) == 3


class TestFileRepositoryStats:
    """Tests for get_total_stats query."""

    async def test_returns_correct_totals(
        self, session: AsyncSession, sample_scan: dict, sample_files: list
    ) -> None:
        repo = FileRepository(session)
        stats = await repo.get_total_stats(sample_scan["id"])

        assert stats["file_count"] == 11  # 11 files in sample_files
        expected_total = sum(f["size_bytes"] for f in sample_files)
        assert stats["total_bytes"] == expected_total

    async def test_empty_scan_returns_zeros(self, session: AsyncSession) -> None:
        repo = FileRepository(session)
        stats = await repo.get_total_stats("nonexistent")
        assert stats["file_count"] == 0
        assert stats["total_bytes"] == 0

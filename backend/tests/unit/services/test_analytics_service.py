"""Unit tests for AnalyticsService business logic."""

import json
from unittest.mock import patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.analytics_service import AnalyticsService, _compute_growth_rate


class TestSnapshotGeneration:
    """Tests for generate_snapshot."""

    async def test_generates_snapshot_for_completed_scan(
        self, session: AsyncSession, sample_scan: dict, sample_files: list, sample_folders: list
    ) -> None:
        service = AnalyticsService(session)

        with patch("app.services.analytics_service._get_disk_usage", return_value=(500000000, 200000000)):
            snapshot = await service.generate_snapshot(sample_scan["id"])

        assert snapshot.scan_id == sample_scan["id"]
        assert snapshot.file_count == 11
        assert snapshot.dir_count == 6
        assert snapshot.total_size_bytes == 500000000
        assert snapshot.used_size_bytes == 300000000  # total - free

        # Verify category breakdown is valid JSON
        breakdown = json.loads(snapshot.category_breakdown)
        assert "video" in breakdown
        assert breakdown["video"] == 3000000

    async def test_raises_not_found_for_missing_scan(
        self, session: AsyncSession
    ) -> None:
        from app.core.exceptions import NotFoundError

        service = AnalyticsService(session)
        with pytest.raises(NotFoundError):
            await service.generate_snapshot("nonexistent-id")

    async def test_updates_existing_snapshot_for_same_date(
        self, session: AsyncSession, sample_scan: dict, sample_files: list, sample_folders: list
    ) -> None:
        service = AnalyticsService(session)

        with patch("app.services.analytics_service._get_disk_usage", return_value=(500000000, 200000000)):
            snap1 = await service.generate_snapshot(sample_scan["id"])
            await session.commit()
            snap2 = await service.generate_snapshot(sample_scan["id"])

        # Same snapshot updated, not duplicated
        assert snap1.id == snap2.id


class TestOverview:
    """Tests for get_overview."""

    async def test_returns_empty_overview_when_no_scans(
        self, session: AsyncSession
    ) -> None:
        service = AnalyticsService(session)
        overview = await service.get_overview()

        assert overview["total_storage"] == 0
        assert overview["file_count"] == 0
        assert overview["last_scan"] is None

    async def test_returns_data_from_snapshot(
        self, session: AsyncSession, sample_scan: dict, sample_files: list, sample_folders: list
    ) -> None:
        service = AnalyticsService(session)

        # Generate snapshot first
        with patch("app.services.analytics_service._get_disk_usage", return_value=(500000000, 200000000)):
            await service.generate_snapshot(sample_scan["id"])
            await session.commit()

        overview = await service.get_overview()
        assert overview["total_storage"] == 500000000
        assert overview["file_count"] == 11
        assert overview["dir_count"] == 6
        assert overview["last_scan"] is not None


class TestCategories:
    """Tests for get_categories."""

    async def test_returns_breakdown_from_live_data(
        self, session: AsyncSession, sample_scan: dict, sample_files: list
    ) -> None:
        service = AnalyticsService(session)
        result = await service.get_categories(scan_id=sample_scan["id"])

        assert result["breakdown"]["video"] == 3000000
        assert result["file_count"] == 11
        assert result["scan_id"] == sample_scan["id"]

    async def test_returns_empty_when_no_data(self, session: AsyncSession) -> None:
        service = AnalyticsService(session)
        result = await service.get_categories()

        assert result["breakdown"] == {}
        assert result["file_count"] == 0


class TestLargestFiles:
    """Tests for get_largest_files."""

    async def test_returns_largest_files_from_scan(
        self, session: AsyncSession, sample_scan: dict, sample_files: list
    ) -> None:
        service = AnalyticsService(session)
        result = await service.get_largest_files(scan_id=sample_scan["id"], limit=3)

        assert len(result["files"]) == 3
        assert result["files"][0]["size_bytes"] >= result["files"][1]["size_bytes"]
        assert result["scan_id"] == sample_scan["id"]
        assert result["total_count"] == 11

    async def test_uses_latest_scan_when_none_specified(
        self, session: AsyncSession, sample_scan: dict, sample_files: list
    ) -> None:
        service = AnalyticsService(session)
        result = await service.get_largest_files(limit=5)

        assert len(result["files"]) == 5
        assert result["scan_id"] == sample_scan["id"]


class TestLargestFolders:
    """Tests for get_largest_folders."""

    async def test_returns_folders_by_size(
        self, session: AsyncSession, sample_scan: dict, sample_folders: list
    ) -> None:
        service = AnalyticsService(session)
        result = await service.get_largest_folders(scan_id=sample_scan["id"], limit=3)

        assert len(result["folders"]) == 3
        assert result["folders"][0]["total_size_bytes"] >= result["folders"][1]["total_size_bytes"]
        assert result["total_count"] == 6


class TestGrowthRate:
    """Tests for _compute_growth_rate helper."""

    def test_computes_daily_rate(self) -> None:
        data = [
            {"date": "2026-06-01", "total_size_bytes": 100000000},
            {"date": "2026-06-11", "total_size_bytes": 110000000},
        ]
        rate = _compute_growth_rate(data)
        assert rate == 1000000.0  # 10M over 10 days = 1M/day

    def test_returns_zero_for_single_point(self) -> None:
        data = [{"date": "2026-06-01", "total_size_bytes": 100000000}]
        assert _compute_growth_rate(data) == 0.0

    def test_returns_zero_for_empty_data(self) -> None:
        assert _compute_growth_rate([]) == 0.0

    def test_handles_negative_growth(self) -> None:
        data = [
            {"date": "2026-06-01", "total_size_bytes": 200000000},
            {"date": "2026-06-11", "total_size_bytes": 150000000},
        ]
        rate = _compute_growth_rate(data)
        assert rate == -5000000.0  # Shrank by 50M over 10 days

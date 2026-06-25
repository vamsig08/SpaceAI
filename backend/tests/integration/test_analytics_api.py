"""Integration tests for analytics API endpoints.

Tests the full HTTP cycle: request → route → service → repository → DB → response.
Uses the test client with an in-memory SQLite database.
"""

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession


class TestHealthEndpoint:
    """Verify the test client works."""

    async def test_health_returns_200(self, api_client: AsyncClient) -> None:
        response = await api_client.get("/api/v1/health")
        assert response.status_code == 200
        assert response.json()["status"] == "healthy"


class TestOverviewEndpoint:
    """Tests for GET /api/v1/analytics/overview."""

    async def test_returns_empty_overview_when_no_data(
        self, api_client: AsyncClient
    ) -> None:
        response = await api_client.get("/api/v1/analytics/overview")
        assert response.status_code == 200

        data = response.json()["data"]
        assert data["total_storage"] == 0
        assert data["file_count"] == 0
        assert data["last_scan"] is None

    async def test_returns_overview_after_scan(
        self,
        api_client: AsyncClient,
        session: AsyncSession,
        sample_scan: dict,
        sample_files: list,
        sample_folders: list,
    ) -> None:
        response = await api_client.get("/api/v1/analytics/overview")
        assert response.status_code == 200

        data = response.json()["data"]
        assert data["file_count"] == 11
        assert data["dir_count"] == 6
        assert data["last_scan"] is not None


class TestCategoriesEndpoint:
    """Tests for GET /api/v1/analytics/categories."""

    async def test_returns_categories_for_scan(
        self,
        api_client: AsyncClient,
        session: AsyncSession,
        sample_scan: dict,
        sample_files: list,
    ) -> None:
        response = await api_client.get(
            "/api/v1/analytics/categories",
            params={"scan_id": sample_scan["id"]},
        )
        assert response.status_code == 200

        data = response.json()["data"]
        assert "breakdown" in data
        assert data["breakdown"]["video"] == 3000000
        assert data["file_count"] == 11

    async def test_returns_empty_for_no_data(self, api_client: AsyncClient) -> None:
        response = await api_client.get("/api/v1/analytics/categories")
        assert response.status_code == 200
        data = response.json()["data"]
        assert data["breakdown"] == {}


class TestExtensionsEndpoint:
    """Tests for GET /api/v1/analytics/extensions."""

    async def test_returns_extensions_sorted_by_size(
        self,
        api_client: AsyncClient,
        session: AsyncSession,
        sample_scan: dict,
        sample_files: list,
    ) -> None:
        response = await api_client.get(
            "/api/v1/analytics/extensions",
            params={"scan_id": sample_scan["id"], "limit": 5},
        )
        assert response.status_code == 200

        data = response.json()["data"]
        extensions = data["extensions"]
        assert len(extensions) == 5
        sizes = [e["total_bytes"] for e in extensions]
        assert sizes == sorted(sizes, reverse=True)


class TestLargestFilesEndpoint:
    """Tests for GET /api/v1/analytics/largest-files."""

    async def test_returns_largest_files(
        self,
        api_client: AsyncClient,
        session: AsyncSession,
        sample_scan: dict,
        sample_files: list,
    ) -> None:
        response = await api_client.get(
            "/api/v1/analytics/largest-files",
            params={"scan_id": sample_scan["id"], "limit": 3},
        )
        assert response.status_code == 200

        data = response.json()["data"]
        assert len(data["files"]) == 3
        assert data["files"][0]["size_bytes"] >= data["files"][1]["size_bytes"]
        assert data["total_count"] == 11

    async def test_returns_empty_when_no_scan(self, api_client: AsyncClient) -> None:
        response = await api_client.get("/api/v1/analytics/largest-files")
        assert response.status_code == 200
        data = response.json()["data"]
        assert data["files"] == []


class TestLargestFoldersEndpoint:
    """Tests for GET /api/v1/analytics/largest-folders."""

    async def test_returns_largest_folders(
        self,
        api_client: AsyncClient,
        session: AsyncSession,
        sample_scan: dict,
        sample_folders: list,
    ) -> None:
        response = await api_client.get(
            "/api/v1/analytics/largest-folders",
            params={"scan_id": sample_scan["id"], "limit": 3},
        )
        assert response.status_code == 200

        data = response.json()["data"]
        assert len(data["folders"]) == 3
        assert data["folders"][0]["total_size_bytes"] >= data["folders"][1]["total_size_bytes"]


class TestGrowthEndpoint:
    """Tests for GET /api/v1/analytics/growth."""

    async def test_returns_empty_growth_data(self, api_client: AsyncClient) -> None:
        response = await api_client.get(
            "/api/v1/analytics/growth", params={"period": 30}
        )
        assert response.status_code == 200

        data = response.json()["data"]
        assert data["data_points"] == []
        assert data["period_days"] == 30
        assert data["daily_growth_bytes"] == 0.0

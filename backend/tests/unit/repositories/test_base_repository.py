"""Unit tests for BaseRepository CRUD operations."""

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.scan import Scan
from app.models.base import generate_uuid, utc_now
from app.repositories.base import BaseRepository


class TestBaseRepositoryGetById:
    """Tests for get_by_id."""

    async def test_returns_entity_when_found(
        self, session: AsyncSession, sample_scan: dict
    ) -> None:
        repo = BaseRepository(Scan, session)
        result = await repo.get_by_id(sample_scan["id"])
        assert result is not None
        assert result.id == sample_scan["id"]

    async def test_returns_none_when_not_found(self, session: AsyncSession) -> None:
        repo = BaseRepository(Scan, session)
        result = await repo.get_by_id("nonexistent-id")
        assert result is None


class TestBaseRepositoryList:
    """Tests for paginated listing."""

    async def test_returns_all_records(
        self, session: AsyncSession, sample_scan: dict
    ) -> None:
        repo = BaseRepository(Scan, session)
        results = await repo.list()
        assert len(results) >= 1

    async def test_respects_limit(
        self, session: AsyncSession, sample_scan: dict
    ) -> None:
        repo = BaseRepository(Scan, session)
        results = await repo.list(limit=1)
        assert len(results) == 1

    async def test_supports_ordering(
        self, session: AsyncSession, sample_scan: dict
    ) -> None:
        repo = BaseRepository(Scan, session)
        results = await repo.list(order_by="created_at", order_desc=True)
        assert len(results) >= 1

    async def test_supports_filters(
        self, session: AsyncSession, sample_scan: dict
    ) -> None:
        repo = BaseRepository(Scan, session)
        results = await repo.list(filters=[Scan.status == "completed"])
        assert all(r.status == "completed" for r in results)

    async def test_empty_result_for_nonmatching_filter(
        self, session: AsyncSession, sample_scan: dict
    ) -> None:
        repo = BaseRepository(Scan, session)
        results = await repo.list(filters=[Scan.status == "nonexistent_status"])
        assert results == []


class TestBaseRepositoryCount:
    """Tests for record counting."""

    async def test_counts_all_records(
        self, session: AsyncSession, sample_scan: dict
    ) -> None:
        repo = BaseRepository(Scan, session)
        count = await repo.count()
        assert count >= 1

    async def test_counts_with_filter(
        self, session: AsyncSession, sample_scan: dict
    ) -> None:
        repo = BaseRepository(Scan, session)
        count = await repo.count(filters=[Scan.status == "completed"])
        assert count == 1

        count = await repo.count(filters=[Scan.status == "nonexistent"])
        assert count == 0


class TestBaseRepositoryCreate:
    """Tests for record creation."""

    async def test_creates_record(self, session: AsyncSession) -> None:
        repo = BaseRepository(Scan, session)
        scan = Scan(
            id=generate_uuid(),
            root_path="/tmp/new-scan",
            status="pending",
            scan_type="full",
            total_files=0,
            total_dirs=0,
            total_size_bytes=0,
            created_at=utc_now(),
        )
        result = await repo.create(scan)
        assert result.id == scan.id

        # Verify it's findable
        found = await repo.get_by_id(scan.id)
        assert found is not None


class TestBaseRepositoryUpdate:
    """Tests for record updates."""

    async def test_updates_fields(
        self, session: AsyncSession, sample_scan: dict
    ) -> None:
        repo = BaseRepository(Scan, session)
        result = await repo.update(sample_scan["id"], status="failed", error_message="test error")
        assert result is not None
        assert result.status == "failed"
        assert result.error_message == "test error"

    async def test_returns_none_for_nonexistent(self, session: AsyncSession) -> None:
        repo = BaseRepository(Scan, session)
        result = await repo.update("nonexistent-id", status="running")
        assert result is None


class TestBaseRepositoryDelete:
    """Tests for record deletion."""

    async def test_deletes_existing_record(
        self, session: AsyncSession, sample_scan: dict
    ) -> None:
        repo = BaseRepository(Scan, session)
        result = await repo.delete(sample_scan["id"])
        assert result is True

        # Verify gone
        found = await repo.get_by_id(sample_scan["id"])
        assert found is None

    async def test_returns_false_for_nonexistent(self, session: AsyncSession) -> None:
        repo = BaseRepository(Scan, session)
        result = await repo.delete("nonexistent-id")
        assert result is False

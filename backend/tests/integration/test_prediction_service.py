"""Integration tests for prediction service with DB persistence."""

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models.base import generate_uuid, utc_now
from app.services.prediction_service import PredictionService


async def _seed_snapshots(
    session: AsyncSession,
    days: int = 30,
    start_bytes: int = 200_000_000_000,
    daily_growth: int = 500_000_000,
) -> None:
    """Seed storage_snapshots table with synthetic time-series data."""
    scan_id = generate_uuid()
    now = utc_now()

    await session.execute(
        text("INSERT INTO scans (id, root_path, status, total_files, created_at) VALUES (:id, '/home', 'completed', 100, :now)"),
        {"id": scan_id, "now": now},
    )

    for d in range(days):
        total = start_bytes + daily_growth * d
        date = f"2026-{6 - days // 30:02d}-{(d % 28) + 1:02d}"
        if d >= 28:
            date = f"2026-06-{(d - 27):02d}"

        # Use sequential dates
        from datetime import datetime, timedelta
        base = datetime(2026, 5, 1)
        dt = base + timedelta(days=d)
        date_str = dt.strftime("%Y-%m-%d")

        await session.execute(
            text(
                """
                INSERT OR IGNORE INTO storage_snapshots (
                    id, scan_id, snapshot_date, total_size_bytes, used_size_bytes,
                    file_count, dir_count, category_breakdown, created_at
                ) VALUES (:id, :sid, :date, :total, :used, :files, :dirs, :cat, :now)
                """
            ),
            {
                "id": generate_uuid(),
                "sid": scan_id,
                "date": date_str,
                "total": total,
                "used": total,
                "files": 100000,
                "dirs": 5000,
                "cat": "{}",
                "now": now,
            },
        )

    await session.commit()


class TestPredictionServiceForecast:
    """Tests for forecast generation with DB persistence."""

    async def test_generates_forecast_from_snapshots(
        self, session: AsyncSession
    ) -> None:
        await _seed_snapshots(session, days=30, daily_growth=500_000_000)

        service = PredictionService(session)
        result = await service.generate_forecast(disk_capacity=500_000_000_000)

        assert "model_type" in result
        assert result["daily_growth_bytes"] > 0
        assert result["weekly_growth_bytes"] > 0
        assert result["predicted_total_30d"] > 0
        assert result["confidence"] > 0
        assert result["trend"] in ("stable", "slow_growth", "moderate_growth", "rapid_growth", "critical_growth")

    async def test_forecast_persisted_to_db(
        self, session: AsyncSession
    ) -> None:
        await _seed_snapshots(session, days=20)

        service = PredictionService(session)
        await service.generate_forecast(disk_capacity=500_000_000_000)
        await session.commit()

        # Should be retrievable
        latest = await service.get_latest_forecast()
        assert latest is not None
        assert latest["model_type"] != ""
        assert latest["input_snapshots"] >= 2

    async def test_insufficient_data_returns_error(
        self, session: AsyncSession
    ) -> None:
        # No snapshots seeded
        service = PredictionService(session)
        result = await service.generate_forecast()
        assert "error" in result

    async def test_exhaustion_estimate_after_forecast(
        self, session: AsyncSession
    ) -> None:
        await _seed_snapshots(session, days=30, start_bytes=200_000_000_000, daily_growth=1_000_000_000)

        service = PredictionService(session)
        await service.generate_forecast(disk_capacity=500_000_000_000)
        await session.commit()

        estimate = await service.get_exhaustion_estimate()
        assert estimate["days_until_full"] is not None
        assert estimate["days_until_full"] > 0
        assert estimate["daily_growth_bytes"] > 0

    async def test_growth_rate_after_forecast(
        self, session: AsyncSession
    ) -> None:
        await _seed_snapshots(session, days=30, daily_growth=500_000_000)

        service = PredictionService(session)
        await service.generate_forecast(disk_capacity=500_000_000_000)
        await session.commit()

        rate = await service.get_growth_rate()
        assert rate["daily_growth_bytes"] > 0
        assert rate["weekly_growth_bytes"] > 0
        assert rate["monthly_growth_bytes"] > 0
        assert rate["trend"] in ("stable", "slow_growth", "moderate_growth", "rapid_growth", "critical_growth")


class TestPredictionServiceNoData:
    """Tests for service behavior with no prediction data."""

    async def test_get_latest_returns_none(self, session: AsyncSession) -> None:
        service = PredictionService(session)
        result = await service.get_latest_forecast()
        assert result is None

    async def test_exhaustion_returns_zeros(self, session: AsyncSession) -> None:
        service = PredictionService(session)
        result = await service.get_exhaustion_estimate()
        assert result["days_until_full"] is None
        assert result["confidence"] == 0

    async def test_growth_rate_returns_stable(self, session: AsyncSession) -> None:
        service = PredictionService(session)
        result = await service.get_growth_rate()
        assert result["trend"] == "stable"
        assert result["daily_growth_bytes"] == 0

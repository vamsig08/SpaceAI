"""Prediction service — orchestrates forecasting and persists results.

Loads storage_snapshots, runs the prediction models, writes results to
the predictions table, and integrates with the recommendation engine.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.logging import get_logger
from app.models.base import generate_uuid, utc_now
from app.services.prediction_models import (
    ForecastResult,
    TimeSeriesPoint,
    exponential_smoothing_forecast,
    linear_regression_forecast,
    moving_average_forecast,
    select_best_model,
)
from app.workers.progress import ProgressReporter
from app.workers.task_manager import TaskState, TaskStatus

logger = get_logger(__name__)


class PredictionService:
    """Provides prediction queries and forecast generation."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_latest_forecast(self) -> dict[str, Any] | None:
        """Get the most recent prediction result.

        Returns:
            Prediction dict or None if no predictions exist.
        """
        result = await self._session.execute(
            text(
                """
                SELECT id, model_type, input_snapshots, daily_growth_bytes,
                       weekly_growth_bytes, predicted_total_30d, predicted_total_90d,
                       exhaustion_date, days_until_full, confidence,
                       confidence_interval, metadata, created_at
                FROM predictions ORDER BY created_at DESC LIMIT 1
                """
            )
        )
        row = result.one_or_none()
        if row is None:
            return None

        return {
            "id": row[0],
            "model_type": row[1],
            "input_snapshots": row[2],
            "daily_growth_bytes": row[3],
            "weekly_growth_bytes": row[4],
            "predicted_total_30d": row[5],
            "predicted_total_90d": row[6],
            "exhaustion_date": row[7],
            "days_until_full": row[8],
            "confidence": row[9],
            "confidence_interval": json.loads(row[10]) if row[10] else None,
            "metadata": json.loads(row[11]) if row[11] else None,
            "created_at": row[12],
        }

    async def get_exhaustion_estimate(self) -> dict[str, Any]:
        """Get disk exhaustion estimate from latest prediction.

        Returns:
            Dict with exhaustion details or empty placeholder.
        """
        prediction = await self.get_latest_forecast()
        if prediction is None:
            return {
                "exhaustion_date": None,
                "days_until_full": None,
                "daily_growth_bytes": 0,
                "weekly_growth_bytes": 0,
                "confidence": 0,
                "model_type": None,
            }

        return {
            "exhaustion_date": prediction["exhaustion_date"],
            "days_until_full": prediction["days_until_full"],
            "daily_growth_bytes": prediction["daily_growth_bytes"],
            "weekly_growth_bytes": prediction["weekly_growth_bytes"],
            "confidence": prediction["confidence"],
            "model_type": prediction["model_type"],
        }

    async def get_growth_rate(self) -> dict[str, Any]:
        """Get current growth rate from latest prediction.

        Returns:
            Dict with growth metrics and trend.
        """
        prediction = await self.get_latest_forecast()
        if prediction is None:
            return {
                "daily_growth_bytes": 0,
                "weekly_growth_bytes": 0,
                "monthly_growth_bytes": 0,
                "trend": "stable",
                "confidence": 0,
            }

        meta = prediction.get("metadata") or {}
        return {
            "daily_growth_bytes": prediction["daily_growth_bytes"],
            "weekly_growth_bytes": prediction["weekly_growth_bytes"],
            "monthly_growth_bytes": prediction["daily_growth_bytes"] * 30,
            "trend": meta.get("trend", "stable"),
            "confidence": prediction["confidence"],
        }

    async def generate_forecast(
        self, disk_capacity: int | None = None
    ) -> dict[str, Any]:
        """Generate a new forecast from storage snapshots.

        Loads historical snapshots, selects the best model, runs the
        forecast, and persists the result.

        Args:
            disk_capacity: Total disk capacity in bytes. If None, attempts
                           to read from latest snapshot.

        Returns:
            The generated forecast as a dict.
        """
        # Load snapshots
        result = await self._session.execute(
            text(
                "SELECT snapshot_date, total_size_bytes FROM storage_snapshots "
                "ORDER BY snapshot_date ASC"
            )
        )
        rows = result.all()

        if len(rows) < 2:
            return {"error": "Insufficient data", "snapshots_available": len(rows)}

        # Convert to time series
        base_date = datetime.strptime(rows[0][0], "%Y-%m-%d")
        data_points = []
        for date_str, total_bytes in rows:
            dt = datetime.strptime(date_str, "%Y-%m-%d")
            day_index = (dt - base_date).days
            data_points.append(TimeSeriesPoint(day_index=day_index, total_bytes=total_bytes))

        # Determine disk capacity
        if disk_capacity is None:
            # Use latest snapshot's total as approximation (will be overridden if disk info available)
            disk_capacity = data_points[-1].total_bytes * 2  # Assume 50% used

        # Run forecast
        forecast = select_best_model(data_points, disk_capacity)

        # Persist
        prediction_id = generate_uuid()
        now = utc_now()
        meta = json.dumps({"trend": forecast.trend, "r_squared": forecast.r_squared})

        await self._session.execute(
            text(
                """
                INSERT INTO predictions (
                    id, model_type, input_snapshots, daily_growth_bytes,
                    weekly_growth_bytes, predicted_total_30d, predicted_total_90d,
                    exhaustion_date, days_until_full, confidence,
                    confidence_interval, metadata, created_at
                ) VALUES (
                    :id, :model, :inputs, :daily, :weekly, :p30, :p90,
                    :exhaustion, :days, :conf, :ci, :meta, :now
                )
                """
            ),
            {
                "id": prediction_id,
                "model": forecast.model_type,
                "inputs": forecast.data_points_used,
                "daily": forecast.daily_growth_bytes,
                "weekly": forecast.weekly_growth_bytes,
                "p30": forecast.predicted_total_30d,
                "p90": forecast.predicted_total_90d,
                "exhaustion": forecast.exhaustion_date,
                "days": forecast.days_until_full,
                "conf": forecast.confidence,
                "ci": None,
                "meta": meta,
                "now": now,
            },
        )
        await self._session.flush()

        logger.info(
            "forecast_generated",
            model=forecast.model_type,
            daily_growth=forecast.daily_growth_bytes,
            days_until_full=forecast.days_until_full,
            confidence=forecast.confidence,
            trend=forecast.trend,
        )

        return {
            "id": prediction_id,
            "model_type": forecast.model_type,
            "daily_growth_bytes": forecast.daily_growth_bytes,
            "weekly_growth_bytes": forecast.weekly_growth_bytes,
            "predicted_total_30d": forecast.predicted_total_30d,
            "predicted_total_90d": forecast.predicted_total_90d,
            "exhaustion_date": forecast.exhaustion_date,
            "days_until_full": forecast.days_until_full,
            "confidence": forecast.confidence,
            "trend": forecast.trend,
            "data_points_used": forecast.data_points_used,
        }

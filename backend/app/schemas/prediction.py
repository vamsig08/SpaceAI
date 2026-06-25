"""Pydantic schemas for prediction API."""

from typing import Any

from pydantic import BaseModel, Field


class ForecastResponse(BaseModel):
    """Forecast generation result."""

    id: str | None = None
    model_type: str
    daily_growth_bytes: float
    weekly_growth_bytes: float
    predicted_total_30d: int
    predicted_total_90d: int
    exhaustion_date: str | None = None
    days_until_full: int | None = None
    confidence: float
    trend: str
    data_points_used: int = 0


class ExhaustionResponse(BaseModel):
    """Disk exhaustion estimate."""

    exhaustion_date: str | None = None
    days_until_full: int | None = None
    daily_growth_bytes: float = 0
    weekly_growth_bytes: float = 0
    confidence: float = 0
    model_type: str | None = None


class GrowthRateResponse(BaseModel):
    """Current growth rate analysis."""

    daily_growth_bytes: float = 0
    weekly_growth_bytes: float = 0
    monthly_growth_bytes: float = 0
    trend: str = "stable"
    confidence: float = 0


class GenerateForecastRequest(BaseModel):
    """Request to generate a new forecast."""

    disk_capacity: int | None = Field(
        default=None, ge=0, description="Total disk capacity in bytes (auto-detected if None)"
    )

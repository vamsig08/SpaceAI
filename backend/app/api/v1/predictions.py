"""Prediction API endpoints — Phase 7."""

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies import get_session
from app.schemas.common import SingleResponse
from app.schemas.prediction import (
    ExhaustionResponse,
    ForecastResponse,
    GenerateForecastRequest,
    GrowthRateResponse,
)
from app.services.prediction_service import PredictionService

router = APIRouter(prefix="/predictions", tags=["predictions"])


@router.post("/forecast", response_model=dict)
async def generate_forecast(
    body: GenerateForecastRequest,
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Generate a new storage forecast from historical snapshots.

    Uses automatic model selection (linear regression, moving average,
    or exponential smoothing) based on data characteristics.
    """
    service = PredictionService(session)
    data = await service.generate_forecast(disk_capacity=body.disk_capacity)
    return {"data": data}


@router.get("/exhaustion", response_model=SingleResponse[ExhaustionResponse])
async def get_exhaustion(
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Get estimated date when disk will be full."""
    service = PredictionService(session)
    data = await service.get_exhaustion_estimate()
    return {"data": data}


@router.get("/growth-rate", response_model=SingleResponse[GrowthRateResponse])
async def get_growth_rate(
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Get current storage growth rate and trend classification."""
    service = PredictionService(session)
    data = await service.get_growth_rate()
    return {"data": data}

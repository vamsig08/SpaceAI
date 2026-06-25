"""Analytics API endpoints — Phase 2.

All endpoints read from pre-computed snapshots for <200ms response times.
No business logic in this layer — delegates entirely to AnalyticsService.
"""

from fastapi import APIRouter, Depends, Query

from app.dependencies import get_analytics_service
from app.schemas.analytics import (
    CategoryBreakdown,
    ExtensionBreakdown,
    GrowthHistory,
    LargestFilesResponse,
    LargestFoldersResponse,
    StorageOverview,
)
from app.schemas.common import SingleResponse
from app.services.analytics_service import AnalyticsService

router = APIRouter(prefix="/analytics", tags=["analytics"])


@router.get("/overview", response_model=SingleResponse[StorageOverview])
async def get_overview(
    service: AnalyticsService = Depends(get_analytics_service),
) -> dict:
    """Get storage overview dashboard data.

    Returns total/used/free storage, file counts, and recovery opportunities.
    Served from pre-computed snapshot for fast response times.
    """
    data = await service.get_overview()
    return {"data": data}


@router.get("/categories", response_model=SingleResponse[CategoryBreakdown])
async def get_categories(
    scan_id: str | None = Query(default=None, description="Scan ID or latest"),
    service: AnalyticsService = Depends(get_analytics_service),
) -> dict:
    """Get file category breakdown (video, image, document, code, etc.).

    By default uses the latest scan. Specify scan_id for a specific scan.
    """
    data = await service.get_categories(scan_id=scan_id)
    return {"data": data}


@router.get("/extensions", response_model=SingleResponse[ExtensionBreakdown])
async def get_extensions(
    scan_id: str | None = Query(default=None),
    limit: int = Query(default=20, ge=1, le=100),
    service: AnalyticsService = Depends(get_analytics_service),
) -> dict:
    """Get top file extensions ranked by total size."""
    data = await service.get_extensions(scan_id=scan_id, limit=limit)
    return {"data": data}


@router.get("/largest-files", response_model=SingleResponse[LargestFilesResponse])
async def get_largest_files(
    scan_id: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
    service: AnalyticsService = Depends(get_analytics_service),
) -> dict:
    """Get the largest files discovered in a scan."""
    data = await service.get_largest_files(scan_id=scan_id, limit=limit)
    return {"data": data}


@router.get("/largest-folders", response_model=SingleResponse[LargestFoldersResponse])
async def get_largest_folders(
    scan_id: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    service: AnalyticsService = Depends(get_analytics_service),
) -> dict:
    """Get the largest directories discovered in a scan."""
    data = await service.get_largest_folders(scan_id=scan_id, limit=limit)
    return {"data": data}


@router.get("/growth", response_model=SingleResponse[GrowthHistory])
async def get_growth(
    days: int = Query(default=30, ge=1, le=365, alias="period"),
    from_date: str | None = Query(default=None, alias="from"),
    to_date: str | None = Query(default=None, alias="to"),
    service: AnalyticsService = Depends(get_analytics_service),
) -> dict:
    """Get storage growth history for trend visualization.

    Returns daily snapshots for the requested period.
    Use 'period' for relative days, or 'from'/'to' for absolute date range.
    """
    data = await service.get_growth_history(
        days=days, from_date=from_date, to_date=to_date
    )
    return {"data": data}

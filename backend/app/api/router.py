"""Root API router — aggregates all versioned endpoint routers."""

from fastapi import APIRouter

from app.api.v1.analytics import router as analytics_router
from app.api.v1.duplicates import router as duplicates_router
from app.api.v1.stale import router as stale_router
from app.api.v1.workspaces import router as workspaces_router
from app.api.v1.recommendations import router as recommendations_router
from app.api.v1.predictions import router as predictions_router
from app.api.v1.cleanup import router as cleanup_router
from app.api.v1.scans import router as scans_router

api_router = APIRouter(prefix="/api/v1")

api_router.include_router(scans_router)
api_router.include_router(analytics_router)
api_router.include_router(duplicates_router)
api_router.include_router(stale_router)
api_router.include_router(workspaces_router)
api_router.include_router(recommendations_router)
api_router.include_router(predictions_router)
api_router.include_router(cleanup_router)

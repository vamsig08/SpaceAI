"""Recommendation API endpoints — Phase 6."""

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies import get_session, get_task_manager, get_progress_reporter
from app.schemas.common import SingleResponse
from app.schemas.recommendation import (
    RecommendationDetail,
    RecommendationGenerateRequest,
    RecommendationGenerateResponse,
    RecommendationUpdateRequest,
)
from app.services.recommendation_service import RecommendationService, run_recommendation_generation
from app.workers.progress import ProgressReporter
from app.workers.task_manager import TaskManager, TaskType

router = APIRouter(prefix="/recommendations", tags=["recommendations"])


@router.post("/generate", response_model=SingleResponse[RecommendationGenerateResponse], status_code=202)
async def generate_recommendations(
    body: RecommendationGenerateRequest,
    request: Request,
    task_manager: TaskManager = Depends(get_task_manager),
    reporter: ProgressReporter = Depends(get_progress_reporter),
) -> dict:
    """Start recommendation generation as a background task."""
    session_factory = request.app.state.session_factory

    task_id = await task_manager.submit(
        TaskType.RECOMMENDATION,
        run_recommendation_generation,
        scan_id=body.scan_id,
        session_factory=session_factory,
        reporter=reporter,
    )

    return {"data": {"task_id": task_id, "scan_id": body.scan_id, "status": "pending"}}


@router.get("", response_model=dict)
async def list_recommendations(
    scan_id: str = Query(...),
    status: str | None = Query(default=None, pattern="^(pending|accepted|dismissed)$"),
    priority: str | None = Query(default=None, pattern="^(critical|high|medium|low)$"),
    category: str | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """List recommendations with optional filters."""
    service = RecommendationService(session)
    result = await service.get_recommendations(
        scan_id, status=status, priority=priority,
        category=category, page=page, page_size=page_size,
    )
    return {"data": result["recommendations"], "meta": result["meta"]}


@router.get("/{rec_id}", response_model=SingleResponse[RecommendationDetail])
async def get_recommendation(
    rec_id: str,
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Get full details of a recommendation."""
    service = RecommendationService(session)
    data = await service.get_recommendation_detail(rec_id)
    return {"data": data}


@router.patch("/{rec_id}", response_model=dict)
async def update_recommendation(
    rec_id: str,
    body: RecommendationUpdateRequest,
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Accept or dismiss a recommendation."""
    service = RecommendationService(session)
    result = await service.update_status(rec_id, body.status, body.dismissed_reason)
    return {"data": result}

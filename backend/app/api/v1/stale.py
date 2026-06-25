"""Stale file analysis API endpoints — Phase 4.

Provides stale file scoring, classification, and developer artifact detection.
"""

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies import get_session, get_task_manager, get_progress_reporter
from app.schemas.common import SingleResponse
from app.schemas.stale import (
    DevArtifactSummary,
    StaleAnalyzeRequest,
    StaleAnalyzeResponse,
    StaleSummary,
)
from app.services.stale_file_service import StaleFileService, run_stale_analysis
from app.workers.progress import ProgressReporter
from app.workers.task_manager import TaskManager, TaskType

router = APIRouter(prefix="/stale", tags=["stale"])


@router.post("/analyze", response_model=SingleResponse[StaleAnalyzeResponse], status_code=202)
async def analyze_stale_files(
    body: StaleAnalyzeRequest,
    request: Request,
    task_manager: TaskManager = Depends(get_task_manager),
    reporter: ProgressReporter = Depends(get_progress_reporter),
) -> dict:
    """Start stale file analysis as a background task.

    Scores all files in the scan for staleness based on access/modify timestamps.
    Returns immediately with a task_id for progress tracking.
    """
    session_factory = request.app.state.session_factory

    task_id = await task_manager.submit(
        TaskType.ANALYTICS,
        run_stale_analysis,
        scan_id=body.scan_id,
        session_factory=session_factory,
        reporter=reporter,
        active_days=body.active_days,
        aging_days=body.aging_days,
        stale_days=body.stale_days,
    )

    return {
        "data": {
            "task_id": task_id,
            "scan_id": body.scan_id,
            "status": "pending",
        }
    }


@router.get("/summary", response_model=SingleResponse[StaleSummary])
async def get_stale_summary(
    scan_id: str = Query(..., description="Scan ID"),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Get stale file analysis summary with classification breakdown."""
    service = StaleFileService(session)
    data = await service.get_stale_summary(scan_id)
    return {"data": data}


@router.get("/files", response_model=dict)
async def list_stale_files(
    scan_id: str = Query(...),
    classification: str | None = Query(default=None, pattern="^(aging|stale|archive_candidate)$"),
    risk_level: str | None = Query(default=None, pattern="^(low|medium|high)$"),
    category: str | None = Query(default=None),
    min_size: int | None = Query(default=None, ge=0),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """List stale files with filtering and pagination."""
    service = StaleFileService(session)
    result = await service.get_stale_files(
        scan_id,
        classification=classification,
        risk_level=risk_level,
        category=category,
        min_size=min_size,
        page=page,
        page_size=page_size,
    )
    return {"data": result["files"], "meta": result["meta"]}


@router.get("/dev-artifacts", response_model=SingleResponse[DevArtifactSummary])
async def get_dev_artifacts(
    scan_id: str = Query(..., description="Scan ID"),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Get developer artifact analysis (node_modules, .venv, build dirs, etc)."""
    service = StaleFileService(session)
    data = await service.get_dev_artifact_summary(scan_id)
    return {"data": data}

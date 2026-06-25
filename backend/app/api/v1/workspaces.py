"""Developer workspace API endpoints — Phase 5."""

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies import get_session, get_task_manager, get_progress_reporter
from app.schemas.common import SingleResponse
from app.schemas.workspace import (
    AbandonedProjectsResponse,
    WorkspaceAnalyzeRequest,
    WorkspaceAnalyzeResponse,
    WorkspaceSummary,
)
from app.services.workspace_service import WorkspaceService, run_workspace_analysis
from app.workers.progress import ProgressReporter
from app.workers.task_manager import TaskManager, TaskType

router = APIRouter(prefix="/workspaces", tags=["workspaces"])


@router.post("/analyze", response_model=SingleResponse[WorkspaceAnalyzeResponse], status_code=202)
async def analyze_workspaces(
    body: WorkspaceAnalyzeRequest,
    request: Request,
    task_manager: TaskManager = Depends(get_task_manager),
    reporter: ProgressReporter = Depends(get_progress_reporter),
) -> dict:
    """Start developer workspace analysis as a background task."""
    session_factory = request.app.state.session_factory

    task_id = await task_manager.submit(
        TaskType.ANALYTICS,
        run_workspace_analysis,
        scan_id=body.scan_id,
        session_factory=session_factory,
        reporter=reporter,
    )

    return {"data": {"task_id": task_id, "scan_id": body.scan_id, "status": "pending"}}


@router.get("/summary", response_model=SingleResponse[WorkspaceSummary])
async def get_workspace_summary(
    scan_id: str = Query(...),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Get workspace analysis summary with per-type breakdown."""
    service = WorkspaceService(session)
    data = await service.get_workspace_summary(scan_id)
    return {"data": data}


@router.get("", response_model=dict)
async def list_workspaces(
    scan_id: str = Query(...),
    workspace_type: str | None = Query(default=None),
    is_active: bool | None = Query(default=None),
    min_size: int | None = Query(default=None, ge=0),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """List detected developer workspaces with filters."""
    service = WorkspaceService(session)
    result = await service.list_workspaces(
        scan_id, workspace_type=workspace_type, is_active=is_active,
        min_size=min_size, page=page, page_size=page_size,
    )
    return {"data": result["workspaces"], "meta": result["meta"]}


@router.get("/abandoned", response_model=SingleResponse[AbandonedProjectsResponse])
async def get_abandoned_projects(
    scan_id: str = Query(...),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Get list of abandoned/inactive developer projects."""
    service = WorkspaceService(session)
    data = await service.get_abandoned_projects(scan_id)
    return {"data": data}

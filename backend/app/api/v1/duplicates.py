"""Duplicate detection API endpoints — Phase 3."""

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies import get_session, get_task_manager, get_progress_reporter
from app.schemas.common import SingleResponse
from app.schemas.duplicate import (
    DuplicateDetectRequest,
    DuplicateDetectResponse,
    DuplicateGroupDetail,
    DuplicateResolveRequest,
    DuplicateResolveResponse,
    DuplicateSummary,
)
from app.services.duplicate_service import DuplicateService, run_duplicate_detection
from app.workers.task_manager import TaskManager, TaskType
from app.workers.progress import ProgressReporter

router = APIRouter(prefix="/duplicates", tags=["duplicates"])


@router.post("/detect", response_model=SingleResponse[DuplicateDetectResponse], status_code=202)
async def detect_duplicates(
    body: DuplicateDetectRequest,
    request: Request,
    task_manager: TaskManager = Depends(get_task_manager),
    reporter: ProgressReporter = Depends(get_progress_reporter),
) -> dict:
    """Start duplicate detection pipeline as a background task."""
    session_factory = request.app.state.session_factory
    task_id = await task_manager.submit(
        TaskType.HASH, run_duplicate_detection,
        scan_id=body.scan_id, session_factory=session_factory,
        thread_pool=task_manager.thread_pool, reporter=reporter,
    )
    return {"data": {"task_id": task_id, "scan_id": body.scan_id, "status": "pending"}}


@router.get("/summary", response_model=SingleResponse[DuplicateSummary])
async def get_summary(
    scan_id: str = Query(...),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Get aggregate duplicate statistics for a scan."""
    service = DuplicateService(session)
    data = await service.get_summary(scan_id)
    return {"data": data}


@router.get("/cleanup-paths", response_model=dict)
async def get_cleanup_paths(
    scan_id: str = Query(...),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Get actual filesystem paths of duplicate files eligible for cleanup."""
    from app.repositories.duplicate_repository import DuplicateRepository
    from sqlalchemy import text as sql_text
    repo = DuplicateRepository(session)
    paths = await repo.get_all_non_keeper_paths(scan_id)
    if paths:
        placeholders = ",".join(f"'{p}'" for p in paths[:1000])
        result = await session.execute(
            sql_text(f"SELECT COALESCE(SUM(size_bytes), 0) FROM files WHERE path IN ({placeholders})")
        )
        total_bytes = result.scalar_one()
    else:
        total_bytes = 0
    return {"data": {"paths": paths, "total_bytes": total_bytes, "file_count": len(paths)}}


@router.get("", response_model=dict)
async def list_groups(
    scan_id: str = Query(...),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
    min_wasted: int | None = Query(default=None, ge=0),
    status: str | None = Query(default=None, pattern="^(unresolved|resolved)$"),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """List duplicate groups with pagination and filters."""
    service = DuplicateService(session)
    result = await service.list_groups(scan_id, page=page, page_size=page_size, min_wasted=min_wasted, status=status)
    return {"data": result["groups"], "meta": result["meta"]}


@router.get("/{group_id}", response_model=SingleResponse[DuplicateGroupDetail])
async def get_group_detail(
    group_id: str,
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Get full details of a duplicate group with all member files."""
    service = DuplicateService(session)
    data = await service.get_group_detail(group_id)
    return {"data": data}


@router.post("/{group_id}/resolve", response_model=SingleResponse[DuplicateResolveResponse])
async def resolve_group(
    group_id: str,
    body: DuplicateResolveRequest,
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Mark which file to keep in a duplicate group."""
    service = DuplicateService(session)
    data = await service.resolve_group(group_id, body.keep_file_id)
    return {"data": data}

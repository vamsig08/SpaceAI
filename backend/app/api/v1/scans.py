"""Scan API endpoints — create, list, detail, progress SSE, cancel."""

import asyncio
import json

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import NotFoundError
from app.dependencies import get_session, get_task_manager, get_progress_reporter
from app.schemas.common import SingleResponse
from app.schemas.scan import ScanCreate, ScanResponse
from app.services.scanner_service import ScannerService
from app.workers.progress import ProgressEvent, ProgressReporter
from app.workers.task_manager import TaskManager

router = APIRouter(prefix="/scans", tags=["scans"])


@router.post("", response_model=SingleResponse[ScanResponse], status_code=202)
async def create_scan(
    body: ScanCreate,
    request: Request,
    session: AsyncSession = Depends(get_session),
    task_manager: TaskManager = Depends(get_task_manager),
    reporter: ProgressReporter = Depends(get_progress_reporter),
) -> dict:
    """Start a new filesystem scan.

    Creates a scan record and launches the background scanner task.
    Returns immediately with the scan ID for progress tracking.
    """
    settings = request.app.state.settings
    session_factory = request.app.state.session_factory

    service = ScannerService(session)
    data = await service.create_scan(
        root_path=body.root_path,
        scan_type=body.scan_type,
        exclusions=body.exclusions,
        max_depth=body.max_depth,
        task_manager=task_manager,
        session_factory=session_factory,
        reporter=reporter,
        batch_size=settings.scanner_batch_size,
        checkpoint_interval=settings.scanner_checkpoint_interval,
    )
    return {"data": data}


@router.get("", response_model=dict)
async def list_scans(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """List all scans ordered by creation date."""
    service = ScannerService(session)
    result = await service.list_scans(page=page, page_size=page_size)
    return {"data": result["scans"], "meta": result["meta"]}


@router.get("/{scan_id}", response_model=SingleResponse[ScanResponse])
async def get_scan(
    scan_id: str,
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Get details of a specific scan."""
    service = ScannerService(session)
    data = await service.get_scan(scan_id)
    return {"data": data}


@router.delete("/{scan_id}", response_model=dict)
async def cancel_scan(
    scan_id: str,
    session: AsyncSession = Depends(get_session),
    task_manager: TaskManager = Depends(get_task_manager),
) -> dict:
    """Cancel a running scan."""
    service = ScannerService(session)
    data = await service.cancel_scan(scan_id, task_manager)
    return {"data": data}


@router.get("/{scan_id}/progress")
async def scan_progress(
    scan_id: str,
    request: Request,
    reporter: ProgressReporter = Depends(get_progress_reporter),
) -> StreamingResponse:
    """Stream scan progress via Server-Sent Events.

    Connects to the ProgressReporter and streams events until the scan
    completes, fails, or the client disconnects.
    """
    queue = await reporter.subscribe(scan_id)

    async def event_generator():
        try:
            while True:
                if await request.is_disconnected():
                    break
                try:
                    event: ProgressEvent = await asyncio.wait_for(queue.get(), timeout=30.0)
                    yield event.to_sse()
                    if event.is_terminal:
                        break
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"
        finally:
            await reporter.unsubscribe(scan_id, queue)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )

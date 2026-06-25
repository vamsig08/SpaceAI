"""Cleanup API endpoints — Phase 8 Safety Framework."""

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies import get_session
from app.schemas.cleanup import (
    CleanupProposeRequest,
    CleanupProposeResponse,
    DryRunResponse,
    ExecuteResponse,
    RollbackResponse,
)
from app.schemas.common import SingleResponse
from app.services.audit_service import AuditService
from app.services.cleanup_service import CleanupService

router = APIRouter(prefix="/cleanup", tags=["cleanup"])


@router.post("/propose", response_model=SingleResponse[CleanupProposeResponse])
async def propose_cleanup(
    body: CleanupProposeRequest,
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Propose a cleanup action for review."""
    service = CleanupService(session)
    data = await service.propose_cleanup(
        recommendation_id=body.recommendation_id,
        action_type=body.action_type,
        target_paths=body.target_paths,
        total_bytes=body.total_bytes,
    )
    return {"data": data}


@router.post("/actions/{action_id}/dry-run", response_model=SingleResponse[DryRunResponse])
async def dry_run(
    action_id: str,
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Execute a dry-run to validate targets without moving files."""
    service = CleanupService(session)
    data = await service.dry_run(action_id)
    return {"data": data}


@router.post("/actions/{action_id}/approve", response_model=dict)
async def approve_action(
    action_id: str,
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Approve a cleanup action for execution."""
    service = CleanupService(session)
    data = await service.approve(action_id)
    return {"data": data}


@router.post("/actions/{action_id}/execute", response_model=SingleResponse[ExecuteResponse])
async def execute_action(
    action_id: str,
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Execute an approved cleanup action (moves files to trash)."""
    service = CleanupService(session)
    data = await service.execute(action_id)
    return {"data": data}


@router.post("/actions/{action_id}/rollback", response_model=SingleResponse[RollbackResponse])
async def rollback_action(
    action_id: str,
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Rollback a completed cleanup (restore files from trash)."""
    service = CleanupService(session)
    data = await service.rollback(action_id)
    return {"data": data}


@router.get("/actions/{action_id}", response_model=dict)
async def get_action(
    action_id: str,
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Get details of a cleanup action."""
    service = CleanupService(session)
    data = await service.get_action(action_id)
    return {"data": data}


@router.get("/actions", response_model=dict)
async def list_actions(
    status: str | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """List cleanup actions with optional status filter."""
    service = CleanupService(session)
    result = await service.list_actions(status=status, page=page, page_size=page_size)
    return {"data": result["actions"], "meta": result["meta"]}


@router.get("/audit-log", response_model=dict)
async def get_audit_log(
    action: str | None = Query(default=None),
    entity_type: str | None = Query(default=None),
    severity: str | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Query the immutable audit log."""
    service = AuditService(session)
    result = await service.get_logs(
        action=action, entity_type=entity_type,
        severity=severity, page=page, page_size=page_size,
    )
    return {"data": result["logs"], "meta": result["meta"]}

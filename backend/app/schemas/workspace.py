"""Pydantic schemas for developer workspace API."""

from typing import Any

from pydantic import BaseModel, Field


class WorkspaceAnalyzeRequest(BaseModel):
    """Request to start workspace analysis."""

    scan_id: str


class WorkspaceAnalyzeResponse(BaseModel):
    """Response after starting workspace analysis."""

    task_id: str
    scan_id: str
    status: str = "pending"


class WorkspaceTypeSummary(BaseModel):
    """Per-type workspace summary."""

    count: int
    total_bytes: int
    recoverable_bytes: int
    safe_recoverable_bytes: int


class WorkspaceSummary(BaseModel):
    """Overall workspace analysis summary."""

    scan_id: str
    total_workspaces: int
    total_recoverable_bytes: int
    safe_recoverable_bytes: int
    inactive_workspaces: int
    by_type: dict[str, WorkspaceTypeSummary]


class WorkspaceEntry(BaseModel):
    """Single workspace in listing."""

    id: str
    path: str
    name: str
    workspace_type: str
    total_size_bytes: int
    recoverable_bytes: int
    safe_recoverable_bytes: int
    last_modified_at: str | None = None
    is_active: bool = True
    days_inactive: int | None = None
    risk_level: str = "low"
    artifacts: list[dict[str, Any]] = Field(default_factory=list)


class AbandonedProject(BaseModel):
    """Single abandoned project entry."""

    path: str
    name: str
    workspace_type: str
    total_size_bytes: int
    recoverable_bytes: int
    days_inactive: int | None = None
    last_modified_at: str | None = None


class AbandonedProjectsResponse(BaseModel):
    """Abandoned projects analysis."""

    scan_id: str
    abandoned_count: int
    total_recoverable_bytes: int
    projects: list[AbandonedProject]

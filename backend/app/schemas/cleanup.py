"""Pydantic schemas for cleanup API."""

from typing import Any

from pydantic import BaseModel, Field


class CleanupProposeRequest(BaseModel):
    """Request to propose a cleanup action."""

    recommendation_id: str | None = None
    action_type: str = Field(..., pattern="^(trash|archive|compress)$")
    target_paths: list[str] = Field(..., min_length=1)
    total_bytes: int = Field(..., ge=0)


class CleanupProposeResponse(BaseModel):
    """Response after proposing cleanup."""

    id: str
    action_type: str
    target_count: int
    total_bytes: int
    status: str = "proposed"


class DryRunResponse(BaseModel):
    """Response from a dry-run."""

    action_id: str
    status: str
    valid_count: int
    missing_count: int
    valid_bytes: int


class ExecuteResponse(BaseModel):
    """Response after executing cleanup."""

    action_id: str
    status: str
    files_processed: int
    bytes_recovered: int
    errors: int
    trash_location: str


class RollbackResponse(BaseModel):
    """Response after rolling back a cleanup."""

    action_id: str
    status: str
    files_restored: int
    bytes_restored: int
    errors: int


class CleanupActionSummary(BaseModel):
    """Summary of a cleanup action in listing."""

    id: str
    action_type: str
    target_count: int
    total_bytes: int
    status: str
    bytes_recovered: int
    created_at: str
    executed_at: str | None = None
    completed_at: str | None = None

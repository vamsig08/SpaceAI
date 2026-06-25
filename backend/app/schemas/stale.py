"""Pydantic schemas for stale file analysis API."""

from typing import Any

from pydantic import BaseModel, Field


class StaleAnalyzeRequest(BaseModel):
    """Request to start stale file analysis."""

    scan_id: str
    active_days: int = Field(default=30, ge=1, description="Days threshold for active classification")
    aging_days: int = Field(default=180, ge=1, description="Days threshold for aging classification")
    stale_days: int = Field(default=365, ge=1, description="Days threshold for stale classification")


class StaleAnalyzeResponse(BaseModel):
    """Response after starting stale analysis task."""

    task_id: str
    scan_id: str
    status: str = "pending"


class ClassificationDetail(BaseModel):
    """Count and bytes for a classification tier."""

    count: int
    bytes: int


class RiskDetail(BaseModel):
    """Count and bytes for a risk level."""

    count: int
    bytes: int


class StaleSummary(BaseModel):
    """Stale file analysis summary."""

    scan_id: str
    classification: dict[str, ClassificationDetail]
    recoverable_bytes: int
    risk_breakdown: dict[str, RiskDetail]
    total_stale_files: int


class StaleFileEntry(BaseModel):
    """Single stale file in listing."""

    id: str
    path: str
    filename: str
    extension: str | None = None
    size_bytes: int
    category: str | None = None
    stale_score: float | None = None
    risk_level: str | None = None
    accessed_at: str | None = None
    modified_at: str | None = None


class DevArtifactDetail(BaseModel):
    """Aggregation for a developer artifact type."""

    count: int
    bytes: int


class DevArtifactSummary(BaseModel):
    """Developer artifact analysis response."""

    scan_id: str
    artifacts: dict[str, DevArtifactDetail]
    total_recoverable_bytes: int
    total_artifact_files: int

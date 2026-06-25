"""Pydantic schemas for recommendation API."""

from typing import Any

from pydantic import BaseModel, Field


class RecommendationGenerateRequest(BaseModel):
    """Request to generate recommendations."""

    scan_id: str


class RecommendationGenerateResponse(BaseModel):
    """Response after starting recommendation generation."""

    task_id: str
    scan_id: str
    status: str = "pending"


class RecommendationEntry(BaseModel):
    """Single recommendation in listing."""

    id: str
    category: str
    priority: str
    title: str
    description: str
    explanation: str | None = None
    recoverable_bytes: int
    confidence: float
    affected_count: int = 0
    status: str = "pending"
    created_at: str


class RecommendationDetail(BaseModel):
    """Full recommendation detail."""

    id: str
    scan_id: str
    category: str
    priority: str
    title: str
    description: str
    explanation: str | None = None
    recoverable_bytes: int
    confidence: float
    affected_paths: list[str] = Field(default_factory=list)
    affected_count: int = 0
    status: str
    dismissed_reason: str | None = None
    created_at: str


class RecommendationUpdateRequest(BaseModel):
    """Request to update recommendation status."""

    status: str = Field(..., pattern="^(accepted|dismissed)$")
    dismissed_reason: str | None = None

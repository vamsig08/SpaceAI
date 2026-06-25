"""Common Pydantic schemas shared across API endpoints.

Provides pagination, sorting, filtering, and response envelope models.
"""

from typing import Any, Generic, TypeVar

from pydantic import BaseModel, Field

T = TypeVar("T")


class PaginationParams(BaseModel):
    """Query parameters for paginated list endpoints."""

    page: int = Field(default=1, ge=1, description="Page number (1-indexed)")
    page_size: int = Field(default=50, ge=1, le=500, description="Items per page")
    sort_by: str | None = Field(default=None, description="Column to sort by")
    sort_order: str = Field(default="desc", pattern="^(asc|desc)$")


class PaginationMeta(BaseModel):
    """Pagination metadata included in list responses."""

    page: int
    page_size: int
    total_items: int
    total_pages: int


class PaginatedResponse(BaseModel, Generic[T]):
    """Standard paginated response envelope."""

    data: list[T]
    meta: PaginationMeta


class SingleResponse(BaseModel, Generic[T]):
    """Standard single-item response envelope."""

    data: T


class ErrorDetail(BaseModel):
    """Structured error response body."""

    code: str
    message: str
    details: dict[str, Any] = Field(default_factory=dict)


class ErrorResponse(BaseModel):
    """Standard error response envelope."""

    error: ErrorDetail

"""Pydantic schemas for folder-related API request/response models."""

from pydantic import BaseModel, Field


class FolderResponse(BaseModel):
    """Response model for a folder record."""

    id: str
    scan_id: str
    path: str
    name: str
    parent_path: str | None = None
    depth: int = 0
    total_size_bytes: int = 0
    file_count: int = 0
    dir_count: int = 0
    discovered_at: str


class FolderFilter(BaseModel):
    """Query parameters for filtering folder listings."""

    scan_id: str | None = None
    min_size: int | None = Field(default=None, ge=0)
    parent_path: str | None = None
    max_depth: int | None = Field(default=None, ge=0)

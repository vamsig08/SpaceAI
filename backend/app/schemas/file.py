"""Pydantic schemas for file-related API request/response models."""

from pydantic import BaseModel, Field


class FileResponse(BaseModel):
    """Response model for a file record."""

    id: str
    scan_id: str
    path: str
    directory: str
    filename: str
    extension: str | None = None
    size_bytes: int
    mime_type: str | None = None
    category: str | None = None
    created_at: str | None = None
    modified_at: str | None = None
    accessed_at: str | None = None
    owner: str | None = None
    permissions: str | None = None
    sha256_hash: str | None = None
    is_duplicate: bool = False
    is_stale: bool = False
    stale_score: float | None = None
    risk_level: str | None = None
    discovered_at: str


class FileFilter(BaseModel):
    """Query parameters for filtering file listings."""

    scan_id: str | None = None
    category: str | None = Field(
        default=None,
        pattern="^(video|image|document|archive|code|audio|data|other)$",
    )
    min_size: int | None = Field(default=None, ge=0)
    max_size: int | None = Field(default=None, ge=0)
    extension: str | None = None
    is_stale: bool | None = None
    is_duplicate: bool | None = None
    directory: str | None = None

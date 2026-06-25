"""Pydantic schemas for scan-related API request/response models."""

from pydantic import BaseModel, Field


class ScanCreate(BaseModel):
    """Request body for creating a new filesystem scan."""

    root_path: str = Field(
        ..., min_length=1, description="Absolute path to scan"
    )
    scan_type: str = Field(
        default="full",
        pattern="^(full|incremental)$",
        description="Scan mode: full or incremental",
    )
    exclusions: list[str] = Field(
        default_factory=list,
        description="Additional exclusion patterns for this scan",
    )
    max_depth: int | None = Field(
        default=None, ge=1, description="Maximum directory depth (null = unlimited)"
    )


class ScanResponse(BaseModel):
    """Response model for a scan record."""

    id: str
    root_path: str
    status: str
    scan_type: str
    started_at: str | None = None
    completed_at: str | None = None
    total_files: int = 0
    total_dirs: int = 0
    total_size_bytes: int = 0
    files_per_second: float | None = None
    error_message: str | None = None
    platform: str | None = None
    created_at: str


class ScanProgress(BaseModel):
    """SSE event payload for scan progress updates."""

    files_scanned: int
    dirs_scanned: int
    current_directory: str
    total_bytes_scanned: int
    estimated_total_files: int | None = None
    eta_seconds: float | None = None
    files_per_second: float
    memory_usage_mb: float | None = None
    errors_skipped: int = 0


class ScanCompleted(BaseModel):
    """SSE event payload when scan finishes successfully."""

    scan_id: str
    total_files: int
    total_dirs: int
    total_bytes: int
    duration_seconds: float
    files_per_second: float
    errors_skipped: int = 0

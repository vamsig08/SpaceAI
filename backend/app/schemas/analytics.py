"""Pydantic schemas for analytics API request/response models."""

from typing import Any

from pydantic import BaseModel, Field


class StorageOverview(BaseModel):
    """Dashboard overview response — served from pre-computed snapshot."""

    total_storage: int = Field(description="Total disk capacity in bytes")
    used_storage: int = Field(description="Used disk space in bytes")
    free_storage: int = Field(description="Free disk space in bytes")
    file_count: int = Field(description="Total files discovered")
    dir_count: int = Field(description="Total directories discovered")
    duplicate_waste: int = Field(default=0, description="Bytes wasted by duplicates")
    stale_files_size: int = Field(default=0, description="Bytes in stale files")
    recovery_opportunities: int = Field(default=0, description="Total recoverable bytes")
    last_scan: str | None = Field(default=None, description="Last scan completion time")
    snapshot_date: str | None = Field(default=None, description="Date of snapshot data")


class CategoryBreakdown(BaseModel):
    """File category breakdown response."""

    breakdown: dict[str, int] = Field(description="Category name → total bytes")
    total_bytes: int
    file_count: int
    scan_id: str | None = None
    snapshot_date: str | None = None


class ExtensionInfo(BaseModel):
    """Single extension aggregation."""

    extension: str
    total_bytes: int
    file_count: int


class ExtensionBreakdown(BaseModel):
    """Top extensions response."""

    extensions: list[ExtensionInfo]
    scan_id: str | None = None


class LargestFileEntry(BaseModel):
    """Single file entry in largest-files response."""

    id: str
    path: str
    filename: str
    size_bytes: int
    category: str | None = None
    modified_at: str | None = None
    extension: str | None = None


class LargestFilesResponse(BaseModel):
    """Largest files response."""

    files: list[LargestFileEntry]
    scan_id: str | None = None
    total_count: int = 0


class LargestFolderEntry(BaseModel):
    """Single folder entry in largest-folders response."""

    id: str
    path: str
    name: str
    total_size_bytes: int
    file_count: int = 0
    depth: int = 0


class LargestFoldersResponse(BaseModel):
    """Largest folders response."""

    folders: list[LargestFolderEntry]
    scan_id: str | None = None
    total_count: int = 0


class GrowthDataPoint(BaseModel):
    """Single data point in growth history."""

    date: str
    total_size_bytes: int
    used_size_bytes: int = 0
    file_count: int = 0


class GrowthHistory(BaseModel):
    """Storage growth history response."""

    data_points: list[GrowthDataPoint]
    period_days: int
    data_point_count: int
    daily_growth_bytes: float = 0.0

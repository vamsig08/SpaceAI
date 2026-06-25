"""Pydantic schemas for duplicate detection API."""

from pydantic import BaseModel, Field


class DuplicateDetectRequest(BaseModel):
    """Request to start duplicate detection."""

    scan_id: str = Field(..., description="Scan ID to analyze for duplicates")


class DuplicateDetectResponse(BaseModel):
    """Response after starting duplicate detection task."""

    task_id: str
    scan_id: str
    status: str = "pending"


class DuplicateSummary(BaseModel):
    """Aggregate duplicate statistics."""

    total_groups: int
    total_duplicate_files: int
    total_wasted_bytes: int
    top_extensions: list[str] = Field(default_factory=list)


class DuplicateMemberResponse(BaseModel):
    """Single file within a duplicate group."""

    id: str
    file_id: str
    path: str
    is_keeper: bool = False


class DuplicateGroupResponse(BaseModel):
    """Duplicate group with metadata."""

    id: str
    sha256_hash: str
    file_size_bytes: int
    member_count: int
    wasted_bytes: int
    status: str
    created_at: str


class DuplicateGroupDetail(BaseModel):
    """Full duplicate group with member list."""

    id: str
    sha256_hash: str
    file_size_bytes: int
    member_count: int
    wasted_bytes: int
    status: str
    created_at: str
    members: list[DuplicateMemberResponse]


class DuplicateResolveRequest(BaseModel):
    """Request to mark a keeper in a duplicate group."""

    keep_file_id: str = Field(..., description="File ID to designate as keeper")


class DuplicateResolveResponse(BaseModel):
    """Response after resolving a duplicate group."""

    group_id: str
    keeper_file_id: str
    status: str
    files_to_cleanup: int
    recoverable_bytes: int

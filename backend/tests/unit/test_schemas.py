"""Tests for Pydantic schema validation — ensures models accept valid data
and reject invalid inputs correctly."""

import pytest
from pydantic import ValidationError

from app.schemas.scan import ScanCreate, ScanProgress, ScanResponse, ScanCompleted
from app.schemas.file import FileFilter, FileResponse
from app.schemas.folder import FolderFilter, FolderResponse


class TestScanCreate:
    """Tests for scan creation request validation."""

    def test_valid_request(self) -> None:
        req = ScanCreate(root_path="/home/user", scan_type="full")
        assert req.root_path == "/home/user"
        assert req.scan_type == "full"
        assert req.exclusions == []
        assert req.max_depth is None

    def test_rejects_empty_root_path(self) -> None:
        with pytest.raises(ValidationError):
            ScanCreate(root_path="", scan_type="full")

    def test_rejects_invalid_scan_type(self) -> None:
        with pytest.raises(ValidationError):
            ScanCreate(root_path="/home", scan_type="invalid")

    def test_accepts_incremental_type(self) -> None:
        req = ScanCreate(root_path="/x", scan_type="incremental")
        assert req.scan_type == "incremental"

    def test_accepts_exclusions(self) -> None:
        req = ScanCreate(root_path="/x", exclusions=["node_modules", ".git"])
        assert len(req.exclusions) == 2


class TestScanResponse:
    """Tests for scan response model."""

    def test_valid_response(self) -> None:
        resp = ScanResponse(
            id="uuid-1",
            root_path="/tmp",
            status="completed",
            scan_type="full",
            total_files=1000,
            created_at="2026-01-01T00:00:00Z",
        )
        assert resp.status == "completed"
        assert resp.total_files == 1000


class TestScanProgress:
    """Tests for scan progress SSE payload."""

    def test_valid_progress(self) -> None:
        p = ScanProgress(
            files_scanned=5000,
            dirs_scanned=200,
            current_directory="/tmp/scan",
            total_bytes_scanned=1000000000,
            files_per_second=556.0,
        )
        assert p.files_scanned == 5000
        assert p.eta_seconds is None


class TestScanCompleted:
    """Tests for scan completed payload."""

    def test_valid_completed(self) -> None:
        c = ScanCompleted(
            scan_id="uuid-1",
            total_files=1000000,
            total_dirs=50000,
            total_bytes=214748364800,
            duration_seconds=120.5,
            files_per_second=8298.8,
        )
        assert c.total_files == 1000000


class TestFileFilter:
    """Tests for file filter parameters."""

    def test_defaults_to_none(self) -> None:
        f = FileFilter()
        assert f.scan_id is None
        assert f.category is None

    def test_rejects_invalid_category(self) -> None:
        with pytest.raises(ValidationError):
            FileFilter(category="invalid_category")

    def test_accepts_valid_category(self) -> None:
        f = FileFilter(category="video")
        assert f.category == "video"


class TestFileResponse:
    """Tests for file response model."""

    def test_valid_response(self) -> None:
        r = FileResponse(
            id="uuid-1",
            scan_id="scan-1",
            path="/tmp/file.mp4",
            directory="/tmp",
            filename="file.mp4",
            size_bytes=5000000,
            discovered_at="2026-01-01T00:00:00Z",
        )
        assert r.is_duplicate is False
        assert r.is_stale is False


class TestFolderFilter:
    """Tests for folder filter parameters."""

    def test_defaults_to_none(self) -> None:
        f = FolderFilter()
        assert f.scan_id is None
        assert f.min_size is None

    def test_rejects_negative_min_size(self) -> None:
        with pytest.raises(ValidationError):
            FolderFilter(min_size=-1)


class TestFolderResponse:
    """Tests for folder response model."""

    def test_valid_response(self) -> None:
        r = FolderResponse(
            id="uuid-1",
            scan_id="scan-1",
            path="/tmp/folder",
            name="folder",
            total_size_bytes=5000000,
            discovered_at="2026-01-01T00:00:00Z",
        )
        assert r.depth == 0
        assert r.file_count == 0

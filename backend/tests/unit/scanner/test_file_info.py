"""Unit tests for file_info module (categorization and path normalization)."""

import os
import sys
import tempfile
from pathlib import Path

import pytest

from app.scanner.file_info import (
    DirInfo,
    FileInfo,
    _normalize_path,
    _timestamp_to_iso,
    categorize_extension,
)


class TestCategorizeExtension:
    """Tests for extension-to-category mapping."""

    def test_video_extensions(self) -> None:
        assert categorize_extension(".mp4") == "video"
        assert categorize_extension(".mkv") == "video"
        assert categorize_extension(".avi") == "video"

    def test_image_extensions(self) -> None:
        assert categorize_extension(".jpg") == "image"
        assert categorize_extension(".png") == "image"
        assert categorize_extension(".svg") == "image"

    def test_document_extensions(self) -> None:
        assert categorize_extension(".pdf") == "document"
        assert categorize_extension(".docx") == "document"
        assert categorize_extension(".md") == "document"

    def test_archive_extensions(self) -> None:
        assert categorize_extension(".zip") == "archive"
        assert categorize_extension(".tar") == "archive"
        assert categorize_extension(".7z") == "archive"

    def test_code_extensions(self) -> None:
        assert categorize_extension(".py") == "code"
        assert categorize_extension(".ts") == "code"
        assert categorize_extension(".rs") == "code"
        assert categorize_extension(".json") == "code"

    def test_audio_extensions(self) -> None:
        assert categorize_extension(".mp3") == "audio"
        assert categorize_extension(".flac") == "audio"

    def test_data_extensions(self) -> None:
        assert categorize_extension(".pt") == "data"
        assert categorize_extension(".onnx") == "data"
        assert categorize_extension(".parquet") == "data"

    def test_unknown_extension_returns_other(self) -> None:
        assert categorize_extension(".xyz123") == "other"
        assert categorize_extension(".unknownext") == "other"

    def test_none_returns_other(self) -> None:
        assert categorize_extension(None) == "other"

    def test_empty_string_returns_other(self) -> None:
        assert categorize_extension("") == "other"

    def test_case_insensitive(self) -> None:
        assert categorize_extension(".MP4") == "video"
        assert categorize_extension(".Py") == "code"


class TestTimestampToIso:
    """Tests for Unix timestamp conversion."""

    def test_converts_epoch_zero(self) -> None:
        result = _timestamp_to_iso(0)
        assert result.startswith("1970-01-01T00:00:00")

    def test_converts_known_timestamp(self) -> None:
        # 2026-01-01 00:00:00 UTC = 1767225600
        result = _timestamp_to_iso(1767225600)
        assert result.startswith("2026-01-01T00:00:00")

    def test_output_format_is_iso8601(self) -> None:
        result = _timestamp_to_iso(1000000000)
        assert "T" in result
        assert result.endswith("Z")


class TestNormalizePath:
    """Tests for path normalization."""

    def test_posix_path_unchanged_on_non_windows(self) -> None:
        if sys.platform == "win32":
            pytest.skip("POSIX-only test")
        assert _normalize_path("/home/user/file.txt") == "/home/user/file.txt"

    def test_unicode_nfc_normalization(self) -> None:
        # NFD form of "café"
        nfd = "cafe\u0301"
        result = _normalize_path(f"/tmp/{nfd}")
        assert "\u0301" not in result  # Combined to NFC
        assert "é" in result or "cafe" in result


class TestFileInfoFromDirEntry:
    """Tests for FileInfo.from_dir_entry with real filesystem."""

    def test_collects_metadata_from_real_file(self, tmp_path: Path) -> None:
        # Create a test file
        test_file = tmp_path / "test.py"
        test_file.write_text("print('hello')")

        # Use os.scandir to get a DirEntry
        entries = list(os.scandir(str(tmp_path)))
        file_entry = next(e for e in entries if e.name == "test.py")

        info = FileInfo.from_dir_entry(file_entry)

        assert info is not None
        assert info.filename == "test.py"
        assert info.extension == ".py"
        assert info.category == "code"
        assert info.size_bytes == len("print('hello')")
        assert info.modified_at is not None
        assert "test.py" in info.path

    def test_handles_file_without_extension(self, tmp_path: Path) -> None:
        test_file = tmp_path / "Makefile"
        test_file.write_text("all:\n\techo hi")

        entries = list(os.scandir(str(tmp_path)))
        file_entry = next(e for e in entries if e.name == "Makefile")

        info = FileInfo.from_dir_entry(file_entry)

        assert info is not None
        assert info.extension is None
        assert info.category == "other"

    def test_handles_empty_file(self, tmp_path: Path) -> None:
        test_file = tmp_path / "empty.txt"
        test_file.touch()

        entries = list(os.scandir(str(tmp_path)))
        file_entry = next(e for e in entries if e.name == "empty.txt")

        info = FileInfo.from_dir_entry(file_entry)

        assert info is not None
        assert info.size_bytes == 0
        assert info.extension == ".txt"

    def test_collects_owner_on_posix(self, tmp_path: Path) -> None:
        if sys.platform == "win32":
            pytest.skip("POSIX-only test")

        test_file = tmp_path / "owned.py"
        test_file.write_text("x = 1")

        entries = list(os.scandir(str(tmp_path)))
        file_entry = next(e for e in entries if e.name == "owned.py")

        info = FileInfo.from_dir_entry(file_entry)

        assert info is not None
        assert info.owner is not None
        assert info.permissions is not None
        assert len(info.permissions) == 3  # e.g. "644"


class TestDirInfo:
    """Tests for DirInfo dataclass."""

    def test_creates_dir_info(self) -> None:
        d = DirInfo(path="/tmp/test", name="test", parent_path="/tmp", depth=1)
        assert d.path == "/tmp/test"
        assert d.name == "test"
        assert d.parent_path == "/tmp"
        assert d.depth == 1

    def test_root_dir_has_no_parent(self) -> None:
        d = DirInfo(path="/", name="/", parent_path=None, depth=0)
        assert d.parent_path is None

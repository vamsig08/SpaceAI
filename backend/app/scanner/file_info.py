"""File metadata collection and categorization.

Collects filesystem metadata from os.DirEntry / os.stat results and
maps file extensions to content categories.
"""

from __future__ import annotations

import os
import sys
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

# Extension-to-category mapping for fast classification
_CATEGORY_MAP: dict[str, str] = {}

_VIDEO_EXTENSIONS = {
    ".mp4", ".avi", ".mkv", ".mov", ".wmv", ".flv", ".webm", ".m4v",
    ".mpg", ".mpeg", ".3gp", ".ts",
}
_IMAGE_EXTENSIONS = {
    ".jpg", ".jpeg", ".png", ".gif", ".bmp", ".svg", ".webp", ".tiff",
    ".tif", ".ico", ".heic", ".heif", ".raw", ".cr2", ".nef",
}
_DOCUMENT_EXTENSIONS = {
    ".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx", ".odt",
    ".ods", ".odp", ".txt", ".rtf", ".md", ".tex", ".epub", ".csv",
}
_ARCHIVE_EXTENSIONS = {
    ".zip", ".tar", ".gz", ".bz2", ".xz", ".7z", ".rar", ".iso",
    ".dmg", ".pkg", ".deb", ".rpm", ".tgz",
}
_CODE_EXTENSIONS = {
    ".py", ".js", ".ts", ".jsx", ".tsx", ".java", ".c", ".cpp", ".h",
    ".hpp", ".cs", ".go", ".rs", ".rb", ".php", ".swift", ".kt",
    ".scala", ".r", ".m", ".sh", ".bash", ".zsh", ".ps1", ".bat",
    ".sql", ".html", ".css", ".scss", ".less", ".vue", ".svelte",
    ".json", ".yaml", ".yml", ".toml", ".xml", ".ini", ".cfg",
    ".dockerfile", ".makefile",
}
_AUDIO_EXTENSIONS = {
    ".mp3", ".wav", ".flac", ".aac", ".ogg", ".wma", ".m4a", ".opus",
    ".aiff", ".alac",
}
_DATA_EXTENSIONS = {
    ".db", ".sqlite", ".sqlite3", ".mdb", ".accdb", ".parquet",
    ".feather", ".arrow", ".hdf5", ".h5", ".npy", ".npz", ".pkl",
    ".pickle", ".pt", ".pth", ".ckpt", ".onnx", ".safetensors",
}

for ext in _VIDEO_EXTENSIONS:
    _CATEGORY_MAP[ext] = "video"
for ext in _IMAGE_EXTENSIONS:
    _CATEGORY_MAP[ext] = "image"
for ext in _DOCUMENT_EXTENSIONS:
    _CATEGORY_MAP[ext] = "document"
for ext in _ARCHIVE_EXTENSIONS:
    _CATEGORY_MAP[ext] = "archive"
for ext in _CODE_EXTENSIONS:
    _CATEGORY_MAP[ext] = "code"
for ext in _AUDIO_EXTENSIONS:
    _CATEGORY_MAP[ext] = "audio"
for ext in _DATA_EXTENSIONS:
    _CATEGORY_MAP[ext] = "data"


def categorize_extension(extension: str | None) -> str:
    """Map a file extension to a content category.

    Args:
        extension: Lowercase extension with dot (e.g., ".py") or None.

    Returns:
        Category string: video|image|document|archive|code|audio|data|other
    """
    if not extension:
        return "other"
    return _CATEGORY_MAP.get(extension.lower(), "other")


def _timestamp_to_iso(ts: float) -> str:
    """Convert a Unix timestamp to ISO8601 string."""
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _normalize_path(path_str: str) -> str:
    """Normalize a filesystem path for consistent storage.

    - Converts backslashes to forward slashes (Windows compatibility)
    - Applies NFC Unicode normalization (macOS NFD handling)
    """
    # Normalize Unicode (macOS uses NFD, we store NFC)
    normalized = unicodedata.normalize("NFC", path_str)
    # Normalize path separators for cross-platform DB consistency
    if sys.platform == "win32":
        normalized = normalized.replace("\\", "/")
    return normalized


@dataclass(slots=True)
class FileInfo:
    """Collected metadata for a single file, ready for database insertion.

    Uses __slots__ via dataclass(slots=True) for memory efficiency when
    buffering thousands of instances in the batch writer.
    """

    path: str
    directory: str
    filename: str
    extension: str | None
    size_bytes: int
    category: str
    created_at: str | None
    modified_at: str | None
    accessed_at: str | None
    owner: str | None
    permissions: str | None

    @classmethod
    def from_dir_entry(cls, entry: os.DirEntry[str]) -> FileInfo | None:
        """Construct FileInfo from an os.DirEntry with cached stat.

        Uses entry.stat(follow_symlinks=True) which leverages the cached
        stat result from os.scandir() — no additional syscall on most platforms.

        Args:
            entry: A DirEntry object from os.scandir().

        Returns:
            FileInfo instance or None if the entry cannot be processed.
        """
        try:
            stat_result = entry.stat(follow_symlinks=True)
        except (OSError, ValueError):
            return None

        path_str = _normalize_path(entry.path)
        path_obj = Path(path_str)

        extension = path_obj.suffix.lower() if path_obj.suffix else None
        category = categorize_extension(extension)

        # Timestamps
        created_at: str | None = None
        modified_at: str | None = None
        accessed_at: str | None = None

        if stat_result.st_mtime:
            modified_at = _timestamp_to_iso(stat_result.st_mtime)
        if stat_result.st_atime:
            accessed_at = _timestamp_to_iso(stat_result.st_atime)
        # st_birthtime on macOS, st_ctime on Linux (inode change, not creation)
        if hasattr(stat_result, "st_birthtime"):
            created_at = _timestamp_to_iso(stat_result.st_birthtime)
        elif stat_result.st_ctime:
            created_at = _timestamp_to_iso(stat_result.st_ctime)

        # Owner and permissions (POSIX only)
        owner: str | None = None
        permissions: str | None = None

        if sys.platform != "win32":
            try:
                import pwd

                pw_entry = pwd.getpwuid(stat_result.st_uid)
                owner = pw_entry.pw_name
            except (KeyError, ImportError):
                owner = str(stat_result.st_uid)

            permissions = oct(stat_result.st_mode)[-3:]

        return cls(
            path=path_str,
            directory=_normalize_path(str(path_obj.parent)),
            filename=path_obj.name,
            extension=extension,
            size_bytes=stat_result.st_size,
            category=category,
            created_at=created_at,
            modified_at=modified_at,
            accessed_at=accessed_at,
            owner=owner,
            permissions=permissions,
        )


@dataclass(slots=True)
class DirInfo:
    """Collected metadata for a directory during traversal."""

    path: str
    name: str
    parent_path: str | None
    depth: int

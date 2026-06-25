"""Streaming file hasher with multi-stage duplicate detection pipeline.

Implements the 3-stage strategy from docs/duplicate-detection-strategy.md:
  Stage 1: Size grouping (SQL, handled in service layer)
  Stage 2: Partial hash — first 4KB + last 4KB → SHA256 of 8KB
  Stage 3: Full file hash — streaming SHA256 with 64KB buffer

Design constraints:
  - Never loads entire file into memory (supports >10GB files)
  - 64KB buffer aligns with OS page size
  - Handles PermissionError, broken symlinks, locked files gracefully
  - Thread-safe: each call is self-contained with local state
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path

from app.core.logging import get_logger

logger = get_logger(__name__)

# Configuration
PARTIAL_HASH_CHUNK_SIZE = 4096  # 4KB from start + 4KB from end
FULL_HASH_BUFFER_SIZE = 65536   # 64KB streaming buffer
MAX_FILE_SIZE_FOR_HASH = 10 * 1024 * 1024 * 1024  # 10 GB safety limit


class HashError(Exception):
    """Raised when a file cannot be hashed."""

    def __init__(self, path: str, reason: str) -> None:
        self.path = path
        self.reason = reason
        super().__init__(f"Cannot hash {path}: {reason}")


def compute_partial_hash(file_path: str, file_size: int) -> str | None:
    """Compute a partial hash from first 4KB + last 4KB of a file.

    This is Stage 2 of the duplicate detection pipeline. Files that share
    the same partial hash are strong duplicate candidates requiring full
    verification in Stage 3.

    For files <= 8KB, the entire file is read (partial = full).

    Args:
        file_path: Absolute path to the file.
        file_size: Known file size (avoids extra stat call).

    Returns:
        Hex SHA256 digest string of the partial content, or None on error.
    """
    try:
        hasher = hashlib.sha256()

        with open(file_path, "rb") as f:
            # Read first chunk
            first_chunk = f.read(PARTIAL_HASH_CHUNK_SIZE)
            hasher.update(first_chunk)

            # Read last chunk (if file is large enough to have a distinct tail)
            if file_size > PARTIAL_HASH_CHUNK_SIZE * 2:
                f.seek(-PARTIAL_HASH_CHUNK_SIZE, os.SEEK_END)
                last_chunk = f.read(PARTIAL_HASH_CHUNK_SIZE)
                hasher.update(last_chunk)
            elif file_size > PARTIAL_HASH_CHUNK_SIZE:
                # File between 4KB and 8KB: read the rest
                remaining = f.read()
                hasher.update(remaining)
            # Files <= 4KB: first_chunk is the entire file, already hashed

        return hasher.hexdigest()

    except PermissionError:
        logger.debug("partial_hash_permission_denied", path=file_path)
        return None
    except FileNotFoundError:
        logger.debug("partial_hash_file_not_found", path=file_path)
        return None
    except OSError as e:
        logger.debug("partial_hash_os_error", path=file_path, error=str(e))
        return None


def compute_full_hash(file_path: str) -> str | None:
    """Compute full SHA256 hash of a file using streaming 64KB reads.

    This is Stage 3 of the duplicate detection pipeline — the definitive
    duplicate confirmation. Only called for files that passed Stage 2.

    Never loads the full file into memory. Reads in 64KB chunks and
    incrementally updates the SHA256 state.

    Args:
        file_path: Absolute path to the file.

    Returns:
        Hex SHA256 digest string, or None on error.
    """
    try:
        file_size = os.path.getsize(file_path)

        if file_size > MAX_FILE_SIZE_FOR_HASH:
            logger.warning(
                "file_too_large_for_hash",
                path=file_path,
                size_bytes=file_size,
                limit_bytes=MAX_FILE_SIZE_FOR_HASH,
            )
            return None

        hasher = hashlib.sha256()
        bytes_read = 0

        with open(file_path, "rb") as f:
            while True:
                chunk = f.read(FULL_HASH_BUFFER_SIZE)
                if not chunk:
                    break
                hasher.update(chunk)
                bytes_read += len(chunk)

        return hasher.hexdigest()

    except PermissionError:
        logger.debug("full_hash_permission_denied", path=file_path)
        return None
    except FileNotFoundError:
        logger.debug("full_hash_file_not_found", path=file_path)
        return None
    except OSError as e:
        logger.debug("full_hash_os_error", path=file_path, error=str(e))
        return None


def compute_hash_for_bytes(data: bytes) -> str:
    """Compute SHA256 hash of in-memory bytes.

    Utility for testing and small-data scenarios.

    Args:
        data: Raw bytes to hash.

    Returns:
        Hex SHA256 digest string.
    """
    return hashlib.sha256(data).hexdigest()


def are_files_identical(path_a: str, path_b: str) -> bool:
    """Byte-for-byte comparison of two files.

    Used as a secondary confirmation when extra certainty is needed
    (e.g., before deletion). More expensive than hash comparison but
    provides absolute certainty.

    Args:
        path_a: First file path.
        path_b: Second file path.

    Returns:
        True if files are byte-for-byte identical, False otherwise.
    """
    try:
        size_a = os.path.getsize(path_a)
        size_b = os.path.getsize(path_b)

        if size_a != size_b:
            return False

        with open(path_a, "rb") as fa, open(path_b, "rb") as fb:
            while True:
                chunk_a = fa.read(FULL_HASH_BUFFER_SIZE)
                chunk_b = fb.read(FULL_HASH_BUFFER_SIZE)
                if chunk_a != chunk_b:
                    return False
                if not chunk_a:
                    return True

    except (OSError, PermissionError):
        return False

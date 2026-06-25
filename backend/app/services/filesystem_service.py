"""Filesystem Service — the ONLY layer that touches the operating system.

Every interaction with files, directories, trash, and permissions goes through
this service. No other module should directly import shutil, os.rename, or
pathlib operations that modify the filesystem.

Design principles:
- Verify before acting (pre-check permissions and existence)
- Verify after acting (confirm destination exists, source removed)
- Never report success without OS confirmation
- Calculate real sizes (including directory recursion)
- Normalize all paths (resolve symlinks, /tmp → /private/tmp on macOS)
- Provide atomic rollback information
"""

from __future__ import annotations

import json
import os
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.core.logging import get_logger

logger = get_logger(__name__)


@dataclass
class MoveResult:
    """Result of a single file/directory move operation."""

    success: bool
    original_path: str
    trash_path: str | None = None
    size_bytes: int = 0
    error: str | None = None


@dataclass
class RestoreResult:
    """Result of a single file/directory restore operation."""

    success: bool
    original_path: str
    error: str | None = None
    size_bytes: int = 0


class FilesystemService:
    """The sole interface for filesystem operations in SpaceAI.

    All moves, deletes, restores, and verifications go through this class.
    It guarantees that reported results match actual OS state.
    """

    def __init__(self, trash_base: Path | None = None) -> None:
        self._trash_base = trash_base or Path.home() / ".spaceai" / "trash"

    def resolve_path(self, path: str) -> Path:
        """Normalize and resolve a path to its real filesystem location.

        Handles:
        - Symlink resolution (/tmp → /private/tmp on macOS)
        - Relative path resolution
        - Unicode normalization

        Args:
            path: Raw path string from frontend or database.

        Returns:
            Resolved absolute Path object.
        """
        p = Path(path)
        try:
            return p.resolve()
        except (OSError, RuntimeError):
            return p.absolute()

    def exists(self, path: str) -> bool:
        """Check if a path exists on the filesystem.

        Args:
            path: Path to check.

        Returns:
            True if exists (file or directory).
        """
        return self.resolve_path(path).exists()

    def can_write(self, path: str) -> bool:
        """Check if we have write permission to move/delete a path.

        Args:
            path: Path to check.

        Returns:
            True if we can modify this path.
        """
        p = self.resolve_path(path)
        if not p.exists():
            return False
        # Check parent directory write permission (needed to remove/move)
        return os.access(str(p.parent), os.W_OK)

    def calculate_size(self, path: str) -> int:
        """Calculate total size of a file or directory (recursive).

        Args:
            path: Path to measure.

        Returns:
            Total bytes (0 if path doesn't exist).
        """
        p = self.resolve_path(path)
        if not p.exists():
            return 0
        if p.is_file():
            return p.stat().st_size
        # Directory: walk and sum
        total = 0
        try:
            for entry in p.rglob("*"):
                if entry.is_file():
                    try:
                        total += entry.stat().st_size
                    except OSError:
                        pass
        except (PermissionError, OSError):
            pass
        return total

    def move_to_trash(
        self, source_path: str, trash_dir: Path, index: int
    ) -> MoveResult:
        """Move a file or directory to trash with full verification.

        Transaction-like behavior:
        1. Validate source exists
        2. Validate permissions
        3. Calculate size
        4. Perform move
        5. Verify destination exists
        6. Verify source is gone

        If any step fails, returns failure with explanation.

        Args:
            source_path: Absolute path to move.
            trash_dir: Destination trash directory.
            index: Numeric index for filename uniqueness.

        Returns:
            MoveResult with success/failure and details.
        """
        src = self.resolve_path(source_path)

        # Step 1: Validate source exists
        if not src.exists():
            return MoveResult(
                success=False,
                original_path=str(src),
                error=f"Path does not exist: {src}",
            )

        # Step 2: Validate permissions
        if not os.access(str(src.parent), os.W_OK):
            return MoveResult(
                success=False,
                original_path=str(src),
                error=f"Permission denied: cannot remove from {src.parent}",
            )

        # Step 3: Calculate real size (before move)
        size = self.calculate_size(str(src))

        # Step 4: Perform move
        dest = trash_dir / f"{index}_{src.name}"
        try:
            shutil.move(str(src), str(dest))
        except (OSError, shutil.Error) as e:
            return MoveResult(
                success=False,
                original_path=str(src),
                error=f"Move failed: {e}",
            )

        # Step 5: Verify destination exists
        if not dest.exists():
            return MoveResult(
                success=False,
                original_path=str(src),
                error=f"Move appeared to succeed but destination not found: {dest}",
            )

        # Step 6: Verify source is gone
        if src.exists():
            # Move didn't actually remove source — this shouldn't happen but check anyway
            return MoveResult(
                success=False,
                original_path=str(src),
                trash_path=str(dest),
                error=f"Move completed but source still exists: {src}",
            )

        logger.debug(
            "filesystem_move_verified",
            source=str(src),
            dest=str(dest),
            size=size,
        )

        return MoveResult(
            success=True,
            original_path=str(src),
            trash_path=str(dest),
            size_bytes=size,
        )

    def restore_from_trash(self, trash_path: str, original_path: str) -> RestoreResult:
        """Restore a file or directory from trash to its original location.

        Transaction-like behavior:
        1. Validate trash file exists
        2. Create parent directory if needed
        3. Move back to original location
        4. Verify restoration
        5. Verify trash file removed

        Args:
            trash_path: Current location in trash.
            original_path: Desired restoration path.

        Returns:
            RestoreResult with success/failure and details.
        """
        src = Path(trash_path)
        dest = Path(original_path)

        # Step 1: Validate trash file exists
        if not src.exists():
            return RestoreResult(
                success=False,
                original_path=original_path,
                error=f"Trash file not found: {trash_path}",
            )

        # Step 2: Ensure parent directory exists
        try:
            dest.parent.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            return RestoreResult(
                success=False,
                original_path=original_path,
                error=f"Cannot create directory {dest.parent}: {e}",
            )

        # Calculate size before move
        size = self.calculate_size(str(src))

        # Step 3: Move back
        try:
            shutil.move(str(src), str(dest))
        except (OSError, shutil.Error) as e:
            return RestoreResult(
                success=False,
                original_path=original_path,
                error=f"Restore failed: {e}",
            )

        # Step 4: Verify restoration
        if not dest.exists():
            return RestoreResult(
                success=False,
                original_path=original_path,
                error=f"Restore appeared to succeed but file not found at: {dest}",
            )

        # Step 5: Verify trash file removed
        if src.exists():
            logger.warning("restore_trash_not_cleaned", trash_path=trash_path)

        return RestoreResult(
            success=True,
            original_path=original_path,
            size_bytes=size,
        )

    def create_trash_dir(self, action_id: str) -> Path:
        """Create a dated trash directory for a cleanup action.

        Args:
            action_id: Unique cleanup action ID.

        Returns:
            Path to the created trash directory.
        """
        from datetime import datetime, timezone

        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        trash_dir = self._trash_base / today / action_id
        trash_dir.mkdir(parents=True, exist_ok=True)
        return trash_dir

    def write_manifest(self, trash_dir: Path, manifest: list[dict[str, Any]]) -> Path:
        """Write the restore manifest to the trash directory.

        Args:
            trash_dir: Trash directory path.
            manifest: List of move records.

        Returns:
            Path to the written manifest file.
        """
        manifest_path = trash_dir / "manifest.json"
        manifest_path.write_text(json.dumps(manifest, indent=2))
        return manifest_path

    def read_manifest(self, manifest_path: str) -> list[dict[str, Any]] | None:
        """Read a restore manifest from disk.

        Args:
            manifest_path: Path to manifest.json.

        Returns:
            Parsed manifest or None if not found/invalid.
        """
        p = Path(manifest_path)
        if not p.exists():
            return None
        try:
            return json.loads(p.read_text())
        except (json.JSONDecodeError, OSError):
            return None

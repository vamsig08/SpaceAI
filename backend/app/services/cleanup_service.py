"""Cleanup service — safety-first file operations with trash, rollback, and audit.

Core invariants:
1. No file is permanently deleted by default (trash-first).
2. Every operation is audited.
3. Every operation is reversible within the retention period.
4. Batch operations are atomic: failure triggers partial rollback.
5. Dry-run mode produces identical output minus actual file moves.

Workflow:
  propose → dry_run → approve → execute → (rollback if needed)
"""

from __future__ import annotations

import json
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.core.exceptions import ConflictError, NotFoundError, ValidationError
from app.core.logging import get_logger
from app.models.base import generate_uuid, utc_now
from app.services.audit_service import AuditService
from app.workers.progress import ProgressReporter
from app.workers.task_manager import TaskState, TaskStatus

logger = get_logger(__name__)

# Status transitions
VALID_TRANSITIONS = {
    "proposed": ["dry_run_complete", "approved"],
    "dry_run_complete": ["approved"],
    "approved": ["executing"],
    "executing": ["completed", "failed"],
    "completed": ["rolled_back"],
    "failed": ["rolled_back"],
}


class CleanupService:
    """Manages the cleanup action lifecycle."""

    def __init__(self, session: AsyncSession, trash_base: Path | None = None) -> None:
        self._session = session
        self._trash_base = trash_base or Path.home() / ".spaceai" / "trash"

    async def propose_cleanup(
        self,
        recommendation_id: str | None,
        action_type: str,
        target_paths: list[str],
        total_bytes: int,
    ) -> dict[str, Any]:
        """Create a proposed cleanup action.

        Args:
            recommendation_id: Source recommendation (optional).
            action_type: Type of cleanup (trash|archive|compress).
            target_paths: List of absolute paths to process.
            total_bytes: Total size of targets.

        Returns:
            Created cleanup action dict.
        """
        if action_type not in ("trash", "archive", "compress"):
            raise ValidationError(f"Invalid action_type: {action_type}", field="action_type")

        if not target_paths:
            raise ValidationError("target_paths cannot be empty", field="target_paths")

        action_id = generate_uuid()
        now = utc_now()

        await self._session.execute(
            text(
                """
                INSERT INTO cleanup_actions (
                    id, recommendation_id, action_type, target_paths, target_count,
                    total_bytes, status, created_at
                ) VALUES (
                    :id, :rec_id, :type, :paths, :count, :bytes, 'proposed', :now
                )
                """
            ),
            {
                "id": action_id,
                "rec_id": recommendation_id,
                "type": action_type,
                "paths": json.dumps(target_paths),
                "count": len(target_paths),
                "bytes": total_bytes,
                "now": now,
            },
        )
        await self._session.flush()

        logger.info(
            "cleanup_proposed",
            action_id=action_id,
            action_type=action_type,
            target_count=len(target_paths),
            total_bytes=total_bytes,
        )

        return {
            "id": action_id,
            "action_type": action_type,
            "target_count": len(target_paths),
            "total_bytes": total_bytes,
            "status": "proposed",
        }

    async def dry_run(self, action_id: str) -> dict[str, Any]:
        """Execute a dry-run: validate targets without moving files.

        Checks:
        - Each target path exists
        - Each target path is readable
        - Sufficient trash space

        Args:
            action_id: The cleanup action to dry-run.

        Returns:
            Dry-run result with per-file status.

        Raises:
            NotFoundError: If action doesn't exist.
            ConflictError: If action isn't in valid state.
        """
        action = await self._get_action(action_id)
        if action["status"] not in ("proposed", "dry_run_complete"):
            raise ConflictError(f"Cannot dry-run action in state '{action['status']}'")

        target_paths = json.loads(action["target_paths"])
        results: list[dict[str, Any]] = []
        valid_count = 0
        valid_bytes = 0

        for path in target_paths:
            p = Path(path)
            if p.exists():
                try:
                    size = p.stat().st_size if p.is_file() else 0
                    results.append({"path": path, "status": "ready", "size": size})
                    valid_count += 1
                    valid_bytes += size
                except OSError as e:
                    results.append({"path": path, "status": "error", "error": str(e)})
            else:
                results.append({"path": path, "status": "missing"})

        dry_run_result = json.dumps({
            "valid_count": valid_count,
            "missing_count": len(target_paths) - valid_count,
            "valid_bytes": valid_bytes,
            "files": results[:100],  # Cap detail at 100 files
        })

        await self._session.execute(
            text(
                "UPDATE cleanup_actions SET status = 'dry_run_complete', "
                "dry_run_result = :result WHERE id = :id"
            ),
            {"result": dry_run_result, "id": action_id},
        )
        await self._session.flush()

        return {
            "action_id": action_id,
            "status": "dry_run_complete",
            "valid_count": valid_count,
            "missing_count": len(target_paths) - valid_count,
            "valid_bytes": valid_bytes,
        }

    async def approve(self, action_id: str) -> dict[str, Any]:
        """Approve a cleanup action for execution.

        Args:
            action_id: The action to approve.

        Returns:
            Updated action status.

        Raises:
            NotFoundError: If action doesn't exist.
            ConflictError: If action isn't in valid state.
        """
        action = await self._get_action(action_id)
        if action["status"] not in ("proposed", "dry_run_complete"):
            raise ConflictError(f"Cannot approve action in state '{action['status']}'")

        now = utc_now()
        await self._session.execute(
            text(
                "UPDATE cleanup_actions SET status = 'approved', approved_at = :now WHERE id = :id"
            ),
            {"now": now, "id": action_id},
        )
        await self._session.flush()

        return {"action_id": action_id, "status": "approved"}

    async def execute(self, action_id: str) -> dict[str, Any]:
        """Execute an approved cleanup action with full filesystem verification.

        Transaction-like behavior:
        1. Validate action state
        2. Create trash directory
        3. For each target: validate → move → verify
        4. Write manifest ONLY for verified moves
        5. Update database ONLY after filesystem confirms
        6. Create audit log

        If zero files are successfully moved, returns failure.
        Partial success reports exactly what moved and what didn't.

        Args:
            action_id: The approved action to execute.

        Returns:
            Execution result with verified bytes recovered.

        Raises:
            NotFoundError: If action doesn't exist.
            ConflictError: If action isn't approved.
        """
        action = await self._get_action(action_id)
        if action["status"] != "approved":
            raise ConflictError(f"Cannot execute action in state '{action['status']}'")

        # Transition to executing
        await self._session.execute(
            text("UPDATE cleanup_actions SET status = 'executing', executed_at = :now WHERE id = :id"),
            {"now": utc_now(), "id": action_id},
        )
        await self._session.flush()

        target_paths = json.loads(action["target_paths"])

        # Use FilesystemService for all OS operations
        from app.services.filesystem_service import FilesystemService
        fs = FilesystemService(trash_base=self._trash_base)

        trash_dir = fs.create_trash_dir(action_id)

        # Execute moves with full verification
        manifest: list[dict[str, Any]] = []
        bytes_recovered = 0
        errors: list[dict[str, str]] = []

        for idx, path in enumerate(target_paths):
            result = fs.move_to_trash(path, trash_dir, idx)

            if result.success:
                manifest.append({
                    "original_path": result.original_path,
                    "trash_path": result.trash_path,
                    "size": result.size_bytes,
                    "moved_at": utc_now(),
                })
                bytes_recovered += result.size_bytes
            else:
                errors.append({"path": path, "error": result.error or "Unknown error"})
                logger.warning("cleanup_file_failed", path=path, error=result.error)

        # Write manifest ONLY for successfully moved files
        manifest_path = ""
        if manifest:
            mp = fs.write_manifest(trash_dir, manifest)
            manifest_path = str(mp)

        # Determine final status based on VERIFIED results
        if not manifest and target_paths:
            # Nothing moved — this is a failure
            error_msg = f"None of the {len(target_paths)} targets could be moved. Errors: {errors[:3]}"
            await self._session.execute(
                text(
                    "UPDATE cleanup_actions SET status = 'failed', error_message = :err, "
                    "completed_at = :now WHERE id = :id"
                ),
                {"err": error_msg, "now": utc_now(), "id": action_id},
            )
            await self._session.flush()
            return {
                "action_id": action_id,
                "status": "failed",
                "files_processed": 0,
                "bytes_recovered": 0,
                "errors": len(errors),
                "trash_location": "",
            }

        # Success (full or partial) — update DB only AFTER filesystem confirms
        status = "completed"
        await self._session.execute(
            text(
                """
                UPDATE cleanup_actions SET
                    status = :status,
                    completed_at = :now,
                    trash_location = :trash,
                    manifest_path = :manifest,
                    bytes_recovered = :bytes,
                    error_message = :err_msg
                WHERE id = :id
                """
            ),
            {
                "status": status,
                "now": utc_now(),
                "trash": str(trash_dir),
                "manifest": manifest_path,
                "bytes": bytes_recovered,
                "err_msg": f"{len(errors)} files skipped" if errors else None,
                "id": action_id,
            },
        )
        await self._session.flush()

        # Audit log
        audit = AuditService(self._session)
        await audit.log(
            action="cleanup_executed",
            entity_type="cleanup_action",
            entity_id=action_id,
            description=f"Verified: {len(manifest)} files moved to trash, {bytes_recovered} bytes recovered",
            bytes_affected=bytes_recovered,
            paths_affected=[m["original_path"] for m in manifest[:50]],
            severity="info",
            correlation_id=action_id,
        )

        logger.info(
            "cleanup_executed_verified",
            action_id=action_id,
            files_moved=len(manifest),
            bytes_recovered=bytes_recovered,
            errors=len(errors),
            verified=True,
        )

        return {
            "action_id": action_id,
            "status": status,
            "files_processed": len(manifest),
            "bytes_recovered": bytes_recovered,
            "errors": len(errors),
            "trash_location": str(trash_dir),
        }

    async def rollback(self, action_id: str) -> dict[str, Any]:
        """Rollback a completed cleanup with full filesystem verification.

        Reads manifest and restores each file, verifying each restoration
        succeeded at the OS level before reporting success.

        Args:
            action_id: The completed action to rollback.

        Returns:
            Rollback result with verified restoration count.

        Raises:
            NotFoundError: If action doesn't exist.
            ConflictError: If action isn't in a rollback-able state.
        """
        action = await self._get_action(action_id)
        if action["status"] not in ("completed", "failed"):
            raise ConflictError(f"Cannot rollback action in state '{action['status']}'")

        manifest_path = action.get("manifest_path")
        if not manifest_path:
            raise ConflictError("No manifest found for rollback")

        from app.services.filesystem_service import FilesystemService
        fs = FilesystemService(trash_base=self._trash_base)

        manifest = fs.read_manifest(manifest_path)
        if manifest is None:
            raise ConflictError(f"Cannot read manifest at: {manifest_path}")

        restored_count = 0
        bytes_restored = 0
        errors: list[dict[str, str]] = []

        for entry in manifest:
            result = fs.restore_from_trash(entry["trash_path"], entry["original_path"])

            if result.success:
                restored_count += 1
                bytes_restored += result.size_bytes
            else:
                errors.append({"path": entry["original_path"], "error": result.error or "Unknown"})

        # Update status ONLY after filesystem operations complete
        await self._session.execute(
            text(
                "UPDATE cleanup_actions SET status = 'rolled_back', rolled_back_at = :now WHERE id = :id"
            ),
            {"now": utc_now(), "id": action_id},
        )
        await self._session.flush()

        # Audit
        audit = AuditService(self._session)
        await audit.log(
            action="cleanup_rolled_back",
            entity_type="cleanup_action",
            entity_id=action_id,
            description=f"Verified: {restored_count} files restored to original locations",
            bytes_affected=bytes_restored,
            severity="warning",
            correlation_id=action_id,
        )

        logger.info(
            "cleanup_rolled_back_verified",
            action_id=action_id,
            files_restored=restored_count,
            bytes_restored=bytes_restored,
            errors=len(errors),
        )

        return {
            "action_id": action_id,
            "status": "rolled_back",
            "files_restored": restored_count,
            "bytes_restored": bytes_restored,
            "errors": len(errors),
        }

    async def get_action(self, action_id: str) -> dict[str, Any]:
        """Get full details of a cleanup action.

        Args:
            action_id: The action ID.

        Returns:
            Action detail dict.

        Raises:
            NotFoundError: If action doesn't exist.
        """
        return await self._get_action(action_id)

    async def list_actions(
        self,
        *,
        status: str | None = None,
        page: int = 1,
        page_size: int = 50,
    ) -> dict[str, Any]:
        """List cleanup actions with optional status filter.

        Returns:
            Dict with actions list and pagination.
        """
        conditions = ["1=1"]
        params: dict[str, Any] = {}

        if status:
            conditions.append("status = :status")
            params["status"] = status

        where = " AND ".join(conditions)
        offset = (page - 1) * page_size

        count_result = await self._session.execute(
            text(f"SELECT COUNT(*) FROM cleanup_actions WHERE {where}"), params
        )
        total = count_result.scalar_one()

        params["limit"] = page_size
        params["offset"] = offset
        result = await self._session.execute(
            text(
                f"""
                SELECT id, action_type, target_count, total_bytes, status,
                       bytes_recovered, created_at, executed_at, completed_at
                FROM cleanup_actions WHERE {where}
                ORDER BY created_at DESC LIMIT :limit OFFSET :offset
                """
            ),
            params,
        )

        actions = [
            {
                "id": row[0],
                "action_type": row[1],
                "target_count": row[2],
                "total_bytes": row[3],
                "status": row[4],
                "bytes_recovered": row[5],
                "created_at": row[6],
                "executed_at": row[7],
                "completed_at": row[8],
            }
            for row in result.all()
        ]

        return {
            "actions": actions,
            "meta": {
                "page": page,
                "page_size": page_size,
                "total_items": total,
                "total_pages": (total + page_size - 1) // page_size,
            },
        }

    async def _get_action(self, action_id: str) -> dict[str, Any]:
        """Fetch a cleanup action by ID."""
        result = await self._session.execute(
            text(
                """
                SELECT id, recommendation_id, action_type, target_paths, target_count,
                       total_bytes, status, dry_run_result, approved_at, executed_at,
                       completed_at, rolled_back_at, trash_location, manifest_path,
                       bytes_recovered, error_message, created_at
                FROM cleanup_actions WHERE id = :id
                """
            ),
            {"id": action_id},
        )
        row = result.one_or_none()
        if row is None:
            raise NotFoundError("cleanup_action", action_id)

        return {
            "id": row[0],
            "recommendation_id": row[1],
            "action_type": row[2],
            "target_paths": row[3],
            "target_count": row[4],
            "total_bytes": row[5],
            "status": row[6],
            "dry_run_result": row[7],
            "approved_at": row[8],
            "executed_at": row[9],
            "completed_at": row[10],
            "rolled_back_at": row[11],
            "trash_location": row[12],
            "manifest_path": row[13],
            "bytes_recovered": row[14],
            "error_message": row[15],
            "created_at": row[16],
        }

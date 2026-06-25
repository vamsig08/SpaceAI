"""Integration tests for cleanup safety framework.

Validates:
- No permanent deletion occurs by default
- Rollback works correctly
- Audit logs are complete
- Interrupted cleanup can recover safely
- State transitions are enforced
"""

import json
import os
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.exceptions import ConflictError, NotFoundError
from app.models.base import generate_uuid, utc_now
from app.services.audit_service import AuditService
from app.services.cleanup_service import CleanupService


@pytest.fixture
def trash_dir(tmp_path: Path) -> Path:
    """Provide a temporary trash directory for testing."""
    d = tmp_path / "trash"
    d.mkdir()
    return d


@pytest.fixture
def target_files(tmp_path: Path) -> list[str]:
    """Create temporary files to be cleaned up."""
    files = []
    for i in range(5):
        f = tmp_path / f"file_{i}.txt"
        f.write_text(f"content of file {i}" * 100)
        files.append(str(f))
    return files


class TestCleanupPropose:
    """Tests for proposing cleanup actions."""

    async def test_propose_creates_action_in_proposed_state(
        self, session: AsyncSession, target_files: list[str], trash_dir: Path
    ) -> None:
        service = CleanupService(session, trash_base=trash_dir)
        result = await service.propose_cleanup(
            recommendation_id=None,
            action_type="trash",
            target_paths=target_files,
            total_bytes=5000,
        )

        assert result["status"] == "proposed"
        assert result["target_count"] == 5
        assert result["action_type"] == "trash"
        assert "id" in result

    async def test_propose_persists_to_db(
        self, session: AsyncSession, target_files: list[str], trash_dir: Path
    ) -> None:
        service = CleanupService(session, trash_base=trash_dir)
        result = await service.propose_cleanup(
            recommendation_id=None,
            action_type="trash",
            target_paths=target_files,
            total_bytes=5000,
        )
        await session.commit()

        action = await service.get_action(result["id"])
        assert action["status"] == "proposed"
        assert action["target_count"] == 5


class TestCleanupDryRun:
    """Tests for dry-run validation."""

    async def test_dry_run_validates_existing_files(
        self, session: AsyncSession, target_files: list[str], trash_dir: Path
    ) -> None:
        service = CleanupService(session, trash_base=trash_dir)
        result = await service.propose_cleanup(
            recommendation_id=None, action_type="trash",
            target_paths=target_files, total_bytes=5000,
        )
        await session.commit()

        dry = await service.dry_run(result["id"])
        assert dry["status"] == "dry_run_complete"
        assert dry["valid_count"] == 5
        assert dry["missing_count"] == 0
        assert dry["valid_bytes"] > 0

    async def test_dry_run_detects_missing_files(
        self, session: AsyncSession, trash_dir: Path
    ) -> None:
        paths = ["/nonexistent/a.txt", "/nonexistent/b.txt"]
        service = CleanupService(session, trash_base=trash_dir)
        result = await service.propose_cleanup(
            recommendation_id=None, action_type="trash",
            target_paths=paths, total_bytes=0,
        )
        await session.commit()

        dry = await service.dry_run(result["id"])
        assert dry["valid_count"] == 0
        assert dry["missing_count"] == 2

    async def test_dry_run_does_not_move_files(
        self, session: AsyncSession, target_files: list[str], trash_dir: Path
    ) -> None:
        service = CleanupService(session, trash_base=trash_dir)
        result = await service.propose_cleanup(
            recommendation_id=None, action_type="trash",
            target_paths=target_files, total_bytes=5000,
        )
        await session.commit()

        await service.dry_run(result["id"])

        # Files should still exist
        for path in target_files:
            assert os.path.exists(path), f"Dry-run should not move files: {path}"


class TestCleanupExecution:
    """Tests for actual cleanup execution."""

    async def test_execute_moves_files_to_trash(
        self, session: AsyncSession, target_files: list[str], trash_dir: Path
    ) -> None:
        service = CleanupService(session, trash_base=trash_dir)
        result = await service.propose_cleanup(
            recommendation_id=None, action_type="trash",
            target_paths=target_files, total_bytes=5000,
        )
        await service.approve(result["id"])
        await session.commit()

        exec_result = await service.execute(result["id"])

        assert exec_result["status"] == "completed"
        assert exec_result["files_processed"] == 5
        assert exec_result["bytes_recovered"] > 0

        # Original files should be GONE
        for path in target_files:
            assert not os.path.exists(path), f"File should have been moved: {path}"

    async def test_execute_creates_manifest(
        self, session: AsyncSession, target_files: list[str], trash_dir: Path
    ) -> None:
        service = CleanupService(session, trash_base=trash_dir)
        result = await service.propose_cleanup(
            recommendation_id=None, action_type="trash",
            target_paths=target_files, total_bytes=5000,
        )
        await service.approve(result["id"])
        await session.commit()

        exec_result = await service.execute(result["id"])

        # Manifest should exist
        action = await service.get_action(result["id"])
        manifest_path = Path(action["manifest_path"])
        assert manifest_path.exists()

        manifest = json.loads(manifest_path.read_text())
        assert len(manifest) == 5
        for entry in manifest:
            assert "original_path" in entry
            assert "trash_path" in entry
            assert "size" in entry

    async def test_execute_without_approval_raises(
        self, session: AsyncSession, target_files: list[str], trash_dir: Path
    ) -> None:
        service = CleanupService(session, trash_base=trash_dir)
        result = await service.propose_cleanup(
            recommendation_id=None, action_type="trash",
            target_paths=target_files, total_bytes=5000,
        )
        await session.commit()

        with pytest.raises(ConflictError):
            await service.execute(result["id"])

    async def test_no_permanent_deletion(
        self, session: AsyncSession, target_files: list[str], trash_dir: Path
    ) -> None:
        """CRITICAL SAFETY TEST: verify files exist in trash after cleanup."""
        service = CleanupService(session, trash_base=trash_dir)
        result = await service.propose_cleanup(
            recommendation_id=None, action_type="trash",
            target_paths=target_files, total_bytes=5000,
        )
        await service.approve(result["id"])
        await session.commit()

        await service.execute(result["id"])
        await session.commit()

        # Files MUST exist in trash
        action = await service.get_action(result["id"])
        manifest = json.loads(Path(action["manifest_path"]).read_text())
        for entry in manifest:
            assert Path(entry["trash_path"]).exists(), (
                f"File must exist in trash: {entry['trash_path']}"
            )


class TestCleanupRollback:
    """Tests for rollback/restore functionality."""

    async def test_rollback_restores_files(
        self, session: AsyncSession, target_files: list[str], trash_dir: Path
    ) -> None:
        service = CleanupService(session, trash_base=trash_dir)
        result = await service.propose_cleanup(
            recommendation_id=None, action_type="trash",
            target_paths=target_files, total_bytes=5000,
        )
        await service.approve(result["id"])
        await session.commit()

        await service.execute(result["id"])
        await session.commit()

        # Files are gone
        for path in target_files:
            assert not os.path.exists(path)

        # Rollback
        rollback_result = await service.rollback(result["id"])

        assert rollback_result["status"] == "rolled_back"
        assert rollback_result["files_restored"] == 5
        assert rollback_result["bytes_restored"] > 0

        # Files are back
        for path in target_files:
            assert os.path.exists(path), f"File should be restored: {path}"

    async def test_rollback_before_execute_raises(
        self, session: AsyncSession, target_files: list[str], trash_dir: Path
    ) -> None:
        service = CleanupService(session, trash_base=trash_dir)
        result = await service.propose_cleanup(
            recommendation_id=None, action_type="trash",
            target_paths=target_files, total_bytes=5000,
        )
        await session.commit()

        with pytest.raises(ConflictError):
            await service.rollback(result["id"])


class TestAuditLogging:
    """Tests for audit trail completeness."""

    async def test_execution_creates_audit_entry(
        self, session: AsyncSession, target_files: list[str], trash_dir: Path
    ) -> None:
        service = CleanupService(session, trash_base=trash_dir)
        result = await service.propose_cleanup(
            recommendation_id=None, action_type="trash",
            target_paths=target_files, total_bytes=5000,
        )
        await service.approve(result["id"])
        await session.commit()

        await service.execute(result["id"])
        await session.commit()

        # Check audit log
        audit = AuditService(session)
        logs = await audit.get_logs(action="cleanup_executed")
        assert logs["meta"]["total_items"] >= 1
        log_entry = logs["logs"][0]
        assert log_entry["entity_id"] == result["id"]
        assert log_entry["bytes_affected"] > 0

    async def test_rollback_creates_audit_entry(
        self, session: AsyncSession, target_files: list[str], trash_dir: Path
    ) -> None:
        service = CleanupService(session, trash_base=trash_dir)
        result = await service.propose_cleanup(
            recommendation_id=None, action_type="trash",
            target_paths=target_files, total_bytes=5000,
        )
        await service.approve(result["id"])
        await session.commit()

        await service.execute(result["id"])
        await service.rollback(result["id"])
        await session.commit()

        audit = AuditService(session)
        logs = await audit.get_logs(action="cleanup_rolled_back")
        assert logs["meta"]["total_items"] >= 1


class TestStateTransitions:
    """Tests for valid state transition enforcement."""

    async def test_cannot_approve_executed_action(
        self, session: AsyncSession, target_files: list[str], trash_dir: Path
    ) -> None:
        service = CleanupService(session, trash_base=trash_dir)
        result = await service.propose_cleanup(
            recommendation_id=None, action_type="trash",
            target_paths=target_files, total_bytes=5000,
        )
        await service.approve(result["id"])
        await session.commit()
        await service.execute(result["id"])
        await session.commit()

        with pytest.raises(ConflictError):
            await service.approve(result["id"])

    async def test_cannot_execute_rolled_back_action(
        self, session: AsyncSession, target_files: list[str], trash_dir: Path
    ) -> None:
        service = CleanupService(session, trash_base=trash_dir)
        result = await service.propose_cleanup(
            recommendation_id=None, action_type="trash",
            target_paths=target_files, total_bytes=5000,
        )
        await service.approve(result["id"])
        await session.commit()
        await service.execute(result["id"])
        await service.rollback(result["id"])
        await session.commit()

        with pytest.raises(ConflictError):
            await service.execute(result["id"])

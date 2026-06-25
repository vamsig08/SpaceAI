"""Integration tests for the workspace analysis background pipeline."""

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models.base import generate_uuid, utc_now
from app.services.workspace_service import WorkspaceService, run_workspace_analysis
from app.workers.progress import ProgressReporter
from app.workers.task_manager import TaskState, TaskStatus, TaskType


def _days_ago(days: int) -> str:
    dt = datetime.now(timezone.utc) - timedelta(days=days)
    return dt.strftime("%Y-%m-%dT%H:%M:%S.%fZ")


async def _seed_dev_workspace_files(
    session_factory: async_sessionmaker[AsyncSession],
) -> str:
    """Seed a scan with realistic developer workspace file paths.

    Returns scan_id.
    """
    scan_id = generate_uuid()
    now = utc_now()
    recent = _days_ago(5)
    old = _days_ago(300)

    async with session_factory() as session:
        await session.execute(
            text(
                "INSERT INTO scans (id, root_path, status, total_files, created_at) "
                "VALUES (:id, '/home/dev', 'completed', 50, :now)"
            ),
            {"id": scan_id, "now": now},
        )

        # Active Node project
        files = [
            (f"/home/dev/webapp/node_modules/react/index.js", 50000, "code", recent),
            (f"/home/dev/webapp/node_modules/lodash/lodash.min.js", 80000, "code", recent),
            (f"/home/dev/webapp/node_modules/webpack/cli.js", 30000, "code", recent),
            (f"/home/dev/webapp/.next/cache/data.json", 20000, "other", recent),
            (f"/home/dev/webapp/src/app.tsx", 3000, "code", recent),
            # Old Python project (abandoned)
            (f"/home/dev/old-api/.venv/lib/python3.12/site-packages/flask.py", 100000, "code", old),
            (f"/home/dev/old-api/.venv/bin/python3", 5000, "other", old),
            (f"/home/dev/old-api/__pycache__/app.cpython-312.pyc", 4000, "other", old),
            (f"/home/dev/old-api/app.py", 2000, "code", old),
            # ML models
            (f"/home/dev/ml-project/models/gpt2.pt", 500000000, "data", old),
            (f"/home/dev/ml-project/models/bert.onnx", 400000000, "data", old),
            # Java build
            (f"/home/dev/java-service/target/classes/Main.class", 8000, "other", recent),
            (f"/home/dev/java-service/.gradle/caches/transforms.bin", 25000, "other", recent),
            # IDE artifacts
            (f"/home/dev/webapp/.idea/workspace.xml", 10000, "other", recent),
            # Duplicate project names
            (f"/home/dev/webapp-backup/node_modules/react/index.js", 50000, "code", old),
        ]

        for path, size, category, modified in files:
            await session.execute(
                text(
                    """
                    INSERT INTO files (id, scan_id, path, directory, filename, extension,
                        size_bytes, category, modified_at, discovered_at)
                    VALUES (:id, :sid, :path, :dir, :name, :ext, :size, :cat, :mod, :now)
                    """
                ),
                {
                    "id": generate_uuid(),
                    "sid": scan_id,
                    "path": path,
                    "dir": "/".join(path.split("/")[:-1]),
                    "name": path.split("/")[-1],
                    "ext": "." + path.split(".")[-1] if "." in path.split("/")[-1] else None,
                    "size": size,
                    "cat": category,
                    "mod": modified,
                    "now": now,
                },
            )
        await session.commit()

    return scan_id


class TestWorkspacePipeline:
    """Integration tests running the full workspace analysis pipeline."""

    async def test_detects_all_workspace_types(
        self, session_factory: async_sessionmaker[AsyncSession]
    ) -> None:
        scan_id = await _seed_dev_workspace_files(session_factory)

        state = TaskState(task_id=scan_id, task_type=TaskType.ANALYTICS)
        reporter = ProgressReporter()

        await run_workspace_analysis(
            task_state=state,
            scan_id=scan_id,
            session_factory=session_factory,
            reporter=reporter,
        )

        assert state.status == TaskStatus.COMPLETED

        # Verify workspaces were created in DB
        async with session_factory() as session:
            result = await session.execute(
                text("SELECT workspace_type, COUNT(*) FROM dev_workspaces WHERE scan_id = :sid GROUP BY workspace_type"),
                {"sid": scan_id},
            )
            types = {row[0]: row[1] for row in result.all()}

        assert "node" in types
        assert "python" in types
        assert "ml" in types
        assert "java" in types

    async def test_marks_abandoned_projects(
        self, session_factory: async_sessionmaker[AsyncSession]
    ) -> None:
        scan_id = await _seed_dev_workspace_files(session_factory)

        state = TaskState(task_id=scan_id, task_type=TaskType.ANALYTICS)
        reporter = ProgressReporter()

        await run_workspace_analysis(
            task_state=state,
            scan_id=scan_id,
            session_factory=session_factory,
            reporter=reporter,
        )

        async with session_factory() as session:
            result = await session.execute(
                text("SELECT COUNT(*) FROM dev_workspaces WHERE scan_id = :sid AND is_active = 0"),
                {"sid": scan_id},
            )
            inactive = result.scalar_one()

        assert inactive >= 2  # old-api and ml-project should be inactive

    async def test_calculates_recoverable_space(
        self, session_factory: async_sessionmaker[AsyncSession]
    ) -> None:
        scan_id = await _seed_dev_workspace_files(session_factory)

        state = TaskState(task_id=scan_id, task_type=TaskType.ANALYTICS)
        reporter = ProgressReporter()

        await run_workspace_analysis(
            task_state=state,
            scan_id=scan_id,
            session_factory=session_factory,
            reporter=reporter,
        )

        async with session_factory() as session:
            result = await session.execute(
                text("SELECT SUM(recoverable_bytes) FROM dev_workspaces WHERE scan_id = :sid"),
                {"sid": scan_id},
            )
            total_recoverable = result.scalar_one()

        # ML models alone are 900MB, plus node_modules, .venv, etc.
        assert total_recoverable > 900000000

    async def test_service_summary_after_analysis(
        self, session_factory: async_sessionmaker[AsyncSession]
    ) -> None:
        scan_id = await _seed_dev_workspace_files(session_factory)

        state = TaskState(task_id=scan_id, task_type=TaskType.ANALYTICS)
        reporter = ProgressReporter()
        await run_workspace_analysis(
            task_state=state, scan_id=scan_id,
            session_factory=session_factory, reporter=reporter,
        )

        async with session_factory() as session:
            service = WorkspaceService(session)
            summary = await service.get_workspace_summary(scan_id)

        assert summary["total_workspaces"] >= 4
        assert summary["total_recoverable_bytes"] > 0
        assert summary["inactive_workspaces"] >= 2
        assert "node" in summary["by_type"]

    async def test_service_abandoned_projects(
        self, session_factory: async_sessionmaker[AsyncSession]
    ) -> None:
        scan_id = await _seed_dev_workspace_files(session_factory)

        state = TaskState(task_id=scan_id, task_type=TaskType.ANALYTICS)
        reporter = ProgressReporter()
        await run_workspace_analysis(
            task_state=state, scan_id=scan_id,
            session_factory=session_factory, reporter=reporter,
        )

        async with session_factory() as session:
            service = WorkspaceService(session)
            result = await service.get_abandoned_projects(scan_id)

        assert result["abandoned_count"] >= 2
        assert result["total_recoverable_bytes"] > 0
        assert len(result["projects"]) >= 2

    async def test_cancellation_stops_pipeline(
        self, session_factory: async_sessionmaker[AsyncSession]
    ) -> None:
        scan_id = await _seed_dev_workspace_files(session_factory)

        state = TaskState(task_id=scan_id, task_type=TaskType.ANALYTICS)
        state.cancel_event.set()
        reporter = ProgressReporter()

        await run_workspace_analysis(
            task_state=state, scan_id=scan_id,
            session_factory=session_factory, reporter=reporter,
        )

        assert state.status == TaskStatus.CANCELLED

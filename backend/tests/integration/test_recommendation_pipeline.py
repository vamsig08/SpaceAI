"""Integration tests for recommendation generation pipeline."""

import json
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models.base import generate_uuid, utc_now
from app.services.recommendation_service import (
    RecommendationService,
    run_recommendation_generation,
)
from app.workers.progress import ProgressReporter
from app.workers.task_manager import TaskState, TaskStatus, TaskType


def _days_ago(days: int) -> str:
    dt = datetime.now(timezone.utc) - timedelta(days=days)
    return dt.strftime("%Y-%m-%dT%H:%M:%S.%fZ")


async def _seed_full_analysis(session_factory: async_sessionmaker[AsyncSession]) -> str:
    """Create a scan with duplicates, stale files, and workspaces for testing."""
    scan_id = generate_uuid()
    now = utc_now()

    async with session_factory() as session:
        # Create scan
        await session.execute(
            text("INSERT INTO scans (id, root_path, status, total_files, total_size_bytes, created_at) VALUES (:id, '/home', 'completed', 100, 10000000000, :now)"),
            {"id": scan_id, "now": now},
        )

        # Files: some stale, some with duplicates
        files = [
            # Stale archive candidates
            (generate_uuid(), "/home/old/backup.zip", 2000000000, "archive", _days_ago(500), 0.95, "low", 1),
            (generate_uuid(), "/home/old/dump.sql", 1000000000, "data", _days_ago(400), 0.90, "low", 1),
            # Stale code
            (generate_uuid(), "/home/old/project/main.py", 5000, "code", _days_ago(300), 0.80, "medium", 1),
            # Active files
            (generate_uuid(), "/home/active/app.py", 3000, "code", _days_ago(1), 0.01, "high", 0),
            # Large files
            (generate_uuid(), "/home/data/model.pt", 5000000000, "data", _days_ago(60), 0.2, "medium", 0),
            (generate_uuid(), "/home/data/dataset.h5", 3000000000, "data", _days_ago(30), 0.1, "medium", 0),
        ]
        for fid, path, size, cat, mod, score, risk, is_stale in files:
            await session.execute(
                text(
                    """INSERT INTO files (id, scan_id, path, directory, filename, extension, size_bytes, category,
                       modified_at, accessed_at, stale_score, risk_level, is_stale, discovered_at)
                    VALUES (:id, :sid, :path, :dir, :name, :ext, :size, :cat, :mod, :mod, :score, :risk, :stale, :now)"""
                ),
                {
                    "id": fid, "sid": scan_id, "path": path,
                    "dir": "/".join(path.split("/")[:-1]),
                    "name": path.split("/")[-1],
                    "ext": "." + path.split(".")[-1],
                    "size": size, "cat": cat, "mod": mod,
                    "score": score, "risk": risk, "stale": is_stale, "now": now,
                },
            )

        # Duplicate groups
        dup_id = generate_uuid()
        await session.execute(
            text(
                "INSERT INTO duplicate_groups (id, scan_id, sha256_hash, file_size_bytes, member_count, wasted_bytes, status, created_at) "
                "VALUES (:id, :sid, 'abc123', 100000000, 5, 400000000, 'unresolved', :now)"
            ),
            {"id": dup_id, "sid": scan_id, "now": now},
        )

        # Workspaces
        ws_data = [
            ("node", 2000000000, 2000000000, 2000000000, _days_ago(5), 1),
            ("python", 500000000, 500000000, 500000000, _days_ago(200), 0),
            ("ml", 3000000000, 3000000000, 0, _days_ago(250), 0),
        ]
        for ws_type, total, recoverable, safe, modified, active in ws_data:
            await session.execute(
                text(
                    """INSERT INTO dev_workspaces (id, scan_id, path, name, workspace_type, total_size_bytes,
                       recoverable_bytes, safe_recoverable_bytes, last_modified_at, is_active, risk_level, artifacts, created_at)
                    VALUES (:id, :sid, :path, :name, :type, :total, :rec, :safe, :mod, :active, 'low', '[]', :now)"""
                ),
                {
                    "id": generate_uuid(), "sid": scan_id,
                    "path": f"/home/{ws_type}-project",
                    "name": f"{ws_type}-project",
                    "type": ws_type, "total": total,
                    "rec": recoverable, "safe": safe,
                    "mod": modified, "active": active, "now": now,
                },
            )

        await session.commit()

    return scan_id


class TestRecommendationPipeline:
    """Integration tests for the full recommendation generation pipeline."""

    async def test_generates_recommendations_from_analysis(
        self, session_factory: async_sessionmaker[AsyncSession]
    ) -> None:
        scan_id = await _seed_full_analysis(session_factory)

        state = TaskState(task_id=scan_id, task_type=TaskType.RECOMMENDATION)
        reporter = ProgressReporter()

        await run_recommendation_generation(
            task_state=state,
            scan_id=scan_id,
            session_factory=session_factory,
            reporter=reporter,
        )

        assert state.status == TaskStatus.COMPLETED

        # Verify recommendations persisted
        async with session_factory() as session:
            result = await session.execute(
                text("SELECT COUNT(*) FROM recommendations WHERE scan_id = :sid"),
                {"sid": scan_id},
            )
            count = result.scalar_one()
        assert count >= 3  # At least: duplicates, workspace, stale/archive

    async def test_recommendations_ordered_by_priority(
        self, session_factory: async_sessionmaker[AsyncSession]
    ) -> None:
        scan_id = await _seed_full_analysis(session_factory)

        state = TaskState(task_id=scan_id, task_type=TaskType.RECOMMENDATION)
        reporter = ProgressReporter()
        await run_recommendation_generation(
            task_state=state, scan_id=scan_id,
            session_factory=session_factory, reporter=reporter,
        )

        async with session_factory() as session:
            service = RecommendationService(session)
            result = await service.get_recommendations(scan_id)

        recs = result["recommendations"]
        priority_order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
        priorities = [priority_order[r["priority"]] for r in recs]
        assert priorities == sorted(priorities)

    async def test_recommendation_service_status_update(
        self, session_factory: async_sessionmaker[AsyncSession]
    ) -> None:
        scan_id = await _seed_full_analysis(session_factory)

        state = TaskState(task_id=scan_id, task_type=TaskType.RECOMMENDATION)
        reporter = ProgressReporter()
        await run_recommendation_generation(
            task_state=state, scan_id=scan_id,
            session_factory=session_factory, reporter=reporter,
        )

        async with session_factory() as session:
            service = RecommendationService(session)
            recs = await service.get_recommendations(scan_id)
            rec_id = recs["recommendations"][0]["id"]

            # Accept
            result = await service.update_status(rec_id, "accepted")
            assert result["status"] == "accepted"
            await session.commit()

        # Verify persisted
        async with session_factory() as session:
            service = RecommendationService(session)
            detail = await service.get_recommendation_detail(rec_id)
            assert detail["status"] == "accepted"

    async def test_recommendation_service_dismiss_with_reason(
        self, session_factory: async_sessionmaker[AsyncSession]
    ) -> None:
        scan_id = await _seed_full_analysis(session_factory)

        state = TaskState(task_id=scan_id, task_type=TaskType.RECOMMENDATION)
        reporter = ProgressReporter()
        await run_recommendation_generation(
            task_state=state, scan_id=scan_id,
            session_factory=session_factory, reporter=reporter,
        )

        async with session_factory() as session:
            service = RecommendationService(session)
            recs = await service.get_recommendations(scan_id)
            rec_id = recs["recommendations"][0]["id"]

            result = await service.update_status(rec_id, "dismissed", "not relevant")
            assert result["status"] == "dismissed"
            await session.commit()

        async with session_factory() as session:
            service = RecommendationService(session)
            detail = await service.get_recommendation_detail(rec_id)
            assert detail["dismissed_reason"] == "not relevant"

    async def test_cancellation_stops_generation(
        self, session_factory: async_sessionmaker[AsyncSession]
    ) -> None:
        scan_id = await _seed_full_analysis(session_factory)

        state = TaskState(task_id=scan_id, task_type=TaskType.RECOMMENDATION)
        state.cancel_event.set()
        reporter = ProgressReporter()

        await run_recommendation_generation(
            task_state=state, scan_id=scan_id,
            session_factory=session_factory, reporter=reporter,
        )

        assert state.status == TaskStatus.CANCELLED

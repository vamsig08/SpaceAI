"""Unit tests for stale file scoring, classification, and service logic."""

import asyncio
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models.base import generate_uuid, utc_now
from app.services.stale_file_service import (
    StaleFileService,
    classify_freshness,
    compute_risk_level,
    compute_stale_score,
    is_dev_artifact_path,
    run_stale_analysis,
    _days_since,
)
from app.workers.progress import ProgressReporter
from app.workers.task_manager import TaskState, TaskStatus, TaskType


# ─── Scoring Function Tests ───────────────────────────────────────────────────


class TestComputeStaleScore:
    """Tests for the staleness scoring function."""

    def test_fresh_file_scores_near_zero(self) -> None:
        score = compute_stale_score(days_since_access=1, days_since_modify=1)
        assert score < 0.1

    def test_30_day_file_scores_low(self) -> None:
        score = compute_stale_score(days_since_access=30, days_since_modify=30)
        assert 0.05 < score < 0.2

    def test_180_day_file_scores_around_half(self) -> None:
        score = compute_stale_score(days_since_access=180, days_since_modify=180)
        assert 0.4 < score < 0.6

    def test_365_day_file_scores_high(self) -> None:
        score = compute_stale_score(days_since_access=365, days_since_modify=365)
        assert score > 0.8

    def test_very_old_file_approaches_one(self) -> None:
        score = compute_stale_score(days_since_access=1000, days_since_modify=1000)
        assert score > 0.95

    def test_zero_days_returns_zero(self) -> None:
        assert compute_stale_score(0, 0) == 0.0

    def test_access_weighted_less_than_modify(self) -> None:
        # Same modify time, different access times — modify now dominates (70%)
        recent_access = compute_stale_score(days_since_access=30, days_since_modify=365)
        old_access = compute_stale_score(days_since_access=365, days_since_modify=30)
        # Recent access but old modify should score HIGHER (modify has 70% weight)
        assert recent_access > old_access

    def test_score_is_bounded_0_to_1(self) -> None:
        assert 0.0 <= compute_stale_score(0, 0) <= 1.0
        assert 0.0 <= compute_stale_score(10000, 10000) <= 1.0
        assert 0.0 <= compute_stale_score(1, 10000) <= 1.0


class TestClassifyFreshness:
    """Tests for freshness tier classification."""

    def test_active_within_30_days(self) -> None:
        assert classify_freshness(0) == "active"
        assert classify_freshness(15) == "active"
        assert classify_freshness(30) == "active"

    def test_aging_30_to_180_days(self) -> None:
        assert classify_freshness(31) == "aging"
        assert classify_freshness(100) == "aging"
        assert classify_freshness(180) == "aging"

    def test_stale_180_to_365_days(self) -> None:
        assert classify_freshness(181) == "stale"
        assert classify_freshness(300) == "stale"
        assert classify_freshness(365) == "stale"

    def test_archive_candidate_over_365_days(self) -> None:
        assert classify_freshness(366) == "archive_candidate"
        assert classify_freshness(730) == "archive_candidate"
        assert classify_freshness(9999) == "archive_candidate"


class TestComputeRiskLevel:
    """Tests for risk level computation."""

    def test_dev_artifacts_always_low_risk(self) -> None:
        assert compute_risk_level("code", 0.1, is_in_dev_artifact=True) == "low"
        assert compute_risk_level("document", 0.0, is_in_dev_artifact=True) == "low"

    def test_code_fresh_is_high_risk(self) -> None:
        assert compute_risk_level("code", 0.3, is_in_dev_artifact=False) == "high"

    def test_code_moderately_stale_is_medium_risk(self) -> None:
        assert compute_risk_level("code", 0.6, is_in_dev_artifact=False) == "medium"

    def test_code_very_stale_is_low_risk(self) -> None:
        assert compute_risk_level("code", 0.9, is_in_dev_artifact=False) == "low"

    def test_media_always_low_risk(self) -> None:
        assert compute_risk_level("video", 0.1, is_in_dev_artifact=False) == "low"
        assert compute_risk_level("audio", 0.0, is_in_dev_artifact=False) == "low"
        assert compute_risk_level("archive", 0.5, is_in_dev_artifact=False) == "low"

    def test_other_category_medium_when_fresh(self) -> None:
        assert compute_risk_level("other", 0.3, is_in_dev_artifact=False) == "medium"

    def test_other_category_low_when_stale(self) -> None:
        assert compute_risk_level("other", 0.7, is_in_dev_artifact=False) == "low"

    def test_none_category_treated_as_other(self) -> None:
        assert compute_risk_level(None, 0.3, is_in_dev_artifact=False) == "medium"


class TestIsDevArtifactPath:
    """Tests for developer artifact path detection."""

    def test_node_modules(self) -> None:
        assert is_dev_artifact_path("/home/dev/project/node_modules/react/index.js")

    def test_venv(self) -> None:
        assert is_dev_artifact_path("/home/dev/project/.venv/lib/python3.12/site.py")
        assert is_dev_artifact_path("/home/dev/project/venv/bin/python")

    def test_pycache(self) -> None:
        assert is_dev_artifact_path("/home/dev/project/__pycache__/module.cpython-312.pyc")

    def test_build_dirs(self) -> None:
        assert is_dev_artifact_path("/home/dev/java-app/target/classes/Main.class")
        assert is_dev_artifact_path("/home/dev/app/build/output.js")
        assert is_dev_artifact_path("/home/dev/app/dist/bundle.js")

    def test_normal_paths_not_detected(self) -> None:
        assert not is_dev_artifact_path("/home/dev/project/src/main.py")
        assert not is_dev_artifact_path("/home/dev/documents/report.pdf")
        assert not is_dev_artifact_path("/home/dev/photos/vacation.jpg")


class TestDaysSince:
    """Tests for timestamp parsing helper."""

    def test_parses_iso_timestamp(self) -> None:
        now = datetime(2026, 6, 23, tzinfo=timezone.utc)
        ts = "2026-06-13T10:00:00.000000Z"
        days = _days_since(ts, now)
        assert 9.5 < days < 10.5

    def test_none_returns_high_value(self) -> None:
        now = datetime(2026, 6, 23, tzinfo=timezone.utc)
        assert _days_since(None, now) == 9999.0

    def test_empty_string_returns_high_value(self) -> None:
        now = datetime(2026, 6, 23, tzinfo=timezone.utc)
        assert _days_since("", now) == 9999.0

    def test_invalid_format_returns_high_value(self) -> None:
        now = datetime(2026, 6, 23, tzinfo=timezone.utc)
        assert _days_since("not-a-date", now) == 9999.0

    def test_future_timestamp_returns_zero(self) -> None:
        now = datetime(2026, 6, 23, tzinfo=timezone.utc)
        future = "2026-12-31T00:00:00.000000Z"
        assert _days_since(future, now) == 0.0


# ─── Service Integration Tests ────────────────────────────────────────────────


async def _insert_files_with_dates(
    session: AsyncSession,
    scan_id: str,
    files: list[tuple[str, int, str, str, str]],
) -> None:
    """Insert files with specific access/modify dates.

    Args:
        session: DB session.
        scan_id: Scan ID.
        files: List of (path, size, category, accessed_at, modified_at) tuples.
    """
    now = utc_now()
    for path, size, category, accessed, modified in files:
        await session.execute(
            text(
                """
                INSERT INTO files (id, scan_id, path, directory, filename, extension,
                    size_bytes, category, accessed_at, modified_at, discovered_at)
                VALUES (:id, :scan_id, :path, :dir, :name, :ext, :size, :cat, :acc, :mod, :now)
                """
            ),
            {
                "id": generate_uuid(),
                "scan_id": scan_id,
                "path": path,
                "dir": "/".join(path.split("/")[:-1]),
                "name": path.split("/")[-1],
                "ext": "." + path.split(".")[-1] if "." in path.split("/")[-1] else None,
                "size": size,
                "cat": category,
                "acc": accessed,
                "mod": modified,
                "now": now,
            },
        )
    await session.commit()


def _days_ago(days: int) -> str:
    """Generate an ISO timestamp N days in the past."""
    dt = datetime.now(timezone.utc) - timedelta(days=days)
    return dt.strftime("%Y-%m-%dT%H:%M:%S.%fZ")


class TestStaleAnalysisPipeline:
    """Tests for the full stale analysis background task."""

    async def test_scores_files_correctly(
        self, session: AsyncSession, session_factory: async_sessionmaker[AsyncSession], sample_scan: dict
    ) -> None:
        # Insert files with different ages
        await _insert_files_with_dates(session, sample_scan["id"], [
            ("/fresh.py", 1000, "code", _days_ago(5), _days_ago(5)),
            ("/aging.pdf", 5000, "document", _days_ago(100), _days_ago(120)),
            ("/stale.mp4", 50000, "video", _days_ago(250), _days_ago(300)),
            ("/ancient.zip", 10000, "archive", _days_ago(500), _days_ago(600)),
        ])

        state = TaskState(task_id=sample_scan["id"], task_type=TaskType.ANALYTICS)
        reporter = ProgressReporter()

        await run_stale_analysis(
            task_state=state,
            scan_id=sample_scan["id"],
            session_factory=session_factory,
            reporter=reporter,
        )

        assert state.status == TaskStatus.COMPLETED

        # Verify scores were applied
        result = await session.execute(
            text(
                "SELECT path, stale_score, is_stale, risk_level FROM files "
                "WHERE scan_id = :sid ORDER BY stale_score"
            ),
            {"sid": sample_scan["id"]},
        )
        rows = result.all()
        # Should be ordered by stale_score ascending
        assert len(rows) >= 4

        # Fresh file should have low score
        fresh = next(r for r in rows if "/fresh.py" in r[0])
        assert fresh[1] < 0.1  # stale_score
        assert fresh[2] == 0   # is_stale = False

        # Ancient file should have high score
        ancient = next(r for r in rows if "/ancient.zip" in r[0])
        assert ancient[1] > 0.9
        assert ancient[2] == 1  # is_stale = True

    async def test_cancellation_stops_analysis(
        self, session: AsyncSession, session_factory: async_sessionmaker[AsyncSession], sample_scan: dict
    ) -> None:
        await _insert_files_with_dates(session, sample_scan["id"], [
            ("/file.txt", 100, "document", _days_ago(100), _days_ago(100)),
        ])

        state = TaskState(task_id=sample_scan["id"], task_type=TaskType.ANALYTICS)
        state.cancel_event.set()  # Pre-cancel
        reporter = ProgressReporter()

        await run_stale_analysis(
            task_state=state,
            scan_id=sample_scan["id"],
            session_factory=session_factory,
            reporter=reporter,
        )

        assert state.status == TaskStatus.CANCELLED


class TestStaleFileServiceSummary:
    """Tests for summary and listing endpoints."""

    async def test_get_stale_summary(
        self, session: AsyncSession, session_factory: async_sessionmaker[AsyncSession], sample_scan: dict
    ) -> None:
        # Insert scored files
        await _insert_files_with_dates(session, sample_scan["id"], [
            ("/fresh.py", 1000, "code", _days_ago(5), _days_ago(5)),
            ("/stale.mp4", 50000, "video", _days_ago(300), _days_ago(300)),
            ("/ancient.zip", 10000, "archive", _days_ago(500), _days_ago(600)),
        ])

        # Run scoring
        state = TaskState(task_id=sample_scan["id"], task_type=TaskType.ANALYTICS)
        reporter = ProgressReporter()
        await run_stale_analysis(
            task_state=state, scan_id=sample_scan["id"],
            session_factory=session_factory, reporter=reporter,
        )

        service = StaleFileService(session)
        summary = await service.get_stale_summary(sample_scan["id"])

        assert summary["scan_id"] == sample_scan["id"]
        assert summary["total_stale_files"] >= 2  # stale + archive_candidate
        assert summary["recoverable_bytes"] > 0

    async def test_get_dev_artifact_summary(
        self, session: AsyncSession, sample_scan: dict
    ) -> None:
        # Insert files in dev artifact directories
        now = utc_now()
        await _insert_files_with_dates(session, sample_scan["id"], [
            ("/project/node_modules/react/index.js", 5000, "code", _days_ago(10), _days_ago(10)),
            ("/project/node_modules/lodash/lodash.js", 8000, "code", _days_ago(10), _days_ago(10)),
            ("/project/.venv/lib/site.py", 3000, "code", _days_ago(30), _days_ago(30)),
            ("/project/src/main.py", 1000, "code", _days_ago(1), _days_ago(1)),
        ])

        service = StaleFileService(session)
        result = await service.get_dev_artifact_summary(sample_scan["id"])

        assert result["total_artifact_files"] >= 3
        assert result["total_recoverable_bytes"] >= 16000
        assert "node_modules" in result["artifacts"]

    async def test_list_stale_files(
        self, session: AsyncSession, session_factory: async_sessionmaker[AsyncSession], sample_scan: dict
    ) -> None:
        await _insert_files_with_dates(session, sample_scan["id"], [
            ("/old1.bin", 5000, "other", _days_ago(400), _days_ago(400)),
            ("/old2.bin", 3000, "other", _days_ago(300), _days_ago(300)),
            ("/fresh.bin", 1000, "other", _days_ago(5), _days_ago(5)),
        ])

        # Score files
        state = TaskState(task_id=sample_scan["id"], task_type=TaskType.ANALYTICS)
        reporter = ProgressReporter()
        await run_stale_analysis(
            task_state=state, scan_id=sample_scan["id"],
            session_factory=session_factory, reporter=reporter,
        )

        service = StaleFileService(session)
        result = await service.get_stale_files(sample_scan["id"])

        # Should only show stale files (is_stale=1)
        assert result["meta"]["total_items"] >= 2
        for f in result["files"]:
            assert f["stale_score"] >= 0.5

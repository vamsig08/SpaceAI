"""Post-scan pipeline — automatically triggers analysis after scan completion.

When a scan completes, this pipeline runs:
1. Stale file scoring
2. Workspace detection
3. Duplicate detection (if enough files)
4. Recommendation generation (after above complete)
5. Forecast update (if enough snapshots)

This ensures a new user gets full results without manual intervention.
Runs as a background task, separate from the scan itself.
"""

from __future__ import annotations

import asyncio

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.logging import get_logger
from app.services.duplicate_service import run_duplicate_detection
from app.services.prediction_service import PredictionService
from app.services.recommendation_service import run_recommendation_generation
from app.services.stale_file_service import run_stale_analysis
from app.services.workspace_service import run_workspace_analysis
from app.workers.progress import ProgressReporter
from app.workers.task_manager import TaskState, TaskStatus, TaskType

logger = get_logger(__name__)


async def run_post_scan_pipeline(
    task_state: TaskState,
    scan_id: str,
    session_factory: async_sessionmaker[AsyncSession],
    thread_pool: "ThreadPoolExecutor",
    reporter: ProgressReporter,
) -> None:
    """Run all analysis subsystems sequentially after scan completion.

    This is submitted as a TaskType.ANALYTICS task after the scan completes.
    It chains: stale → workspace → duplicates → recommendations.

    Args:
        task_state: Task state for progress/cancellation.
        scan_id: Completed scan to analyze.
        session_factory: DB session factory.
        thread_pool: Thread pool for hash operations.
        reporter: Progress reporter.
    """
    from concurrent.futures import ThreadPoolExecutor

    logger.info("post_scan_pipeline_start", scan_id=scan_id)

    # Wait for the scan to complete before running analysis
    for _ in range(600):  # Max 5 minutes
        async with session_factory() as session:
            result = await session.execute(
                text("SELECT status FROM scans WHERE id = :sid"),
                {"sid": scan_id},
            )
            row = result.one_or_none()
            if row and row[0] in ("completed", "completed_with_warnings", "failed", "cancelled"):
                break
        await asyncio.sleep(0.5)
    else:
        logger.warning("post_scan_pipeline_timeout", scan_id=scan_id)
        task_state.status = TaskStatus.FAILED
        return

    # Verify scan actually completed (not failed/cancelled)
    async with session_factory() as session:
        result = await session.execute(
            text("SELECT status FROM scans WHERE id = :sid"), {"sid": scan_id}
        )
        row = result.one()
        if row[0] not in ("completed", "completed_with_warnings"):
            logger.info("post_scan_pipeline_skip", scan_id=scan_id, scan_status=row[0])
            task_state.status = TaskStatus.COMPLETED
            return

    # 1. Stale file analysis
    if task_state.cancel_event.is_set():
        task_state.status = TaskStatus.CANCELLED
        return

    logger.info("post_scan_pipeline_stage", scan_id=scan_id, stage="stale_analysis")
    stale_state = TaskState(task_id=f"stale-{scan_id}", task_type=TaskType.ANALYTICS)
    await run_stale_analysis(
        task_state=stale_state,
        scan_id=scan_id,
        session_factory=session_factory,
        reporter=reporter,
    )

    # 2. Workspace analysis
    if task_state.cancel_event.is_set():
        task_state.status = TaskStatus.CANCELLED
        return

    logger.info("post_scan_pipeline_stage", scan_id=scan_id, stage="workspace_analysis")
    ws_state = TaskState(task_id=f"ws-{scan_id}", task_type=TaskType.ANALYTICS)
    await run_workspace_analysis(
        task_state=ws_state,
        scan_id=scan_id,
        session_factory=session_factory,
        reporter=reporter,
    )

    # 3. Duplicate detection
    if task_state.cancel_event.is_set():
        task_state.status = TaskStatus.CANCELLED
        return

    logger.info("post_scan_pipeline_stage", scan_id=scan_id, stage="duplicate_detection")
    dup_state = TaskState(task_id=f"dup-{scan_id}", task_type=TaskType.HASH)
    await run_duplicate_detection(
        task_state=dup_state,
        scan_id=scan_id,
        session_factory=session_factory,
        thread_pool=thread_pool,
        reporter=reporter,
    )

    # 4. Recommendation generation
    if task_state.cancel_event.is_set():
        task_state.status = TaskStatus.CANCELLED
        return

    logger.info("post_scan_pipeline_stage", scan_id=scan_id, stage="recommendations")
    rec_state = TaskState(task_id=f"rec-{scan_id}", task_type=TaskType.RECOMMENDATION)
    await run_recommendation_generation(
        task_state=rec_state,
        scan_id=scan_id,
        session_factory=session_factory,
        reporter=reporter,
    )

    # 5. Forecast (only if multiple snapshots exist)
    if task_state.cancel_event.is_set():
        task_state.status = TaskStatus.CANCELLED
        return

    async with session_factory() as session:
        result = await session.execute(text("SELECT COUNT(*) FROM storage_snapshots"))
        snapshot_count = result.scalar_one()

    if snapshot_count >= 2:
        logger.info("post_scan_pipeline_stage", scan_id=scan_id, stage="forecast")
        async with session_factory() as session:
            service = PredictionService(session)
            await service.generate_forecast()
            await session.commit()

    task_state.status = TaskStatus.COMPLETED
    logger.info("post_scan_pipeline_complete", scan_id=scan_id)

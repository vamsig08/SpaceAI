"""Recommendation service — orchestrates rule engine + persistence + AI enrichment.

Gathers context from all analysis phases, runs the rule engine, persists
results to the recommendations table, and optionally enriches with AI.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import text, func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.exceptions import NotFoundError
from app.core.logging import get_logger
from app.models.base import generate_uuid, utc_now
from app.services.recommendation_engine import (
    Recommendation,
    generate_recommendations,
)
from app.workers.progress import ProgressReporter
from app.workers.task_manager import TaskState, TaskStatus

logger = get_logger(__name__)


class RecommendationService:
    """Manages recommendation lifecycle: generation, listing, status updates."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_recommendations(
        self,
        scan_id: str,
        *,
        status: str | None = None,
        priority: str | None = None,
        category: str | None = None,
        page: int = 1,
        page_size: int = 50,
    ) -> dict[str, Any]:
        """List recommendations with optional filters.

        Args:
            scan_id: Scan to query.
            status: Filter by status (pending|accepted|dismissed).
            priority: Filter by priority.
            category: Filter by recommendation category.
            page: Page number.
            page_size: Results per page.

        Returns:
            Dict with recommendations list and pagination metadata.
        """
        conditions = ["scan_id = :scan_id"]
        params: dict[str, Any] = {"scan_id": scan_id}

        if status:
            conditions.append("status = :status")
            params["status"] = status
        if priority:
            conditions.append("priority = :priority")
            params["priority"] = priority
        if category:
            conditions.append("category = :category")
            params["category"] = category

        where = " AND ".join(conditions)
        offset = (page - 1) * page_size

        count_result = await self._session.execute(
            text(f"SELECT COUNT(*) FROM recommendations WHERE {where}"), params
        )
        total = count_result.scalar_one()

        params["limit"] = page_size
        params["offset"] = offset
        result = await self._session.execute(
            text(
                f"""
                SELECT id, category, priority, title, description, explanation,
                       recoverable_bytes, confidence, affected_count, status, created_at
                FROM recommendations
                WHERE {where}
                ORDER BY
                    CASE priority
                        WHEN 'critical' THEN 0
                        WHEN 'high' THEN 1
                        WHEN 'medium' THEN 2
                        WHEN 'low' THEN 3
                    END,
                    recoverable_bytes DESC
                LIMIT :limit OFFSET :offset
                """
            ),
            params,
        )

        recommendations = [
            {
                "id": row[0],
                "category": row[1],
                "priority": row[2],
                "title": row[3],
                "description": row[4],
                "explanation": row[5],
                "recoverable_bytes": row[6],
                "confidence": row[7],
                "affected_count": row[8],
                "status": row[9],
                "created_at": row[10],
            }
            for row in result.all()
        ]

        return {
            "recommendations": recommendations,
            "meta": {
                "page": page,
                "page_size": page_size,
                "total_items": total,
                "total_pages": (total + page_size - 1) // page_size,
            },
        }

    async def get_recommendation_detail(self, rec_id: str) -> dict[str, Any]:
        """Get full details of a single recommendation.

        Args:
            rec_id: Recommendation ID.

        Returns:
            Full recommendation data.

        Raises:
            NotFoundError: If recommendation doesn't exist.
        """
        result = await self._session.execute(
            text(
                """
                SELECT id, scan_id, category, priority, title, description,
                       explanation, recoverable_bytes, confidence, affected_paths,
                       affected_count, status, dismissed_reason, created_at
                FROM recommendations WHERE id = :id
                """
            ),
            {"id": rec_id},
        )
        row = result.one_or_none()
        if row is None:
            raise NotFoundError("recommendation", rec_id)

        import json
        return {
            "id": row[0],
            "scan_id": row[1],
            "category": row[2],
            "priority": row[3],
            "title": row[4],
            "description": row[5],
            "explanation": row[6],
            "recoverable_bytes": row[7],
            "confidence": row[8],
            "affected_paths": json.loads(row[9]) if row[9] else [],
            "affected_count": row[10],
            "status": row[11],
            "dismissed_reason": row[12],
            "created_at": row[13],
        }

    async def update_status(
        self, rec_id: str, status: str, dismissed_reason: str | None = None
    ) -> dict[str, Any]:
        """Update recommendation status (accept/dismiss).

        Args:
            rec_id: Recommendation ID.
            status: New status (accepted|dismissed).
            dismissed_reason: Optional reason for dismissal.

        Returns:
            Updated recommendation summary.

        Raises:
            NotFoundError: If recommendation doesn't exist.
        """
        # Verify exists
        check = await self._session.execute(
            text("SELECT id FROM recommendations WHERE id = :id"), {"id": rec_id}
        )
        if check.one_or_none() is None:
            raise NotFoundError("recommendation", rec_id)

        params: dict[str, Any] = {"id": rec_id, "status": status}
        set_clause = "status = :status"

        if dismissed_reason:
            set_clause += ", dismissed_reason = :reason"
            params["reason"] = dismissed_reason

        await self._session.execute(
            text(f"UPDATE recommendations SET {set_clause} WHERE id = :id"),
            params,
        )
        await self._session.flush()

        return {"id": rec_id, "status": status}


# ─── Background Task ──────────────────────────────────────────────────────────


async def run_recommendation_generation(
    task_state: TaskState,
    scan_id: str,
    session_factory: async_sessionmaker[AsyncSession],
    reporter: ProgressReporter,
) -> None:
    """Generate recommendations for a scan as a background task.

    Gathers context from all analysis phases, runs the rule engine,
    and persists results.

    Args:
        task_state: Task state for progress/cancellation.
        scan_id: Scan to generate recommendations for.
        session_factory: DB session factory.
        reporter: Progress reporter.
    """
    import json

    logger.info("recommendation_generation_start", scan_id=scan_id)

    # ── Gather context from all phases ─────────────────────────────────────
    context: dict[str, Any] = {}

    async with session_factory() as session:
        # Duplicate summary
        dup_result = await session.execute(
            text(
                """
                SELECT COUNT(*) as groups,
                       COALESCE(SUM(member_count), 0) as files,
                       COALESCE(SUM(wasted_bytes), 0) as wasted
                FROM duplicate_groups WHERE scan_id = :sid
                """
            ),
            {"sid": scan_id},
        )
        dup_row = dup_result.one()
        context["duplicates"] = {
            "total_groups": dup_row[0],
            "total_duplicate_files": dup_row[1],
            "total_wasted_bytes": dup_row[2],
            "top_extensions": [],
        }

        # Stale summary
        stale_result = await session.execute(
            text(
                """
                SELECT
                    SUM(CASE WHEN stale_score >= 0.5 AND stale_score < 0.85 THEN size_bytes ELSE 0 END) as stale_bytes,
                    SUM(CASE WHEN stale_score >= 0.5 AND stale_score < 0.85 THEN 1 ELSE 0 END) as stale_count,
                    SUM(CASE WHEN stale_score >= 0.85 THEN size_bytes ELSE 0 END) as archive_bytes,
                    SUM(CASE WHEN stale_score >= 0.85 THEN 1 ELSE 0 END) as archive_count
                FROM files WHERE scan_id = :sid
                """
            ),
            {"sid": scan_id},
        )
        stale_row = stale_result.one()
        context["stale"] = {
            "classification": {
                "stale": {"bytes": stale_row[0] or 0, "count": stale_row[1] or 0},
                "archive_candidate": {"bytes": stale_row[2] or 0, "count": stale_row[3] or 0},
            },
        }

        # Workspace summary
        ws_result = await session.execute(
            text(
                """
                SELECT workspace_type, COUNT(*), SUM(total_size_bytes),
                       SUM(recoverable_bytes), SUM(safe_recoverable_bytes)
                FROM dev_workspaces WHERE scan_id = :sid
                GROUP BY workspace_type
                """
            ),
            {"sid": scan_id},
        )
        ws_rows = ws_result.all()
        ws_by_type = {
            row[0]: {
                "count": row[1],
                "total_bytes": row[2],
                "recoverable_bytes": row[3],
                "safe_recoverable_bytes": row[4],
            }
            for row in ws_rows
        }
        inactive_result = await session.execute(
            text("SELECT COUNT(*) FROM dev_workspaces WHERE scan_id = :sid AND is_active = 0"),
            {"sid": scan_id},
        )
        context["workspaces"] = {
            "by_type": ws_by_type,
            "total_workspaces": sum(r[1] for r in ws_rows),
            "total_recoverable_bytes": sum(r[3] for r in ws_rows),
            "safe_recoverable_bytes": sum(r[4] for r in ws_rows),
            "inactive_workspaces": inactive_result.scalar_one(),
        }

        # Overview (free space)
        scan_result = await session.execute(
            text("SELECT total_size_bytes FROM scans WHERE id = :sid"), {"sid": scan_id}
        )
        scan_row = scan_result.one_or_none()
        context["overview"] = {"free_storage": 0}  # Real disk usage not available in background

        # Largest files
        large_result = await session.execute(
            text(
                """
                SELECT path, size_bytes, category FROM files
                WHERE scan_id = :sid ORDER BY size_bytes DESC LIMIT 20
                """
            ),
            {"sid": scan_id},
        )
        context["largest_files"] = [
            {"path": row[0], "size_bytes": row[1], "category": row[2]}
            for row in large_result.all()
        ]

        # Growth data (simplified)
        context["growth"] = {"daily_growth_bytes": 0}

    if task_state.cancel_event.is_set():
        task_state.status = TaskStatus.CANCELLED
        return

    # ── Run rule engine ────────────────────────────────────────────────────
    recommendations = generate_recommendations(context)

    # ── Persist results ────────────────────────────────────────────────────
    async with session_factory() as session:
        # Clear old recommendations for this scan
        await session.execute(
            text("DELETE FROM recommendations WHERE scan_id = :sid"),
            {"sid": scan_id},
        )

        now = utc_now()
        for rec in recommendations:
            await session.execute(
                text(
                    """
                    INSERT INTO recommendations (
                        id, scan_id, provider, category, priority, title, description,
                        explanation, recoverable_bytes, confidence, affected_paths,
                        affected_count, status, created_at
                    ) VALUES (
                        :id, :sid, 'rule_engine', :category, :priority, :title, :desc,
                        :explanation, :recoverable, :confidence, :paths,
                        :count, 'pending', :now
                    )
                    """
                ),
                {
                    "id": generate_uuid(),
                    "sid": scan_id,
                    "category": rec.category,
                    "priority": rec.priority,
                    "title": rec.title,
                    "desc": rec.description,
                    "explanation": rec.reasoning,
                    "recoverable": rec.recoverable_bytes,
                    "confidence": rec.confidence,
                    "paths": json.dumps(rec.affected_paths[:20]),
                    "count": rec.affected_count,
                    "now": now,
                },
            )
        await session.commit()

    task_state.status = TaskStatus.COMPLETED
    logger.info(
        "recommendation_generation_complete",
        scan_id=scan_id,
        total_recommendations=len(recommendations),
    )

    await reporter.emit_completed(
        task_id=scan_id,
        scan_id=scan_id,
        total_files=len(recommendations),
        total_dirs=0,
        total_bytes=sum(r.recoverable_bytes for r in recommendations),
        duration_seconds=0,
        files_per_second=0,
    )

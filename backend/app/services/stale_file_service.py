"""Stale file analysis service — Phase 4.

Classifies files by freshness, computes confidence and risk scores,
detects inactive developer assets, and calculates recoverable storage.

Classification tiers:
  - active:            accessed within 30 days
  - aging:             accessed 30-180 days ago
  - stale:             accessed 180-365 days ago
  - archive_candidate: accessed >365 days ago

Scoring:
  - stale_score: 0.0 (fresh) to 1.0 (deeply stale) based on access/modify age
  - risk_level:  low|medium|high based on file category and staleness

Developer asset detection:
  - Old repositories (no changes in 6+ months)
  - Virtual environments (.venv, venv, node_modules)
  - Build artifacts (target, build, dist, __pycache__)
"""

from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import func, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.logging import get_logger
from app.models.base import utc_now
from app.models.file import File
from app.workers.progress import ProgressReporter
from app.workers.task_manager import TaskState, TaskStatus

logger = get_logger(__name__)

# ─── Configuration ─────────────────────────────────────────────────────────────

# Classification thresholds (days since last access)
ACTIVE_THRESHOLD_DAYS = 30
AGING_THRESHOLD_DAYS = 180
STALE_THRESHOLD_DAYS = 365

# Developer asset patterns that indicate recoverable workspace artifacts
DEV_ARTIFACT_DIRS = {
    "node_modules", ".venv", "venv", "__pycache__", ".tox",
    "target", "build", "dist", ".gradle", ".cache",
    ".pytest_cache", ".mypy_cache", ".ruff_cache",
    "coverage", "htmlcov", ".next", ".nuxt",
}

# Low-risk categories (safe to archive/delete)
LOW_RISK_CATEGORIES = {"archive", "audio", "video", "data"}
# High-risk categories (may be important even if stale)
HIGH_RISK_CATEGORIES = {"code", "document"}


# ─── Scoring Functions ─────────────────────────────────────────────────────────


def compute_stale_score(
    days_since_access: float,
    days_since_modify: float,
) -> float:
    """Compute a staleness score from 0.0 (fresh) to 1.0 (deeply stale).

    Uses a sigmoid-like curve that:
    - Returns ~0.1 at 30 days (just past active threshold)
    - Returns ~0.5 at 180 days (midpoint)
    - Returns ~0.85 at 365 days
    - Asymptotically approaches 1.0

    The access age is weighted 70% and modify age 30%, because access time
    is a stronger signal of relevance (a file may be old but still read).

    Args:
        days_since_access: Days since last access.
        days_since_modify: Days since last modification.

    Returns:
        Score between 0.0 and 1.0.
    """
    if days_since_access <= 0 and days_since_modify <= 0:
        return 0.0

    # Weighted age: modify matters more than access (atime unreliable on macOS/Windows)
    weighted_days = (days_since_access * 0.3) + (days_since_modify * 0.7)

    # Sigmoid curve centered at 180 days, steepness 0.015
    score = 1.0 / (1.0 + math.exp(-0.015 * (weighted_days - 180)))

    return round(min(max(score, 0.0), 1.0), 4)


def classify_freshness(days_since_access: float) -> str:
    """Classify a file into a freshness tier.

    Args:
        days_since_access: Days since last access timestamp.

    Returns:
        Classification string: active|aging|stale|archive_candidate
    """
    if days_since_access <= ACTIVE_THRESHOLD_DAYS:
        return "active"
    elif days_since_access <= AGING_THRESHOLD_DAYS:
        return "aging"
    elif days_since_access <= STALE_THRESHOLD_DAYS:
        return "stale"
    else:
        return "archive_candidate"


def compute_risk_level(
    category: str | None,
    stale_score: float,
    is_in_dev_artifact: bool,
) -> str:
    """Compute risk level for a file.

    Risk represents how dangerous it would be to delete/archive this file.

    Args:
        category: File category (code, document, video, etc.)
        stale_score: Staleness score 0.0-1.0.
        is_in_dev_artifact: Whether the file is inside a dev artifact dir.

    Returns:
        Risk level: low|medium|high
    """
    # Dev artifacts are always low risk (regenerable)
    if is_in_dev_artifact:
        return "low"

    cat = category or "other"

    # High-value categories start at higher risk
    if cat in HIGH_RISK_CATEGORIES:
        if stale_score < 0.5:
            return "high"
        elif stale_score < 0.8:
            return "medium"
        else:
            return "low"  # Even code becomes low-risk after 1+ year unused

    # Low-value categories (media, archives) are always lower risk
    if cat in LOW_RISK_CATEGORIES:
        return "low"

    # Default: medium if moderately stale, low if very stale
    if stale_score < 0.5:
        return "medium"
    return "low"


def is_dev_artifact_path(path: str) -> bool:
    """Check if a file path is inside a known developer artifact directory.

    Args:
        path: Normalized file path.

    Returns:
        True if the path contains a known dev artifact directory component.
    """
    parts = path.split("/")
    return any(part in DEV_ARTIFACT_DIRS for part in parts)


# ─── Service Class ─────────────────────────────────────────────────────────────


class StaleFileService:
    """Orchestrates stale file analysis for a scan."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_stale_summary(self, scan_id: str) -> dict[str, Any]:
        """Get summary of stale file analysis for a scan.

        Args:
            scan_id: Scan to summarize.

        Returns:
            Dict with classification counts, recoverable bytes, and breakdown.
        """
        # Classification counts and sizes
        stmt = text(
            """
            SELECT
                CASE
                    WHEN stale_score IS NULL OR stale_score < 0.1 THEN 'active'
                    WHEN stale_score < 0.5 THEN 'aging'
                    WHEN stale_score < 0.85 THEN 'stale'
                    ELSE 'archive_candidate'
                END as classification,
                COUNT(*) as file_count,
                COALESCE(SUM(size_bytes), 0) as total_bytes
            FROM files
            WHERE scan_id = :scan_id
            GROUP BY classification
            """
        )
        result = await self._session.execute(stmt, {"scan_id": scan_id})
        rows = result.all()

        classification = {
            "active": {"count": 0, "bytes": 0},
            "aging": {"count": 0, "bytes": 0},
            "stale": {"count": 0, "bytes": 0},
            "archive_candidate": {"count": 0, "bytes": 0},
        }
        for row in rows:
            cls_name = row[0]
            if cls_name in classification:
                classification[cls_name] = {"count": row[1], "bytes": row[2]}

        # Recoverable space (stale + archive candidates)
        recoverable_bytes = (
            classification["stale"]["bytes"]
            + classification["archive_candidate"]["bytes"]
        )

        # Risk breakdown
        risk_stmt = text(
            """
            SELECT risk_level, COUNT(*) as cnt, COALESCE(SUM(size_bytes), 0) as bytes
            FROM files
            WHERE scan_id = :scan_id AND is_stale = 1
            GROUP BY risk_level
            """
        )
        risk_result = await self._session.execute(risk_stmt, {"scan_id": scan_id})
        risk_breakdown = {
            row[0] or "unscored": {"count": row[1], "bytes": row[2]}
            for row in risk_result.all()
        }

        return {
            "scan_id": scan_id,
            "classification": classification,
            "recoverable_bytes": recoverable_bytes,
            "risk_breakdown": risk_breakdown,
            "total_stale_files": (
                classification["stale"]["count"]
                + classification["archive_candidate"]["count"]
            ),
        }

    async def get_stale_files(
        self,
        scan_id: str,
        *,
        classification: str | None = None,
        risk_level: str | None = None,
        category: str | None = None,
        min_size: int | None = None,
        page: int = 1,
        page_size: int = 50,
    ) -> dict[str, Any]:
        """List stale files with filtering and pagination.

        Args:
            scan_id: Scan to query.
            classification: Filter by freshness tier.
            risk_level: Filter by risk level.
            category: Filter by file category.
            min_size: Minimum file size filter.
            page: Page number.
            page_size: Results per page.

        Returns:
            Dict with file list and pagination metadata.
        """
        conditions = ["scan_id = :scan_id", "is_stale = 1"]
        params: dict[str, Any] = {"scan_id": scan_id}

        if risk_level:
            conditions.append("risk_level = :risk_level")
            params["risk_level"] = risk_level

        if category:
            conditions.append("category = :category")
            params["category"] = category

        if min_size:
            conditions.append("size_bytes >= :min_size")
            params["min_size"] = min_size

        if classification:
            score_ranges = {
                "aging": ("stale_score >= 0.1", "stale_score < 0.5"),
                "stale": ("stale_score >= 0.5", "stale_score < 0.85"),
                "archive_candidate": ("stale_score >= 0.85", None),
            }
            if classification in score_ranges:
                low, high = score_ranges[classification]
                conditions.append(low)
                if high:
                    conditions.append(high)

        where_clause = " AND ".join(conditions)
        offset = (page - 1) * page_size

        # Count
        count_stmt = text(f"SELECT COUNT(*) FROM files WHERE {where_clause}")
        count_result = await self._session.execute(count_stmt, params)
        total = count_result.scalar_one()

        # Fetch page
        query_stmt = text(
            f"""
            SELECT id, path, filename, extension, size_bytes, category,
                   stale_score, risk_level, accessed_at, modified_at
            FROM files
            WHERE {where_clause}
            ORDER BY stale_score DESC, size_bytes DESC
            LIMIT :limit OFFSET :offset
            """
        )
        params["limit"] = page_size
        params["offset"] = offset
        result = await self._session.execute(query_stmt, params)

        files = [
            {
                "id": row[0],
                "path": row[1],
                "filename": row[2],
                "extension": row[3],
                "size_bytes": row[4],
                "category": row[5],
                "stale_score": row[6],
                "risk_level": row[7],
                "accessed_at": row[8],
                "modified_at": row[9],
            }
            for row in result.all()
        ]

        total_pages = (total + page_size - 1) // page_size

        return {
            "files": files,
            "meta": {
                "page": page,
                "page_size": page_size,
                "total_items": total,
                "total_pages": total_pages,
            },
        }

    async def get_dev_artifact_summary(self, scan_id: str) -> dict[str, Any]:
        """Analyze stale developer artifacts (node_modules, .venv, etc).

        Args:
            scan_id: Scan to analyze.

        Returns:
            Dict with per-type artifact sizes and counts.
        """
        artifact_patterns = [
            ("node_modules", "%/node_modules/%"),
            ("python_venv", "%/.venv/%"),
            ("python_venv", "%/venv/%"),
            ("pycache", "%/__pycache__/%"),
            ("build_output", "%/build/%"),
            ("build_output", "%/dist/%"),
            ("build_output", "%/target/%"),
            ("gradle", "%/.gradle/%"),
            ("cache", "%/.cache/%"),
            ("next_cache", "%/.next/%"),
        ]

        results: dict[str, dict[str, int]] = {}

        for artifact_type, pattern in artifact_patterns:
            stmt = text(
                """
                SELECT COUNT(*) as cnt, COALESCE(SUM(size_bytes), 0) as bytes
                FROM files
                WHERE scan_id = :scan_id AND path LIKE :pattern
                """
            )
            row = (await self._session.execute(
                stmt, {"scan_id": scan_id, "pattern": pattern}
            )).one()

            if artifact_type not in results:
                results[artifact_type] = {"count": 0, "bytes": 0}
            results[artifact_type]["count"] += row[0]
            results[artifact_type]["bytes"] += row[1]

        # Filter out empty entries
        results = {k: v for k, v in results.items() if v["count"] > 0}
        total_recoverable = sum(v["bytes"] for v in results.values())

        return {
            "scan_id": scan_id,
            "artifacts": results,
            "total_recoverable_bytes": total_recoverable,
            "total_artifact_files": sum(v["count"] for v in results.values()),
        }


# ─── Background Task Function ─────────────────────────────────────────────────


async def run_stale_analysis(
    task_state: TaskState,
    scan_id: str,
    session_factory: async_sessionmaker[AsyncSession],
    reporter: ProgressReporter,
    active_days: int = ACTIVE_THRESHOLD_DAYS,
    aging_days: int = AGING_THRESHOLD_DAYS,
    stale_days: int = STALE_THRESHOLD_DAYS,
) -> None:
    """Score all files in a scan for staleness as a background task.

    Updates files.stale_score, files.risk_level, and files.is_stale in-place.
    Processes in batches of 5000 to manage memory and allow cancellation.

    Args:
        task_state: Task state for progress/cancellation.
        scan_id: Scan to analyze.
        session_factory: DB session factory.
        reporter: SSE progress reporter.
        active_days: Days threshold for "active" classification.
        aging_days: Days threshold for "aging" classification.
        stale_days: Days threshold for "stale" classification.
    """
    logger.info("stale_analysis_start", scan_id=scan_id)
    now = datetime.now(timezone.utc)
    batch_size = 5000
    offset = 0
    total_scored = 0
    total_stale = 0

    # Get total file count for progress
    async with session_factory() as session:
        result = await session.execute(
            text("SELECT COUNT(*) FROM files WHERE scan_id = :sid"),
            {"sid": scan_id},
        )
        total_files = result.scalar_one()

    while True:
        if task_state.cancel_event.is_set():
            task_state.status = TaskStatus.CANCELLED
            return

        async with session_factory() as session:
            # Fetch batch
            result = await session.execute(
                text(
                    """
                    SELECT id, path, accessed_at, modified_at, category, size_bytes
                    FROM files
                    WHERE scan_id = :scan_id
                    ORDER BY id
                    LIMIT :limit OFFSET :offset
                    """
                ),
                {"scan_id": scan_id, "limit": batch_size, "offset": offset},
            )
            rows = result.all()

            if not rows:
                break

            # Score each file
            updates = []
            for file_id, path, accessed_at, modified_at, category, size_bytes in rows:
                days_access = _days_since(accessed_at, now)
                days_modify = _days_since(modified_at, now)

                score = compute_stale_score(days_access, days_modify)
                is_dev_artifact = is_dev_artifact_path(path)
                risk = compute_risk_level(category, score, is_dev_artifact)
                is_stale = 1 if score >= 0.5 else 0

                updates.append({
                    "id": file_id,
                    "stale_score": score,
                    "risk_level": risk,
                    "is_stale": is_stale,
                })

                if is_stale:
                    total_stale += 1

            # Batch update
            for u in updates:
                await session.execute(
                    text(
                        """
                        UPDATE files SET stale_score = :stale_score,
                            risk_level = :risk_level, is_stale = :is_stale
                        WHERE id = :id
                        """
                    ),
                    u,
                )
            await session.commit()

        total_scored += len(rows)
        offset += batch_size

        # Emit progress
        await reporter.emit_progress(
            task_id=scan_id,
            files_scanned=total_scored,
            dirs_scanned=0,
            current_directory=f"Scoring files ({total_scored}/{total_files})",
            total_bytes_scanned=0,
            files_per_second=0,
        )

    task_state.status = TaskStatus.COMPLETED
    logger.info(
        "stale_analysis_complete",
        scan_id=scan_id,
        total_scored=total_scored,
        total_stale=total_stale,
    )

    await reporter.emit_completed(
        task_id=scan_id,
        scan_id=scan_id,
        total_files=total_scored,
        total_dirs=0,
        total_bytes=0,
        duration_seconds=0,
        files_per_second=0,
    )


def _days_since(timestamp_str: str | None, now: datetime) -> float:
    """Compute days between a stored ISO timestamp and now.

    Args:
        timestamp_str: ISO8601 timestamp string or None.
        now: Current time.

    Returns:
        Days elapsed, or 9999.0 if timestamp is None/unparseable.
    """
    if not timestamp_str:
        return 9999.0
    try:
        dt = datetime.fromisoformat(timestamp_str.replace("Z", "+00:00"))
        delta = now - dt
        return max(0.0, delta.total_seconds() / 86400)
    except (ValueError, TypeError):
        return 9999.0

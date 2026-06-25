"""Deterministic rule-based recommendation engine — Phase 6.

Generates actionable storage recommendations by aggregating findings from:
- Duplicate detection (Phase 3)
- Stale file analysis (Phase 4)
- Developer workspace optimizer (Phase 5)
- Storage analytics (Phase 2)

Architecture:
- Each rule is a function that evaluates analysis data and emits 0+ recommendations.
- Rules run deterministically — no LLM required for core functionality.
- AI enrichment is optional (adds explanations, summaries) via the AI provider layer.
- All recommendations include: priority, confidence, risk, recoverable space, reasoning.

Scoring methodology:
- priority: critical|high|medium|low — based on recoverable space and risk
- confidence: 0.0-1.0 — how certain we are the recommendation is valid
- risk: low|medium|high — how risky it is to act on this recommendation
- recoverable_bytes: exact or estimated space that would be freed
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.core.logging import get_logger

logger = get_logger(__name__)


@dataclass
class Recommendation:
    """A single actionable recommendation."""

    category: str           # duplicate_cleanup|stale_cleanup|workspace_cleanup|archive|large_file|growth_warning
    priority: str           # critical|high|medium|low
    title: str
    description: str
    reasoning: str
    confidence: float       # 0.0-1.0
    risk_level: str         # low|medium|high
    recoverable_bytes: int
    affected_count: int = 0
    affected_paths: list[str] = field(default_factory=list)


# ─── Priority Calculation ──────────────────────────────────────────────────────


def _compute_priority(recoverable_bytes: int, risk: str) -> str:
    """Determine priority from recoverable space and risk level.

    - critical: >50GB recoverable, low risk
    - high: >5GB recoverable OR >1GB with low risk
    - medium: >500MB recoverable
    - low: everything else
    """
    gb = recoverable_bytes / (1024 ** 3)

    if gb >= 50 and risk == "low":
        return "critical"
    if gb >= 5:
        return "high"
    if gb >= 1 and risk == "low":
        return "high"
    if gb >= 0.5:
        return "medium"
    return "low"


# ─── Rule Functions ────────────────────────────────────────────────────────────


def rule_duplicate_cleanup(context: dict[str, Any]) -> list[Recommendation]:
    """Generate recommendations from duplicate detection results.

    Triggers when:
    - Duplicate groups exist with significant wasted space
    """
    duplicates = context.get("duplicates", {})
    total_groups = duplicates.get("total_groups", 0)
    total_wasted = duplicates.get("total_wasted_bytes", 0)
    top_extensions = duplicates.get("top_extensions", [])

    if total_groups == 0 or total_wasted < 100 * 1024:  # <100KB not worth recommending
        return []

    recommendations = []
    wasted_gb = total_wasted / (1024 ** 3)

    title = f"Remove {total_groups} duplicate file groups"
    desc = (
        f"Found {total_groups} groups of identical files consuming "
        f"{_format_size(total_wasted)} of redundant storage."
    )
    reasoning = (
        f"SHA256-verified duplicate detection identified {total_groups} groups. "
        f"Most common duplicate types: {', '.join(top_extensions[:5]) if top_extensions else 'various'}. "
        f"Keeping one copy of each and removing the rest would recover {_format_size(total_wasted)}."
    )

    recommendations.append(Recommendation(
        category="duplicate_cleanup",
        priority=_compute_priority(total_wasted, "low"),
        title=title,
        description=desc,
        reasoning=reasoning,
        confidence=0.95,  # SHA256 verified — very high confidence
        risk_level="low",
        recoverable_bytes=total_wasted,
        affected_count=duplicates.get("total_duplicate_files", 0),
    ))

    return recommendations


def rule_stale_file_cleanup(context: dict[str, Any]) -> list[Recommendation]:
    """Generate recommendations from stale file analysis.

    Triggers when:
    - Significant storage in stale/archive-candidate files
    """
    stale = context.get("stale", {})
    classification = stale.get("classification", {})

    stale_bytes = classification.get("stale", {}).get("bytes", 0)
    archive_bytes = classification.get("archive_candidate", {}).get("bytes", 0)
    stale_count = classification.get("stale", {}).get("count", 0)
    archive_count = classification.get("archive_candidate", {}).get("count", 0)

    recommendations = []

    # Archive candidates (>1 year unused)
    if archive_bytes > 1 * 1024 * 1024:  # >1MB
        recommendations.append(Recommendation(
            category="archive",
            priority=_compute_priority(archive_bytes, "low"),
            title=f"Archive {archive_count} files unused for over 1 year",
            description=(
                f"{archive_count} files ({_format_size(archive_bytes)}) have not been "
                f"accessed in over 365 days and are strong candidates for archival or removal."
            ),
            reasoning=(
                f"These files show zero access activity for 1+ year. "
                f"Archiving them would recover {_format_size(archive_bytes)} "
                f"with minimal risk since they haven't been needed."
            ),
            confidence=0.88,
            risk_level="low",
            recoverable_bytes=archive_bytes,
            affected_count=archive_count,
        ))

    # Stale files (6-12 months unused)
    if stale_bytes > 5 * 1024 * 1024:  # >5MB
        recommendations.append(Recommendation(
            category="stale_cleanup",
            priority=_compute_priority(stale_bytes, "medium"),
            title=f"Review {stale_count} stale files for cleanup",
            description=(
                f"{stale_count} files ({_format_size(stale_bytes)}) have not been "
                f"accessed in 6-12 months. Review for potential removal or archival."
            ),
            reasoning=(
                f"Files inactive for 6-12 months are unlikely to be needed but "
                f"may contain important data. Manual review recommended before deletion."
            ),
            confidence=0.72,
            risk_level="medium",
            recoverable_bytes=stale_bytes,
            affected_count=stale_count,
        ))

    return recommendations


def rule_workspace_cleanup(context: dict[str, Any]) -> list[Recommendation]:
    """Generate recommendations from developer workspace analysis.

    Triggers when:
    - Inactive workspaces with significant recoverable space
    - Safe-to-delete artifacts (node_modules, __pycache__, etc.)
    """
    workspaces = context.get("workspaces", {})
    by_type = workspaces.get("by_type", {})
    inactive = workspaces.get("inactive_workspaces", 0)
    total_recoverable = workspaces.get("total_recoverable_bytes", 0)
    safe_recoverable = workspaces.get("safe_recoverable_bytes", 0)

    recommendations = []

    # Safe cleanup (regenerable artifacts)
    if safe_recoverable > 10 * 1024 * 1024:  # >10MB
        recommendations.append(Recommendation(
            category="workspace_cleanup",
            priority=_compute_priority(safe_recoverable, "low"),
            title="Remove regenerable developer artifacts",
            description=(
                f"Found {_format_size(safe_recoverable)} in safely removable artifacts "
                f"(node_modules, __pycache__, build outputs, .venv). "
                f"These can be regenerated with a single command."
            ),
            reasoning=(
                f"Developer build artifacts are fully regenerable from source. "
                f"Removing them frees {_format_size(safe_recoverable)} immediately. "
                f"Run `npm install`, `pip install`, or `gradle build` to restore when needed."
            ),
            confidence=0.97,
            risk_level="low",
            recoverable_bytes=safe_recoverable,
            affected_count=workspaces.get("total_workspaces", 0),
        ))

    # Abandoned workspaces
    if inactive >= 2:
        inactive_bytes = total_recoverable - safe_recoverable
        if inactive_bytes < 0:
            inactive_bytes = 0

        recommendations.append(Recommendation(
            category="workspace_cleanup",
            priority=_compute_priority(inactive_bytes, "medium") if inactive_bytes > 0 else "low",
            title=f"Review {inactive} abandoned developer projects",
            description=(
                f"{inactive} projects have not been modified in 6+ months. "
                f"Consider archiving or removing them."
            ),
            reasoning=(
                f"Inactive projects consume space and add clutter. "
                f"If they are no longer needed, archiving to a compressed "
                f"backup or removing them entirely would reduce workspace overhead."
            ),
            confidence=0.80,
            risk_level="medium",
            recoverable_bytes=inactive_bytes,
            affected_count=inactive,
        ))

    # Per-type recommendations for significant categories
    for ws_type, data in by_type.items():
        if data.get("recoverable_bytes", 0) > 100 * 1024 * 1024:  # >100MB per type
            recommendations.append(Recommendation(
                category="workspace_cleanup",
                priority="high",
                title=f"Clean {ws_type} workspace artifacts ({_format_size(data['recoverable_bytes'])})",
                description=(
                    f"{data['count']} {ws_type} workspace(s) contain "
                    f"{_format_size(data['recoverable_bytes'])} of recoverable artifacts."
                ),
                reasoning=(
                    f"The {ws_type} ecosystem accumulates significant cache and build "
                    f"artifacts over time. Periodic cleanup is recommended."
                ),
                confidence=0.90,
                risk_level="low" if ws_type in ("node", "python", "java", "rust") else "medium",
                recoverable_bytes=data["recoverable_bytes"],
                affected_count=data["count"],
            ))

    return recommendations


def rule_large_file_review(context: dict[str, Any]) -> list[Recommendation]:
    """Generate recommendations for unusually large files.

    Triggers when:
    - Files >1GB exist that may be unnecessary
    """
    largest_files = context.get("largest_files", [])

    # Find files >1GB
    large_files = [f for f in largest_files if f.get("size_bytes", 0) > 1024 ** 3]

    if not large_files:
        return []

    total_large = sum(f["size_bytes"] for f in large_files)

    return [Recommendation(
        category="large_file",
        priority=_compute_priority(total_large, "medium"),
        title=f"Review {len(large_files)} files over 1 GB",
        description=(
            f"Found {len(large_files)} files larger than 1 GB, "
            f"totaling {_format_size(total_large)}. "
            f"Review whether these are still needed."
        ),
        reasoning=(
            f"Large files are the fastest way to recover significant space. "
            f"Common offenders include old VM images, database dumps, "
            f"video recordings, and ML model checkpoints."
        ),
        confidence=0.65,
        risk_level="medium",
        recoverable_bytes=total_large,
        affected_count=len(large_files),
        affected_paths=[f.get("path", "") for f in large_files[:20]],
    )]


def rule_storage_growth_warning(context: dict[str, Any]) -> list[Recommendation]:
    """Generate warnings about storage growth trajectory.

    Triggers when:
    - Growth rate suggests disk exhaustion within 6 months
    """
    growth = context.get("growth", {})
    daily_growth = growth.get("daily_growth_bytes", 0)
    free_space = context.get("overview", {}).get("free_storage", 0)

    if daily_growth <= 0 or free_space <= 0:
        return []

    days_until_full = free_space / daily_growth
    if days_until_full > 180:  # More than 6 months — no warning
        return []

    priority = "critical" if days_until_full < 30 else "high" if days_until_full < 90 else "medium"
    monthly_growth = daily_growth * 30

    return [Recommendation(
        category="growth_warning",
        priority=priority,
        title=f"Disk may be full in {int(days_until_full)} days",
        description=(
            f"At the current growth rate of {_format_size(int(monthly_growth))}/month, "
            f"the remaining {_format_size(free_space)} of free space will be exhausted "
            f"in approximately {int(days_until_full)} days."
        ),
        reasoning=(
            f"Storage growth averaging {_format_size(int(daily_growth))}/day has been detected. "
            f"Proactive cleanup or additional storage is recommended to prevent disruption."
        ),
        confidence=0.70,
        risk_level="high" if days_until_full < 60 else "medium",
        recoverable_bytes=0,
        affected_count=0,
    )]


# ─── Engine Orchestrator ──────────────────────────────────────────────────────

# All rules in priority order
ALL_RULES = [
    rule_storage_growth_warning,
    rule_duplicate_cleanup,
    rule_workspace_cleanup,
    rule_stale_file_cleanup,
    rule_large_file_review,
]

# Priority ordering for sorting
PRIORITY_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3}


def generate_recommendations(context: dict[str, Any]) -> list[Recommendation]:
    """Run all rules against the analysis context and return sorted recommendations.

    Args:
        context: Aggregated analysis data from all phases. Expected keys:
            - duplicates: DuplicateSummary data
            - stale: StaleSummary data
            - workspaces: WorkspaceSummary data
            - overview: StorageOverview data
            - growth: GrowthHistory data
            - largest_files: List of largest file entries

    Returns:
        List of Recommendation objects sorted by priority then recoverable space.
    """
    recommendations: list[Recommendation] = []

    for rule_fn in ALL_RULES:
        try:
            results = rule_fn(context)
            recommendations.extend(results)
        except Exception as e:
            logger.warning(
                "recommendation_rule_failed",
                rule=rule_fn.__name__,
                error=str(e),
            )

    # Sort: priority first, then recoverable space descending
    recommendations.sort(
        key=lambda r: (PRIORITY_ORDER.get(r.priority, 99), -r.recoverable_bytes)
    )

    logger.info(
        "recommendations_generated",
        total=len(recommendations),
        by_priority={
            p: sum(1 for r in recommendations if r.priority == p)
            for p in ("critical", "high", "medium", "low")
        },
    )

    return recommendations


# ─── Utilities ─────────────────────────────────────────────────────────────────


def _format_size(bytes_val: int) -> str:
    """Format bytes into human-readable string."""
    if bytes_val >= 1024 ** 3:
        return f"{bytes_val / (1024 ** 3):.1f} GB"
    elif bytes_val >= 1024 ** 2:
        return f"{bytes_val / (1024 ** 2):.1f} MB"
    elif bytes_val >= 1024:
        return f"{bytes_val / 1024:.1f} KB"
    return f"{bytes_val} B"

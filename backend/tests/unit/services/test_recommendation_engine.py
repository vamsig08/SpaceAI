"""Unit tests for the deterministic rule-based recommendation engine."""

import pytest

from app.services.recommendation_engine import (
    Recommendation,
    _compute_priority,
    _format_size,
    generate_recommendations,
    rule_duplicate_cleanup,
    rule_large_file_review,
    rule_stale_file_cleanup,
    rule_storage_growth_warning,
    rule_workspace_cleanup,
)


class TestComputePriority:
    """Tests for priority determination."""

    def test_critical_for_large_low_risk(self) -> None:
        assert _compute_priority(60 * 1024**3, "low") == "critical"

    def test_high_for_large_any_risk(self) -> None:
        assert _compute_priority(6 * 1024**3, "medium") == "high"

    def test_high_for_moderate_low_risk(self) -> None:
        assert _compute_priority(2 * 1024**3, "low") == "high"

    def test_medium_for_moderate(self) -> None:
        assert _compute_priority(600 * 1024**2, "medium") == "medium"

    def test_low_for_small(self) -> None:
        assert _compute_priority(100 * 1024**2, "low") == "low"


class TestRuleDuplicateCleanup:
    """Tests for the duplicate cleanup rule."""

    def test_generates_recommendation_for_duplicates(self) -> None:
        ctx = {
            "duplicates": {
                "total_groups": 50,
                "total_duplicate_files": 200,
                "total_wasted_bytes": 5 * 1024**3,
                "top_extensions": [".jpg", ".pdf"],
            }
        }
        recs = rule_duplicate_cleanup(ctx)
        assert len(recs) == 1
        assert recs[0].category == "duplicate_cleanup"
        assert recs[0].confidence == 0.95
        assert recs[0].recoverable_bytes == 5 * 1024**3
        assert recs[0].priority == "high"

    def test_no_recommendation_when_no_duplicates(self) -> None:
        ctx = {"duplicates": {"total_groups": 0, "total_wasted_bytes": 0}}
        assert rule_duplicate_cleanup(ctx) == []

    def test_no_recommendation_for_tiny_waste(self) -> None:
        ctx = {"duplicates": {"total_groups": 2, "total_wasted_bytes": 50}}
        assert rule_duplicate_cleanup(ctx) == []

    def test_missing_context_returns_empty(self) -> None:
        assert rule_duplicate_cleanup({}) == []


class TestRuleStaleFileCleanup:
    """Tests for the stale/archive file rules."""

    def test_generates_archive_recommendation(self) -> None:
        ctx = {
            "stale": {
                "classification": {
                    "stale": {"bytes": 100 * 1024**2, "count": 50},
                    "archive_candidate": {"bytes": 2 * 1024**3, "count": 200},
                }
            }
        }
        recs = rule_stale_file_cleanup(ctx)
        archive_recs = [r for r in recs if r.category == "archive"]
        assert len(archive_recs) == 1
        assert archive_recs[0].recoverable_bytes == 2 * 1024**3
        assert archive_recs[0].confidence == 0.88

    def test_generates_stale_recommendation(self) -> None:
        ctx = {
            "stale": {
                "classification": {
                    "stale": {"bytes": 500 * 1024**2, "count": 1000},
                    "archive_candidate": {"bytes": 5 * 1024**2, "count": 10},
                }
            }
        }
        recs = rule_stale_file_cleanup(ctx)
        stale_recs = [r for r in recs if r.category == "stale_cleanup"]
        assert len(stale_recs) == 1
        assert stale_recs[0].risk_level == "medium"

    def test_no_recommendation_below_threshold(self) -> None:
        ctx = {
            "stale": {
                "classification": {
                    "stale": {"bytes": 1000, "count": 2},
                    "archive_candidate": {"bytes": 500, "count": 1},
                }
            }
        }
        assert rule_stale_file_cleanup(ctx) == []


class TestRuleWorkspaceCleanup:
    """Tests for workspace cleanup rules."""

    def test_generates_safe_cleanup_recommendation(self) -> None:
        ctx = {
            "workspaces": {
                "by_type": {"node": {"count": 5, "recoverable_bytes": 2 * 1024**3, "safe_recoverable_bytes": 2 * 1024**3}},
                "total_workspaces": 5,
                "total_recoverable_bytes": 2 * 1024**3,
                "safe_recoverable_bytes": 50 * 1024**2,
                "inactive_workspaces": 0,
            }
        }
        recs = rule_workspace_cleanup(ctx)
        safe_recs = [r for r in recs if r.confidence > 0.95]
        assert len(safe_recs) >= 1
        assert safe_recs[0].risk_level == "low"

    def test_no_recommendation_below_threshold(self) -> None:
        ctx = {
            "workspaces": {
                "by_type": {},
                "total_workspaces": 1,
                "total_recoverable_bytes": 1000,
                "safe_recoverable_bytes": 500,  # Below 10MB threshold
                "inactive_workspaces": 0,
            }
        }
        recs = rule_workspace_cleanup(ctx)
        safe_recs = [r for r in recs if r.confidence > 0.95]
        assert len(safe_recs) == 0

    def test_generates_abandoned_recommendation(self) -> None:
        ctx = {
            "workspaces": {
                "by_type": {},
                "total_workspaces": 10,
                "total_recoverable_bytes": 1024**3,
                "safe_recoverable_bytes": 50 * 1024**2,
                "inactive_workspaces": 5,
            }
        }
        recs = rule_workspace_cleanup(ctx)
        abandoned_recs = [r for r in recs if "abandoned" in r.title.lower()]
        assert len(abandoned_recs) == 1
        assert abandoned_recs[0].affected_count == 5

    def test_generates_per_type_for_large_categories(self) -> None:
        ctx = {
            "workspaces": {
                "by_type": {
                    "node": {"count": 10, "recoverable_bytes": 5 * 1024**3, "safe_recoverable_bytes": 5 * 1024**3},
                    "python": {"count": 3, "recoverable_bytes": 100 * 1024**2, "safe_recoverable_bytes": 100 * 1024**2},
                },
                "total_workspaces": 13,
                "total_recoverable_bytes": 5 * 1024**3 + 100 * 1024**2,
                "safe_recoverable_bytes": 100 * 1024**2,
                "inactive_workspaces": 0,
            }
        }
        recs = rule_workspace_cleanup(ctx)
        node_recs = [r for r in recs if "node" in r.title.lower()]
        assert len(node_recs) >= 1

    def test_empty_workspaces_returns_empty(self) -> None:
        ctx = {
            "workspaces": {
                "by_type": {},
                "total_workspaces": 0,
                "total_recoverable_bytes": 0,
                "safe_recoverable_bytes": 0,
                "inactive_workspaces": 0,
            }
        }
        assert rule_workspace_cleanup(ctx) == []


class TestRuleLargeFileReview:
    """Tests for large file review rule."""

    def test_generates_for_files_over_1gb(self) -> None:
        ctx = {
            "largest_files": [
                {"path": "/data/vm.img", "size_bytes": 10 * 1024**3, "category": "data"},
                {"path": "/data/model.pt", "size_bytes": 5 * 1024**3, "category": "data"},
                {"path": "/docs/small.pdf", "size_bytes": 50 * 1024**2, "category": "document"},
            ]
        }
        recs = rule_large_file_review(ctx)
        assert len(recs) == 1
        assert recs[0].affected_count == 2
        assert recs[0].recoverable_bytes == 15 * 1024**3

    def test_no_recommendation_when_all_small(self) -> None:
        ctx = {"largest_files": [{"path": "/a.txt", "size_bytes": 1000, "category": "document"}]}
        assert rule_large_file_review(ctx) == []

    def test_empty_largest_returns_empty(self) -> None:
        assert rule_large_file_review({}) == []


class TestRuleStorageGrowthWarning:
    """Tests for storage growth warning rule."""

    def test_warns_when_disk_filling_fast(self) -> None:
        ctx = {
            "growth": {"daily_growth_bytes": 1024**3},  # 1GB/day
            "overview": {"free_storage": 50 * 1024**3},  # 50GB free
        }
        recs = rule_storage_growth_warning(ctx)
        assert len(recs) == 1
        assert recs[0].category == "growth_warning"
        assert recs[0].priority == "high"  # ~50 days

    def test_critical_when_very_fast(self) -> None:
        ctx = {
            "growth": {"daily_growth_bytes": 5 * 1024**3},
            "overview": {"free_storage": 100 * 1024**3},  # 20 days
        }
        recs = rule_storage_growth_warning(ctx)
        assert len(recs) == 1
        assert recs[0].priority == "critical"

    def test_no_warning_with_plenty_of_space(self) -> None:
        ctx = {
            "growth": {"daily_growth_bytes": 100 * 1024**2},
            "overview": {"free_storage": 500 * 1024**3},  # 5000 days
        }
        assert rule_storage_growth_warning(ctx) == []

    def test_no_warning_with_zero_growth(self) -> None:
        ctx = {"growth": {"daily_growth_bytes": 0}, "overview": {"free_storage": 100 * 1024**3}}
        assert rule_storage_growth_warning(ctx) == []


class TestGenerateRecommendations:
    """Tests for the full rule engine orchestrator."""

    def test_runs_all_rules_and_sorts(self) -> None:
        ctx = {
            "duplicates": {"total_groups": 10, "total_duplicate_files": 30, "total_wasted_bytes": 2 * 1024**3, "top_extensions": [".jpg"]},
            "stale": {"classification": {"stale": {"bytes": 100 * 1024**2, "count": 50}, "archive_candidate": {"bytes": 1 * 1024**3, "count": 100}}},
            "workspaces": {"by_type": {}, "total_workspaces": 3, "total_recoverable_bytes": 500 * 1024**2, "safe_recoverable_bytes": 400 * 1024**2, "inactive_workspaces": 2},
            "overview": {"free_storage": 0},
            "growth": {"daily_growth_bytes": 0},
            "largest_files": [],
        }
        recs = generate_recommendations(ctx)
        assert len(recs) >= 3  # duplicates + archive + workspace
        # Verify sorted by priority
        priorities = [r.priority for r in recs]
        order = ["critical", "high", "medium", "low"]
        assert all(
            order.index(priorities[i]) <= order.index(priorities[i + 1])
            for i in range(len(priorities) - 1)
        )

    def test_empty_context_returns_empty(self) -> None:
        recs = generate_recommendations({})
        assert recs == []

    def test_handles_rule_failure_gracefully(self) -> None:
        # Even with weird data, should not crash
        ctx = {"duplicates": "not_a_dict", "stale": None, "workspaces": 123}
        recs = generate_recommendations(ctx)
        # Should still return (possibly empty) without raising
        assert isinstance(recs, list)


class TestFormatSize:
    """Tests for human-readable size formatting."""

    def test_bytes(self) -> None:
        assert _format_size(500) == "500 B"

    def test_kilobytes(self) -> None:
        assert _format_size(2048) == "2.0 KB"

    def test_megabytes(self) -> None:
        assert _format_size(50 * 1024**2) == "50.0 MB"

    def test_gigabytes(self) -> None:
        assert _format_size(3 * 1024**3) == "3.0 GB"

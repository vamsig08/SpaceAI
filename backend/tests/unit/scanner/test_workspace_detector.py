"""Unit tests for the workspace detection engine."""

from datetime import datetime, timedelta, timezone

import pytest

from app.scanner.workspace_detector import (
    WorkspaceResult,
    _normalize_project_name,
    detect_abandoned_projects,
    detect_duplicate_projects,
    detect_workspaces_from_files,
)


def _days_ago(days: int) -> str:
    dt = datetime.now(timezone.utc) - timedelta(days=days)
    return dt.strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _make_records(paths_with_sizes: list[tuple[str, int]], modified_at: str | None = None) -> list[tuple[str, str, int, str | None]]:
    """Helper to build file records for testing."""
    mod = modified_at or _days_ago(10)
    return [(f"id-{i}", path, size, mod) for i, (path, size) in enumerate(paths_with_sizes)]


class TestDetectWorkspacesFromFiles:
    """Tests for the core workspace detection function."""

    def test_detects_node_modules(self) -> None:
        records = _make_records([
            ("/home/dev/webapp/node_modules/react/index.js", 5000),
            ("/home/dev/webapp/node_modules/lodash/lodash.js", 8000),
            ("/home/dev/webapp/src/app.ts", 1000),
        ])
        results = detect_workspaces_from_files(records)

        node_ws = [r for r in results if r.workspace_type == "node"]
        assert len(node_ws) >= 1
        assert node_ws[0].recoverable_bytes == 13000

    def test_detects_python_venv(self) -> None:
        records = _make_records([
            ("/home/dev/api/.venv/lib/python3.12/site-packages/flask.py", 20000),
            ("/home/dev/api/.venv/bin/python", 5000),
            ("/home/dev/api/app.py", 2000),
        ])
        results = detect_workspaces_from_files(records)

        python_ws = [r for r in results if r.workspace_type == "python"]
        assert len(python_ws) >= 1
        assert python_ws[0].recoverable_bytes >= 25000

    def test_detects_pycache(self) -> None:
        records = _make_records([
            ("/home/dev/project/__pycache__/module.cpython-312.pyc", 3000),
            ("/home/dev/project/src/__pycache__/utils.cpython-312.pyc", 2000),
        ])
        results = detect_workspaces_from_files(records)

        python_ws = [r for r in results if r.workspace_type == "python"]
        assert len(python_ws) >= 1
        total_recoverable = sum(w.recoverable_bytes for w in python_ws)
        assert total_recoverable == 5000

    def test_detects_java_target(self) -> None:
        records = _make_records([
            ("/home/dev/java-app/target/classes/Main.class", 4000),
            ("/home/dev/java-app/target/test-classes/Test.class", 3000),
            ("/home/dev/java-app/.gradle/caches/data.bin", 10000),
        ])
        results = detect_workspaces_from_files(records)

        java_ws = [r for r in results if r.workspace_type == "java"]
        assert len(java_ws) >= 1

    def test_detects_ml_models_by_extension(self) -> None:
        records = _make_records([
            ("/home/dev/ml-project/models/model.pt", 500000000),
            ("/home/dev/ml-project/models/checkpoint.ckpt", 300000000),
            ("/home/dev/ml-project/train.py", 5000),
        ])
        results = detect_workspaces_from_files(records)

        ml_ws = [r for r in results if r.workspace_type == "ml"]
        assert len(ml_ws) >= 1
        assert ml_ws[0].recoverable_bytes == 800000000

    def test_detects_ide_artifacts(self) -> None:
        records = _make_records([
            ("/home/dev/project/.idea/workspace.xml", 5000),
            ("/home/dev/project/.idea/misc.xml", 2000),
        ])
        results = detect_workspaces_from_files(records)

        ide_ws = [r for r in results if r.workspace_type == "ide"]
        assert len(ide_ws) >= 1

    def test_detects_rust_target(self) -> None:
        records = _make_records([
            ("/home/dev/rust-app/target/debug/app", 50000),
            ("/home/dev/rust-app/target/release/app", 40000),
        ])
        results = detect_workspaces_from_files(records)

        rust_ws = [r for r in results if r.workspace_type == "rust"]
        assert len(rust_ws) >= 1
        assert rust_ws[0].recoverable_bytes == 90000

    def test_detects_multiple_workspace_types(self) -> None:
        records = _make_records([
            ("/home/dev/webapp/node_modules/react/index.js", 5000),
            ("/home/dev/api/.venv/lib/flask.py", 10000),
            ("/home/dev/ml/models/model.pt", 500000),
        ])
        results = detect_workspaces_from_files(records)

        types = {r.workspace_type for r in results}
        assert "node" in types
        assert "python" in types
        assert "ml" in types

    def test_safe_recoverable_is_subset_of_recoverable(self) -> None:
        records = _make_records([
            ("/home/dev/app/node_modules/pkg/index.js", 10000),
            ("/home/dev/app/.next/cache/data.json", 5000),
        ])
        results = detect_workspaces_from_files(records)

        for ws in results:
            assert ws.safe_recoverable_bytes <= ws.recoverable_bytes

    def test_empty_input_returns_empty(self) -> None:
        results = detect_workspaces_from_files([])
        assert results == []

    def test_no_artifacts_returns_empty(self) -> None:
        records = _make_records([
            ("/home/dev/project/src/main.py", 1000),
            ("/home/dev/project/README.md", 500),
        ])
        results = detect_workspaces_from_files(records)
        assert results == []

    def test_results_sorted_by_recoverable_desc(self) -> None:
        records = _make_records([
            ("/home/dev/small/node_modules/a.js", 100),
            ("/home/dev/big/.venv/lib/big.so", 999999),
        ])
        results = detect_workspaces_from_files(records)

        if len(results) >= 2:
            assert results[0].recoverable_bytes >= results[1].recoverable_bytes


class TestDetectAbandonedProjects:
    """Tests for abandoned project detection."""

    def test_old_project_marked_abandoned(self) -> None:
        ws = [
            WorkspaceResult(
                path="/home/dev/old-project",
                name="old-project",
                workspace_type="node",
                last_modified_at=_days_ago(200),
            ),
        ]
        abandoned = detect_abandoned_projects(ws, inactive_threshold_days=180)

        assert len(abandoned) == 1
        assert ws[0].is_active is False
        assert ws[0].days_inactive is not None
        assert ws[0].days_inactive >= 200

    def test_recent_project_not_abandoned(self) -> None:
        ws = [
            WorkspaceResult(
                path="/home/dev/active",
                name="active",
                workspace_type="python",
                last_modified_at=_days_ago(10),
            ),
        ]
        abandoned = detect_abandoned_projects(ws, inactive_threshold_days=180)

        assert len(abandoned) == 0
        assert ws[0].is_active is True

    def test_no_modified_at_treated_as_abandoned(self) -> None:
        ws = [
            WorkspaceResult(
                path="/home/dev/unknown",
                name="unknown",
                workspace_type="java",
                last_modified_at=None,
            ),
        ]
        abandoned = detect_abandoned_projects(ws, inactive_threshold_days=180)

        assert len(abandoned) == 1
        assert ws[0].days_inactive == 9999

    def test_custom_threshold(self) -> None:
        ws = [
            WorkspaceResult(
                path="/home/dev/project",
                name="project",
                workspace_type="node",
                last_modified_at=_days_ago(100),
            ),
        ]
        # With 90-day threshold, this is abandoned
        abandoned = detect_abandoned_projects(ws, inactive_threshold_days=90)
        assert len(abandoned) == 1

        # Reset and test with 180-day threshold — not abandoned
        ws[0].is_active = True
        abandoned = detect_abandoned_projects(ws, inactive_threshold_days=180)
        assert len(abandoned) == 0


class TestDetectDuplicateProjects:
    """Tests for duplicate/backup project detection."""

    def test_detects_copy_suffix(self) -> None:
        ws = [
            WorkspaceResult(path="/dev/project", name="project", workspace_type="node"),
            WorkspaceResult(path="/dev/project-copy", name="project-copy", workspace_type="node"),
        ]
        groups = detect_duplicate_projects(ws)
        assert len(groups) == 1
        assert len(groups[0]) == 2

    def test_detects_backup_suffix(self) -> None:
        ws = [
            WorkspaceResult(path="/dev/app", name="app", workspace_type="python"),
            WorkspaceResult(path="/dev/app-backup", name="app-backup", workspace_type="python"),
            WorkspaceResult(path="/dev/app-old", name="app-old", workspace_type="python"),
        ]
        groups = detect_duplicate_projects(ws)
        assert len(groups) == 1
        assert len(groups[0]) == 3

    def test_detects_version_suffix(self) -> None:
        ws = [
            WorkspaceResult(path="/dev/project", name="project", workspace_type="node"),
            WorkspaceResult(path="/dev/project-v2", name="project-v2", workspace_type="node"),
            WorkspaceResult(path="/dev/project-final", name="project-final", workspace_type="node"),
        ]
        groups = detect_duplicate_projects(ws)
        assert len(groups) == 1

    def test_unrelated_projects_no_groups(self) -> None:
        ws = [
            WorkspaceResult(path="/dev/webapp", name="webapp", workspace_type="node"),
            WorkspaceResult(path="/dev/api-server", name="api-server", workspace_type="python"),
            WorkspaceResult(path="/dev/ml-pipeline", name="ml-pipeline", workspace_type="ml"),
        ]
        groups = detect_duplicate_projects(ws)
        assert len(groups) == 0

    def test_empty_input(self) -> None:
        groups = detect_duplicate_projects([])
        assert groups == []


class TestNormalizeProjectName:
    """Tests for project name normalization."""

    def test_strips_copy_suffix(self) -> None:
        assert _normalize_project_name("project-copy") == "project"

    def test_strips_backup_suffix(self) -> None:
        assert _normalize_project_name("project-backup") == "project"

    def test_strips_version_suffix(self) -> None:
        assert _normalize_project_name("project-v2") == "project"
        assert _normalize_project_name("project-v10") == "project"

    def test_strips_final_suffix(self) -> None:
        assert _normalize_project_name("project-final") == "project"

    def test_strips_old_suffix(self) -> None:
        assert _normalize_project_name("project-old") == "project"

    def test_preserves_normal_names(self) -> None:
        assert _normalize_project_name("my-webapp") == "my-webapp"
        assert _normalize_project_name("api-server") == "api-server"

    def test_strips_trailing_numbers(self) -> None:
        assert _normalize_project_name("project-2") == "project"

    def test_short_names_return_empty(self) -> None:
        assert _normalize_project_name("a") == ""

    def test_case_insensitive(self) -> None:
        assert _normalize_project_name("Project-BACKUP") == "project"

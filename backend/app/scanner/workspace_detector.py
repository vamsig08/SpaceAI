"""Developer workspace detection engine.

Identifies developer-specific storage artifacts by analyzing file paths
in the scanned filesystem. Each detector returns structured findings
about a specific workspace type with recovery estimates.

Supported workspace types:
  - Python: .venv, venv, __pycache__, pip/poetry cache
  - Node: node_modules, npm/yarn/pnpm cache
  - Java: target, build, .gradle
  - Docker: images, containers, build cache, volumes
  - Machine Learning: .pt, .pth, .ckpt, .onnx, HuggingFace/TF cache
  - IDE: .idea, .vscode caches, IntelliJ caches
  - Rust: target (Cargo)
  - Go: pkg/mod cache

Architecture:
  - Each detector is a function that takes file records and returns WorkspaceFindings
  - Detectors operate on path patterns (no filesystem access needed — uses DB data)
  - Results are written to the dev_workspaces table
"""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any


@dataclass
class ArtifactMatch:
    """A single matched artifact within a workspace."""

    artifact_type: str       # e.g. "node_modules", "__pycache__", ".venv"
    root_path: str           # The workspace root containing this artifact
    total_bytes: int = 0
    file_count: int = 0
    risk_level: str = "low"  # low|medium|high


@dataclass
class WorkspaceResult:
    """Detection result for a single developer workspace."""

    path: str                   # Workspace root directory
    name: str                   # Human-readable workspace name
    workspace_type: str         # python|node|java|docker|ml|ide|rust|go
    total_size_bytes: int = 0
    recoverable_bytes: int = 0
    safe_recoverable_bytes: int = 0  # Subset that's definitely safe to delete
    last_modified_at: str | None = None
    days_inactive: int | None = None
    is_active: bool = True
    risk_level: str = "low"
    artifacts: list[dict[str, Any]] = field(default_factory=list)


# ─── Detection Patterns ───────────────────────────────────────────────────────

# Patterns that identify workspace root directories
# Each entry: (pattern_in_path, workspace_type, artifact_type, risk, is_safe_to_delete)

ARTIFACT_PATTERNS: list[tuple[str, str, str, str, bool]] = [
    # Python
    ("/.venv/", "python", ".venv", "low", True),
    ("/venv/", "python", "venv", "low", True),
    ("/__pycache__/", "python", "__pycache__", "low", True),
    ("/.tox/", "python", ".tox", "low", True),
    ("/.mypy_cache/", "python", ".mypy_cache", "low", True),
    ("/.pytest_cache/", "python", ".pytest_cache", "low", True),
    ("/.ruff_cache/", "python", ".ruff_cache", "low", True),
    ("/site-packages/", "python", "site-packages", "low", True),

    # Node
    ("/node_modules/", "node", "node_modules", "low", True),
    ("/.next/", "node", ".next", "low", True),
    ("/.nuxt/", "node", ".nuxt", "low", True),
    ("/bower_components/", "node", "bower_components", "low", True),

    # Java / JVM
    ("/target/classes/", "java", "target", "low", True),
    ("/target/test-classes/", "java", "target", "low", True),
    ("/.gradle/", "java", ".gradle", "low", True),
    ("/build/classes/", "java", "build", "low", True),
    ("/build/libs/", "java", "build", "low", True),

    # Rust
    ("/target/debug/", "rust", "cargo_target", "low", True),
    ("/target/release/", "rust", "cargo_target", "low", True),

    # Docker (path-based indicators in cache directories)
    ("/.docker/", "docker", "docker_cache", "medium", False),
    ("/docker/volumes/", "docker", "docker_volumes", "medium", False),

    # Machine Learning
    ("/.cache/huggingface/", "ml", "huggingface_cache", "medium", False),
    ("/.cache/torch/", "ml", "torch_cache", "medium", False),
    ("/checkpoints/", "ml", "checkpoints", "medium", False),

    # IDE
    ("/.idea/", "ide", ".idea", "low", True),
    ("/.vscode/", "ide", ".vscode", "low", True),
    ("/.vs/", "ide", ".vs", "low", True),

    # General build/dist
    ("/dist/", "node", "dist", "medium", False),
    ("/coverage/", "node", "coverage", "low", True),
    ("/htmlcov/", "python", "htmlcov", "low", True),
]

# File extension patterns for ML model detection
ML_MODEL_EXTENSIONS = {".pt", ".pth", ".ckpt", ".onnx", ".safetensors", ".h5", ".pb"}


def detect_workspaces_from_files(
    file_records: list[tuple[str, str, int, str | None]],
) -> list[WorkspaceResult]:
    """Analyze file records to detect developer workspaces.

    This is the core detection engine. It processes file paths to identify
    workspace artifacts, groups them by inferred workspace root, and
    computes recovery estimates.

    Args:
        file_records: List of (file_id, path, size_bytes, modified_at) tuples.

    Returns:
        List of WorkspaceResult objects, one per detected workspace.
    """
    # Track artifacts by workspace root
    workspace_artifacts: dict[str, list[dict[str, Any]]] = defaultdict(list)
    workspace_types: dict[str, str] = {}
    workspace_sizes: dict[str, int] = defaultdict(int)
    workspace_recoverable: dict[str, int] = defaultdict(int)
    workspace_safe_recoverable: dict[str, int] = defaultdict(int)
    workspace_last_modified: dict[str, str] = {}
    workspace_file_counts: dict[str, int] = defaultdict(int)

    # ML model detection (by extension)
    ml_model_files: dict[str, list[dict[str, Any]]] = defaultdict(list)

    for file_id, path, size_bytes, modified_at in file_records:
        matched = False

        # Check path against artifact patterns
        for pattern, ws_type, artifact_type, risk, is_safe in ARTIFACT_PATTERNS:
            if pattern in path:
                # Infer workspace root: everything before the artifact pattern
                idx = path.find(pattern)
                ws_root = path[:idx]

                if not ws_root or ws_root == "/":
                    continue

                workspace_types[ws_root] = ws_type
                workspace_sizes[ws_root] += size_bytes
                workspace_recoverable[ws_root] += size_bytes
                workspace_file_counts[ws_root] += 1

                if is_safe:
                    workspace_safe_recoverable[ws_root] += size_bytes

                # Track artifact detail
                workspace_artifacts[ws_root].append({
                    "type": artifact_type,
                    "size": size_bytes,
                    "path": path,
                })

                if modified_at:
                    existing = workspace_last_modified.get(ws_root)
                    if not existing or modified_at > existing:
                        workspace_last_modified[ws_root] = modified_at

                matched = True
                break

        # ML model detection by extension
        if not matched:
            ext = _get_extension(path)
            if ext in ML_MODEL_EXTENSIONS:
                # Group by parent directory
                parent = "/".join(path.split("/")[:-1])
                ml_model_files[parent].append({
                    "path": path,
                    "size": size_bytes,
                    "modified_at": modified_at,
                })

    # Convert ML model collections into workspace results
    for parent_dir, models in ml_model_files.items():
        if len(models) >= 1:
            total_size = sum(m["size"] for m in models)
            workspace_types[parent_dir] = "ml"
            workspace_sizes[parent_dir] = workspace_sizes.get(parent_dir, 0) + total_size
            workspace_recoverable[parent_dir] = workspace_recoverable.get(parent_dir, 0) + total_size
            workspace_file_counts[parent_dir] = workspace_file_counts.get(parent_dir, 0) + len(models)
            workspace_artifacts[parent_dir].extend([
                {"type": "ml_model", "size": m["size"], "path": m["path"]}
                for m in models
            ])
            latest = max((m["modified_at"] for m in models if m["modified_at"]), default=None)
            if latest:
                workspace_last_modified[parent_dir] = latest

    # Build results
    results: list[WorkspaceResult] = []
    for ws_root, ws_type in workspace_types.items():
        # Aggregate artifacts by type for this workspace
        artifact_summary = _aggregate_artifacts(workspace_artifacts[ws_root])

        # Determine overall risk (highest among artifacts)
        risk = _compute_workspace_risk(artifact_summary, ws_type)

        name = ws_root.split("/")[-1] if "/" in ws_root else ws_root

        results.append(WorkspaceResult(
            path=ws_root,
            name=name,
            workspace_type=ws_type,
            total_size_bytes=workspace_sizes[ws_root],
            recoverable_bytes=workspace_recoverable[ws_root],
            safe_recoverable_bytes=workspace_safe_recoverable.get(ws_root, 0),
            last_modified_at=workspace_last_modified.get(ws_root),
            risk_level=risk,
            artifacts=artifact_summary,
        ))

    # Sort by recoverable bytes descending
    results.sort(key=lambda r: r.recoverable_bytes, reverse=True)
    return results


def detect_abandoned_projects(
    workspace_results: list[WorkspaceResult],
    inactive_threshold_days: int = 180,
) -> list[WorkspaceResult]:
    """Identify abandoned projects from workspace results.

    A project is considered abandoned if its most recent file modification
    exceeds the threshold.

    Args:
        workspace_results: Already-detected workspaces with last_modified_at.
        inactive_threshold_days: Days of inactivity to consider abandoned.

    Returns:
        Subset of workspaces classified as abandoned.
    """
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc)
    abandoned = []

    for ws in workspace_results:
        if not ws.last_modified_at:
            ws.is_active = False
            ws.days_inactive = 9999
            abandoned.append(ws)
            continue

        try:
            dt = datetime.fromisoformat(ws.last_modified_at.replace("Z", "+00:00"))
            days = (now - dt).days
            if days >= inactive_threshold_days:
                ws.is_active = False
                ws.days_inactive = days
                abandoned.append(ws)
            else:
                ws.is_active = True
                ws.days_inactive = days
        except (ValueError, TypeError):
            ws.is_active = False
            ws.days_inactive = 9999
            abandoned.append(ws)

    return abandoned


def detect_duplicate_projects(
    workspace_results: list[WorkspaceResult],
) -> list[list[WorkspaceResult]]:
    """Detect likely duplicate/backup projects by name similarity.

    Identifies patterns like:
      project, project-copy, project-final, project-v2, project-backup

    Args:
        workspace_results: Detected workspaces.

    Returns:
        List of groups where each group contains likely duplicates.
    """
    # Normalize names for comparison
    normalized: dict[str, list[WorkspaceResult]] = defaultdict(list)

    for ws in workspace_results:
        base_name = _normalize_project_name(ws.name)
        if base_name:
            normalized[base_name].append(ws)

    # Return groups with 2+ entries (potential duplicates)
    return [group for group in normalized.values() if len(group) >= 2]


# ─── Helper Functions ──────────────────────────────────────────────────────────


def _get_extension(path: str) -> str:
    """Extract lowercase file extension from a path."""
    parts = path.rsplit(".", 1)
    if len(parts) == 2 and "/" not in parts[1]:
        return "." + parts[1].lower()
    return ""


def _aggregate_artifacts(
    artifacts: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Aggregate raw artifact matches by type.

    Args:
        artifacts: Raw list of {type, size, path} dicts.

    Returns:
        Aggregated list of {type, total_bytes, file_count} dicts.
    """
    by_type: dict[str, dict[str, int]] = {}
    for a in artifacts:
        atype = a["type"]
        if atype not in by_type:
            by_type[atype] = {"total_bytes": 0, "file_count": 0}
        by_type[atype]["total_bytes"] += a["size"]
        by_type[atype]["file_count"] += 1

    return [
        {"type": atype, "total_bytes": data["total_bytes"], "file_count": data["file_count"]}
        for atype, data in sorted(by_type.items(), key=lambda x: -x[1]["total_bytes"])
    ]


def _compute_workspace_risk(
    artifacts: list[dict[str, Any]], workspace_type: str
) -> str:
    """Compute overall workspace risk level.

    Docker and ML workspaces are medium risk (may contain unique data).
    Everything else is low risk (regenerable from source).
    """
    if workspace_type in ("docker", "ml"):
        return "medium"
    return "low"


def _normalize_project_name(name: str) -> str:
    """Normalize a project directory name for duplicate comparison.

    Strips common suffixes like -copy, -final, -v2, -backup, -old.

    Args:
        name: Raw directory name.

    Returns:
        Normalized base name, or empty string if name is too short.
    """
    if len(name) < 2:
        return ""

    # Remove common duplicate suffixes
    patterns = [
        r"[-_\s]*(copy|backup|bak|old|archive|final|v\d+|ver\d+)[-_\s]*\d*$",
        r"[-_\s]+\d+$",  # trailing numbers like project-2
        r"\s*\(\d+\)$",   # macOS copy pattern: file (2)
    ]

    normalized = name.lower().strip()
    for pattern in patterns:
        normalized = re.sub(pattern, "", normalized, flags=re.IGNORECASE)

    return normalized.strip("-_ ")

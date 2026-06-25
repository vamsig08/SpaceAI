"""Add tables for phases 2-9: snapshots, duplicates, workspaces, recommendations,
predictions, cleanup, and audit logs.

Created before any production data exists to avoid expensive ALTER TABLE
operations on the 1M-row files table in future phases.

Revision ID: 002
Revises: 001
Create Date: 2026-06-23
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "002"
down_revision: Union[str, None] = "001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── STORAGE SNAPSHOTS (Phase 2: Analytics) ─────────────────────────────
    op.create_table(
        "storage_snapshots",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "scan_id",
            sa.String(36),
            sa.ForeignKey("scans.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("snapshot_date", sa.String(10), nullable=False),
        sa.Column("total_size_bytes", sa.Integer, nullable=False),
        sa.Column("used_size_bytes", sa.Integer, nullable=False),
        sa.Column("file_count", sa.Integer, nullable=False),
        sa.Column("dir_count", sa.Integer, nullable=False),
        sa.Column("category_breakdown", sa.String, nullable=False),
        sa.Column("extension_breakdown", sa.String, nullable=True),
        sa.Column("largest_files", sa.String, nullable=True),
        sa.Column("largest_dirs", sa.String, nullable=True),
        sa.Column("created_at", sa.String, nullable=False),
    )
    op.create_index(
        "idx_snapshots_date", "storage_snapshots", ["snapshot_date"], unique=True
    )
    op.create_index("idx_snapshots_scan", "storage_snapshots", ["scan_id"])

    # ── DUPLICATE GROUPS (Phase 3: Duplicate Detection) ────────────────────
    op.create_table(
        "duplicate_groups",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "scan_id",
            sa.String(36),
            sa.ForeignKey("scans.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("sha256_hash", sa.String(64), nullable=False),
        sa.Column("file_size_bytes", sa.Integer, nullable=False),
        sa.Column("member_count", sa.Integer, nullable=False),
        sa.Column("wasted_bytes", sa.Integer, nullable=False),
        sa.Column(
            "status", sa.String(20), nullable=False, server_default="unresolved"
        ),
        sa.Column("created_at", sa.String, nullable=False),
    )
    op.create_index("idx_dup_groups_scan", "duplicate_groups", ["scan_id"])
    op.create_index(
        "idx_dup_groups_wasted", "duplicate_groups", ["wasted_bytes"]
    )
    op.create_index("idx_dup_groups_hash", "duplicate_groups", ["sha256_hash"])

    # ── DUPLICATE MEMBERS ──────────────────────────────────────────────────
    op.create_table(
        "duplicate_members",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "group_id",
            sa.String(36),
            sa.ForeignKey("duplicate_groups.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "file_id",
            sa.String(36),
            sa.ForeignKey("files.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("path", sa.String, nullable=False),
        sa.Column("is_keeper", sa.Integer, nullable=False, server_default="0"),
        sa.Column("created_at", sa.String, nullable=False),
    )
    op.create_index("idx_dup_members_group", "duplicate_members", ["group_id"])
    op.create_index("idx_dup_members_file", "duplicate_members", ["file_id"])

    # ── DEV WORKSPACES (Phase 5: Developer Workspace Optimizer) ────────────
    op.create_table(
        "dev_workspaces",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "scan_id",
            sa.String(36),
            sa.ForeignKey("scans.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("path", sa.String, nullable=False),
        sa.Column("name", sa.String, nullable=False),
        sa.Column("workspace_type", sa.String(20), nullable=False),
        sa.Column("total_size_bytes", sa.Integer, nullable=False),
        sa.Column("recoverable_bytes", sa.Integer, nullable=False, server_default="0"),
        sa.Column(
            "safe_recoverable_bytes", sa.Integer, nullable=False, server_default="0"
        ),
        sa.Column("last_modified_at", sa.String, nullable=True),
        sa.Column("is_active", sa.Integer, nullable=False, server_default="1"),
        sa.Column("days_inactive", sa.Integer, nullable=True),
        sa.Column("risk_level", sa.String(10), nullable=False, server_default="low"),
        sa.Column("artifacts", sa.String, nullable=False),
        sa.Column("created_at", sa.String, nullable=False),
    )
    op.create_index("idx_workspaces_scan", "dev_workspaces", ["scan_id"])
    op.create_index("idx_workspaces_type", "dev_workspaces", ["workspace_type"])
    op.create_index(
        "idx_workspaces_size", "dev_workspaces", ["total_size_bytes"]
    )
    op.create_index(
        "idx_workspaces_inactive", "dev_workspaces", ["is_active", "days_inactive"]
    )

    # ── RECOMMENDATIONS (Phase 7: AI Engine) ───────────────────────────────
    op.create_table(
        "recommendations",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "scan_id",
            sa.String(36),
            sa.ForeignKey("scans.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("provider", sa.String(20), nullable=False),
        sa.Column("model", sa.String(50), nullable=True),
        sa.Column("category", sa.String(30), nullable=False),
        sa.Column("priority", sa.String(10), nullable=False),
        sa.Column("title", sa.String, nullable=False),
        sa.Column("description", sa.String, nullable=False),
        sa.Column("explanation", sa.String, nullable=True),
        sa.Column("recoverable_bytes", sa.Integer, nullable=False, server_default="0"),
        sa.Column("confidence", sa.Float, nullable=False, server_default="0.0"),
        sa.Column("affected_paths", sa.String, nullable=True),
        sa.Column("affected_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("dismissed_reason", sa.String, nullable=True),
        sa.Column("created_at", sa.String, nullable=False),
    )
    op.create_index("idx_rec_status", "recommendations", ["status"])
    op.create_index("idx_rec_priority", "recommendations", ["priority"])
    op.create_index("idx_rec_category", "recommendations", ["category"])
    op.create_index("idx_rec_scan", "recommendations", ["scan_id"])

    # ── PREDICTIONS (Phase 8: Predictive Analytics) ────────────────────────
    op.create_table(
        "predictions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("model_type", sa.String(30), nullable=False),
        sa.Column("input_snapshots", sa.Integer, nullable=False),
        sa.Column("daily_growth_bytes", sa.Float, nullable=False),
        sa.Column("weekly_growth_bytes", sa.Float, nullable=False),
        sa.Column("predicted_total_30d", sa.Integer, nullable=True),
        sa.Column("predicted_total_90d", sa.Integer, nullable=True),
        sa.Column("exhaustion_date", sa.String(10), nullable=True),
        sa.Column("days_until_full", sa.Integer, nullable=True),
        sa.Column("confidence", sa.Float, nullable=False),
        sa.Column("confidence_interval", sa.String, nullable=True),
        sa.Column("metadata", sa.String, nullable=True),
        sa.Column("created_at", sa.String, nullable=False),
    )
    op.create_index("idx_predictions_created", "predictions", ["created_at"])

    # ── CLEANUP ACTIONS (Phase 9: Safety Framework) ────────────────────────
    op.create_table(
        "cleanup_actions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "recommendation_id",
            sa.String(36),
            sa.ForeignKey("recommendations.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("action_type", sa.String(20), nullable=False),
        sa.Column("target_paths", sa.String, nullable=False),
        sa.Column("target_count", sa.Integer, nullable=False),
        sa.Column("total_bytes", sa.Integer, nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="proposed"),
        sa.Column("dry_run_result", sa.String, nullable=True),
        sa.Column("approved_at", sa.String, nullable=True),
        sa.Column("approved_by", sa.String, nullable=True),
        sa.Column("executed_at", sa.String, nullable=True),
        sa.Column("completed_at", sa.String, nullable=True),
        sa.Column("rolled_back_at", sa.String, nullable=True),
        sa.Column("trash_location", sa.String, nullable=True),
        sa.Column("manifest_path", sa.String, nullable=True),
        sa.Column("error_message", sa.String, nullable=True),
        sa.Column("bytes_recovered", sa.Integer, nullable=False, server_default="0"),
        sa.Column("created_at", sa.String, nullable=False),
    )
    op.create_index("idx_cleanup_status", "cleanup_actions", ["status"])
    op.create_index("idx_cleanup_created", "cleanup_actions", ["created_at"])

    # ── AUDIT LOGS (Phase 9: Safety Framework) ─────────────────────────────
    op.create_table(
        "audit_logs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("correlation_id", sa.String(36), nullable=True),
        sa.Column("action", sa.String(50), nullable=False),
        sa.Column("entity_type", sa.String(30), nullable=True),
        sa.Column("entity_id", sa.String(36), nullable=True),
        sa.Column("description", sa.String, nullable=True),
        sa.Column("metadata", sa.String, nullable=True),
        sa.Column("bytes_affected", sa.Integer, nullable=False, server_default="0"),
        sa.Column("paths_affected", sa.String, nullable=True),
        sa.Column("severity", sa.String(10), nullable=False, server_default="info"),
        sa.Column("created_at", sa.String, nullable=False),
    )
    op.create_index("idx_audit_action", "audit_logs", ["action"])
    op.create_index(
        "idx_audit_entity", "audit_logs", ["entity_type", "entity_id"]
    )
    op.create_index("idx_audit_created", "audit_logs", ["created_at"])
    op.create_index(
        "idx_audit_correlation",
        "audit_logs",
        ["correlation_id"],
        sqlite_where=sa.text("correlation_id IS NOT NULL"),
    )
    op.create_index(
        "idx_audit_severity",
        "audit_logs",
        ["severity"],
        sqlite_where=sa.text("severity != 'info'"),
    )


def downgrade() -> None:
    op.drop_table("audit_logs")
    op.drop_table("cleanup_actions")
    op.drop_table("predictions")
    op.drop_table("recommendations")
    op.drop_table("dev_workspaces")
    op.drop_table("duplicate_members")
    op.drop_table("duplicate_groups")
    op.drop_table("storage_snapshots")

"""Initial schema: scans, files, folders, exclusion_rules

Revision ID: 001
Revises: None
Create Date: 2026-06-23
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── SCANS ──────────────────────────────────────────────────────────────
    op.create_table(
        "scans",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("root_path", sa.String, nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("scan_type", sa.String(20), nullable=False, server_default="full"),
        sa.Column("started_at", sa.String, nullable=True),
        sa.Column("completed_at", sa.String, nullable=True),
        sa.Column("total_files", sa.Integer, nullable=False, server_default="0"),
        sa.Column("total_dirs", sa.Integer, nullable=False, server_default="0"),
        sa.Column("total_size_bytes", sa.Integer, nullable=False, server_default="0"),
        sa.Column("files_per_second", sa.Float, nullable=True),
        sa.Column("error_message", sa.String, nullable=True),
        sa.Column("checkpoint_data", sa.String, nullable=True),
        sa.Column("exclusion_patterns", sa.String, nullable=True),
        sa.Column("platform", sa.String(20), nullable=True),
        sa.Column("created_at", sa.String, nullable=False),
    )
    op.create_index("idx_scans_status", "scans", ["status"])
    op.create_index("idx_scans_created_at", "scans", ["created_at"])

    # ── FILES ──────────────────────────────────────────────────────────────
    op.create_table(
        "files",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "scan_id",
            sa.String(36),
            sa.ForeignKey("scans.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("path", sa.String, nullable=False),
        sa.Column("directory", sa.String, nullable=False),
        sa.Column("filename", sa.String, nullable=False),
        sa.Column("extension", sa.String(50), nullable=True),
        sa.Column("size_bytes", sa.Integer, nullable=False),
        sa.Column("mime_type", sa.String(100), nullable=True),
        sa.Column("category", sa.String(20), nullable=True),
        sa.Column("created_at", sa.String, nullable=True),
        sa.Column("modified_at", sa.String, nullable=True),
        sa.Column("accessed_at", sa.String, nullable=True),
        sa.Column("owner", sa.String(100), nullable=True),
        sa.Column("permissions", sa.String(10), nullable=True),
        sa.Column("sha256_hash", sa.String(64), nullable=True),
        sa.Column("is_duplicate", sa.Integer, nullable=False, server_default="0"),
        sa.Column("is_stale", sa.Integer, nullable=False, server_default="0"),
        sa.Column("stale_score", sa.Float, nullable=True),
        sa.Column("risk_level", sa.String(10), nullable=True),
        sa.Column("discovered_at", sa.String, nullable=False),
    )
    op.create_index("idx_files_scan_id", "files", ["scan_id"])
    op.create_index("idx_files_directory", "files", ["directory"])
    op.create_index("idx_files_extension", "files", ["extension"])
    op.create_index("idx_files_size_bytes", "files", ["size_bytes"])
    op.create_index("idx_files_category", "files", ["category"])
    op.create_index("idx_files_modified_at", "files", ["modified_at"])
    op.create_index("idx_files_accessed_at", "files", ["accessed_at"])
    op.create_index(
        "idx_files_hash",
        "files",
        ["sha256_hash"],
        sqlite_where=sa.text("sha256_hash IS NOT NULL"),
    )
    op.create_index(
        "idx_files_is_stale",
        "files",
        ["is_stale"],
        sqlite_where=sa.text("is_stale = 1"),
    )
    op.create_index(
        "idx_files_is_duplicate",
        "files",
        ["is_duplicate"],
        sqlite_where=sa.text("is_duplicate = 1"),
    )
    op.create_index("idx_files_size_scan", "files", ["scan_id", "size_bytes"])

    # ── FOLDERS ────────────────────────────────────────────────────────────
    op.create_table(
        "folders",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "scan_id",
            sa.String(36),
            sa.ForeignKey("scans.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("path", sa.String, nullable=False),
        sa.Column("name", sa.String, nullable=False),
        sa.Column("parent_path", sa.String, nullable=True),
        sa.Column("depth", sa.Integer, nullable=False, server_default="0"),
        sa.Column("total_size_bytes", sa.Integer, nullable=False, server_default="0"),
        sa.Column("file_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("dir_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("discovered_at", sa.String, nullable=False),
    )
    op.create_index("idx_folders_scan_id", "folders", ["scan_id"])
    op.create_index("idx_folders_path", "folders", ["path"])
    op.create_index("idx_folders_parent", "folders", ["parent_path"])
    op.create_index("idx_folders_size", "folders", ["total_size_bytes"])

    # ── EXCLUSION RULES ────────────────────────────────────────────────────
    op.create_table(
        "exclusion_rules",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("pattern", sa.String, nullable=False),
        sa.Column("rule_type", sa.String(20), nullable=False),
        sa.Column("description", sa.String, nullable=True),
        sa.Column("is_system", sa.Integer, nullable=False, server_default="0"),
        sa.Column("is_active", sa.Integer, nullable=False, server_default="1"),
        sa.Column("created_at", sa.String, nullable=False),
    )

    # ── SEED DEFAULT EXCLUSION RULES ──────────────────────────────────────
    op.execute(
        """
        INSERT INTO exclusion_rules (id, pattern, rule_type, description, is_system, is_active, created_at)
        VALUES
        ('00000000-0000-0000-0000-000000000001', '.git', 'name', 'Git repositories', 1, 1, '2026-06-23T00:00:00.000000Z'),
        ('00000000-0000-0000-0000-000000000002', '.svn', 'name', 'Subversion directories', 1, 1, '2026-06-23T00:00:00.000000Z'),
        ('00000000-0000-0000-0000-000000000003', '__pycache__', 'name', 'Python bytecode cache', 1, 1, '2026-06-23T00:00:00.000000Z'),
        ('00000000-0000-0000-0000-000000000004', '.DS_Store', 'name', 'macOS Finder metadata', 1, 1, '2026-06-23T00:00:00.000000Z'),
        ('00000000-0000-0000-0000-000000000005', 'Thumbs.db', 'name', 'Windows thumbnail cache', 1, 1, '2026-06-23T00:00:00.000000Z'),
        ('00000000-0000-0000-0000-000000000006', '$Recycle.Bin', 'name', 'Windows Recycle Bin', 1, 1, '2026-06-23T00:00:00.000000Z'),
        ('00000000-0000-0000-0000-000000000007', 'System Volume Information', 'name', 'Windows system restore', 1, 1, '2026-06-23T00:00:00.000000Z'),
        ('00000000-0000-0000-0000-000000000008', '.Spotlight-V100', 'name', 'macOS Spotlight index', 1, 1, '2026-06-23T00:00:00.000000Z'),
        ('00000000-0000-0000-0000-000000000009', '.fseventsd', 'name', 'macOS FSEvents log', 1, 1, '2026-06-23T00:00:00.000000Z'),
        ('00000000-0000-0000-0000-000000000010', '.Trashes', 'name', 'macOS Trash', 1, 1, '2026-06-23T00:00:00.000000Z'),
        ('00000000-0000-0000-0000-000000000011', '.Trash', 'name', 'macOS User Trash', 1, 1, '2026-06-23T00:00:00.000000Z'),
        ('00000000-0000-0000-0000-000000000012', 'Library', 'name', 'macOS Library', 1, 1, '2026-06-23T00:00:00.000000Z')
        """
    )


def downgrade() -> None:
    op.drop_table("exclusion_rules")
    op.drop_table("folders")
    op.drop_table("files")
    op.drop_table("scans")

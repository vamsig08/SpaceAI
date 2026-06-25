"""SQLAlchemy ORM model for cleanup operation tracking."""

from sqlalchemy import ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class CleanupAction(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Tracks the lifecycle of a cleanup operation through the safety workflow.

    States: proposed → approved → executing → completed|failed|rolled_back

    Every destructive operation must flow through this table with explicit
    user approval before execution.
    """

    __tablename__ = "cleanup_actions"

    recommendation_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("recommendations.id", ondelete="SET NULL"), nullable=True
    )
    action_type: Mapped[str] = mapped_column(String(20), nullable=False)
    target_paths: Mapped[str] = mapped_column(String, nullable=False)
    target_count: Mapped[int] = mapped_column(Integer, nullable=False)
    total_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default="proposed"
    )
    dry_run_result: Mapped[str | None] = mapped_column(String, nullable=True)
    approved_at: Mapped[str | None] = mapped_column(String, nullable=True)
    approved_by: Mapped[str | None] = mapped_column(String, nullable=True)
    executed_at: Mapped[str | None] = mapped_column(String, nullable=True)
    completed_at: Mapped[str | None] = mapped_column(String, nullable=True)
    rolled_back_at: Mapped[str | None] = mapped_column(String, nullable=True)
    trash_location: Mapped[str | None] = mapped_column(String, nullable=True)
    manifest_path: Mapped[str | None] = mapped_column(String, nullable=True)
    error_message: Mapped[str | None] = mapped_column(String, nullable=True)
    bytes_recovered: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="0"
    )

    def __repr__(self) -> str:
        return (
            f"CleanupAction(id={self.id!r}, type={self.action_type!r}, "
            f"status={self.status!r}, bytes={self.total_bytes})"
        )

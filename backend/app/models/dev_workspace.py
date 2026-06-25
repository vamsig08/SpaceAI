"""SQLAlchemy ORM model for detected developer workspaces."""

from sqlalchemy import ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class DevWorkspace(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Represents a detected developer workspace (project, venv, build dir, etc).

    Stores detection results from the workspace analysis pipeline including
    recoverable space estimates and activity classification.
    """

    __tablename__ = "dev_workspaces"

    scan_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("scans.id", ondelete="CASCADE"), nullable=False
    )
    path: Mapped[str] = mapped_column(String, nullable=False)
    name: Mapped[str] = mapped_column(String, nullable=False)
    workspace_type: Mapped[str] = mapped_column(String(20), nullable=False)
    total_size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    recoverable_bytes: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="0"
    )
    safe_recoverable_bytes: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="0"
    )
    last_modified_at: Mapped[str | None] = mapped_column(String, nullable=True)
    is_active: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")
    days_inactive: Mapped[int | None] = mapped_column(Integer, nullable=True)
    risk_level: Mapped[str] = mapped_column(
        String(10), nullable=False, server_default="low"
    )
    artifacts: Mapped[str] = mapped_column(String, nullable=False)

    def __repr__(self) -> str:
        return (
            f"DevWorkspace(id={self.id!r}, name={self.name!r}, "
            f"type={self.workspace_type!r}, recoverable={self.recoverable_bytes})"
        )

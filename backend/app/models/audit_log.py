"""SQLAlchemy ORM model for the immutable audit trail."""

from sqlalchemy import Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class AuditLog(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Immutable record of all system actions for compliance and rollback.

    Every significant operation (scan, cleanup, restore, setting change)
    creates an audit log entry. These are never modified or deleted.
    """

    __tablename__ = "audit_logs"

    correlation_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    action: Mapped[str] = mapped_column(String(50), nullable=False)
    entity_type: Mapped[str | None] = mapped_column(String(30), nullable=True)
    entity_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    description: Mapped[str | None] = mapped_column(String, nullable=True)
    details: Mapped[str | None] = mapped_column("metadata", String, nullable=True)
    bytes_affected: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="0"
    )
    paths_affected: Mapped[str | None] = mapped_column(String, nullable=True)
    severity: Mapped[str] = mapped_column(
        String(10), nullable=False, server_default="info"
    )

    def __repr__(self) -> str:
        return (
            f"AuditLog(id={self.id!r}, action={self.action!r}, "
            f"entity={self.entity_type}:{self.entity_id})"
        )

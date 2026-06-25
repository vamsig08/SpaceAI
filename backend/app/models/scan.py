"""SQLAlchemy ORM model for filesystem scan operations."""

from sqlalchemy import Float, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class Scan(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Represents a filesystem scan operation.

    Tracks the lifecycle of a scan from pending through completion or failure,
    including checkpoint data for crash recovery and resume support.
    """

    __tablename__ = "scans"

    root_path: Mapped[str] = mapped_column(String, nullable=False)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default="pending"
    )
    scan_type: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default="full"
    )
    started_at: Mapped[str | None] = mapped_column(String, nullable=True)
    completed_at: Mapped[str | None] = mapped_column(String, nullable=True)
    total_files: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    total_dirs: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    total_size_bytes: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="0"
    )
    files_per_second: Mapped[float | None] = mapped_column(Float, nullable=True)
    error_message: Mapped[str | None] = mapped_column(String, nullable=True)
    checkpoint_data: Mapped[str | None] = mapped_column(String, nullable=True)
    exclusion_patterns: Mapped[str | None] = mapped_column(String, nullable=True)
    platform: Mapped[str | None] = mapped_column(String(20), nullable=True)

    # Relationships
    files: Mapped[list["File"]] = relationship(  # type: ignore[name-defined]  # noqa: F821
        "File", back_populates="scan", cascade="all, delete-orphan", lazy="selectin"
    )
    folders: Mapped[list["Folder"]] = relationship(  # type: ignore[name-defined]  # noqa: F821
        "Folder", back_populates="scan", cascade="all, delete-orphan", lazy="selectin"
    )

    def __repr__(self) -> str:
        return (
            f"Scan(id={self.id!r}, root_path={self.root_path!r}, "
            f"status={self.status!r}, total_files={self.total_files})"
        )

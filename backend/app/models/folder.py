"""SQLAlchemy ORM model for discovered directory metadata."""

from sqlalchemy import ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, UUIDPrimaryKeyMixin


class Folder(UUIDPrimaryKeyMixin, Base):
    """Represents a directory discovered during a filesystem scan.

    Stores aggregated size and count information computed during
    the post-scan analysis phase.
    """

    __tablename__ = "folders"

    scan_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("scans.id", ondelete="CASCADE"), nullable=False
    )
    path: Mapped[str] = mapped_column(String, nullable=False)
    name: Mapped[str] = mapped_column(String, nullable=False)
    parent_path: Mapped[str | None] = mapped_column(String, nullable=True)
    depth: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    total_size_bytes: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="0"
    )
    file_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    dir_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    discovered_at: Mapped[str] = mapped_column(String, nullable=False)

    # Relationships
    scan: Mapped["Scan"] = relationship(  # type: ignore[name-defined]  # noqa: F821
        "Scan", back_populates="folders"
    )

    def __repr__(self) -> str:
        return (
            f"Folder(id={self.id!r}, path={self.path!r}, "
            f"total_size_bytes={self.total_size_bytes})"
        )

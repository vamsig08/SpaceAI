"""SQLAlchemy ORM model for discovered file metadata."""

from sqlalchemy import Float, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, UUIDPrimaryKeyMixin


class File(UUIDPrimaryKeyMixin, Base):
    """Represents a single file discovered during a filesystem scan.

    Contains all metadata collected during the discovery pass.
    Hash, stale scoring, and duplicate flags are populated in later passes.
    """

    __tablename__ = "files"

    scan_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("scans.id", ondelete="CASCADE"), nullable=False
    )
    path: Mapped[str] = mapped_column(String, nullable=False)
    directory: Mapped[str] = mapped_column(String, nullable=False)
    filename: Mapped[str] = mapped_column(String, nullable=False)
    extension: Mapped[str | None] = mapped_column(String(50), nullable=True)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    mime_type: Mapped[str | None] = mapped_column(String(100), nullable=True)
    category: Mapped[str | None] = mapped_column(String(20), nullable=True)
    created_at: Mapped[str | None] = mapped_column(String, nullable=True)
    modified_at: Mapped[str | None] = mapped_column(String, nullable=True)
    accessed_at: Mapped[str | None] = mapped_column(String, nullable=True)
    owner: Mapped[str | None] = mapped_column(String(100), nullable=True)
    permissions: Mapped[str | None] = mapped_column(String(10), nullable=True)
    sha256_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    is_duplicate: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="0"
    )
    is_stale: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    stale_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    risk_level: Mapped[str | None] = mapped_column(String(10), nullable=True)
    discovered_at: Mapped[str] = mapped_column(String, nullable=False)

    # Relationships
    scan: Mapped["Scan"] = relationship(  # type: ignore[name-defined]  # noqa: F821
        "Scan", back_populates="files"
    )

    def __repr__(self) -> str:
        return (
            f"File(id={self.id!r}, path={self.path!r}, "
            f"size_bytes={self.size_bytes})"
        )

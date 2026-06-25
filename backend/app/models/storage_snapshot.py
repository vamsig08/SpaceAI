"""SQLAlchemy ORM model for pre-computed storage analytics snapshots."""

from sqlalchemy import Float, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class StorageSnapshot(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Pre-computed analytics snapshot generated after each scan completes.

    Contains aggregated storage metrics, category breakdowns, and top-N
    file/directory rankings. Designed to serve dashboard queries in <50ms
    without aggregating the raw files table (which may have 1M+ rows).
    """

    __tablename__ = "storage_snapshots"

    scan_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("scans.id", ondelete="CASCADE"), nullable=False
    )
    snapshot_date: Mapped[str] = mapped_column(String(10), nullable=False)
    total_size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    used_size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    file_count: Mapped[int] = mapped_column(Integer, nullable=False)
    dir_count: Mapped[int] = mapped_column(Integer, nullable=False)
    category_breakdown: Mapped[str] = mapped_column(String, nullable=False)
    extension_breakdown: Mapped[str | None] = mapped_column(String, nullable=True)
    largest_files: Mapped[str | None] = mapped_column(String, nullable=True)
    largest_dirs: Mapped[str | None] = mapped_column(String, nullable=True)

    def __repr__(self) -> str:
        return (
            f"StorageSnapshot(id={self.id!r}, date={self.snapshot_date!r}, "
            f"files={self.file_count}, size={self.total_size_bytes})"
        )

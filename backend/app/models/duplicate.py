"""SQLAlchemy ORM models for duplicate detection results."""

from sqlalchemy import ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class DuplicateGroup(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A group of files that share the same SHA256 hash.

    Each group represents a set of identical files. The wasted_bytes
    field tracks how much space could be recovered by keeping only one copy.
    """

    __tablename__ = "duplicate_groups"

    scan_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("scans.id", ondelete="CASCADE"), nullable=False
    )
    sha256_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    file_size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    member_count: Mapped[int] = mapped_column(Integer, nullable=False)
    wasted_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default="unresolved"
    )

    # Relationships
    members: Mapped[list["DuplicateMember"]] = relationship(
        "DuplicateMember",
        back_populates="group",
        cascade="all, delete-orphan",
        lazy="selectin",
    )

    def __repr__(self) -> str:
        return (
            f"DuplicateGroup(id={self.id!r}, hash={self.sha256_hash[:12]}..., "
            f"members={self.member_count}, wasted={self.wasted_bytes})"
        )


class DuplicateMember(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A single file that belongs to a duplicate group.

    The is_keeper flag marks which copy the user has designated to keep.
    All other members are candidates for cleanup.
    """

    __tablename__ = "duplicate_members"

    group_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("duplicate_groups.id", ondelete="CASCADE"), nullable=False
    )
    file_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("files.id", ondelete="CASCADE"), nullable=False
    )
    path: Mapped[str] = mapped_column(String, nullable=False)
    is_keeper: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")

    # Relationships
    group: Mapped["DuplicateGroup"] = relationship(
        "DuplicateGroup", back_populates="members"
    )

    def __repr__(self) -> str:
        return (
            f"DuplicateMember(id={self.id!r}, path={self.path!r}, "
            f"keeper={bool(self.is_keeper)})"
        )

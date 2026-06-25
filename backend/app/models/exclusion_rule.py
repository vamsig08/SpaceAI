"""SQLAlchemy ORM model for scan exclusion rules."""

from sqlalchemy import Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class ExclusionRule(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Represents a pattern-based rule for excluding paths from scans.

    Rules can be system-defined (built-in defaults) or user-defined.
    Patterns are matched against directory/file names or full paths
    depending on the rule_type.
    """

    __tablename__ = "exclusion_rules"

    pattern: Mapped[str] = mapped_column(String, nullable=False)
    rule_type: Mapped[str] = mapped_column(String(20), nullable=False)
    description: Mapped[str | None] = mapped_column(String, nullable=True)
    is_system: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    is_active: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")

    def __repr__(self) -> str:
        return (
            f"ExclusionRule(id={self.id!r}, pattern={self.pattern!r}, "
            f"rule_type={self.rule_type!r})"
        )

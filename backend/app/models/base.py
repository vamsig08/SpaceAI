"""SQLAlchemy declarative base and common model mixins.

Provides the shared base class for all ORM models and reusable
column mixins for timestamps, UUIDs, and audit fields.
"""

import uuid
from datetime import datetime, timezone

from sqlalchemy import String, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def generate_uuid() -> str:
    """Generate a new UUID4 string for use as primary key."""
    return str(uuid.uuid4())


def utc_now() -> str:
    """Generate current UTC timestamp as ISO8601 string."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


class Base(DeclarativeBase):
    """Declarative base for all SpaceAI ORM models.

    All models inherit from this base. It configures:
    - Type annotation mapping for common Python types
    - Default naming conventions for constraints and indexes
    """

    pass


class TimestampMixin:
    """Mixin that adds a created_at column with auto-set default.

    Use this mixin for any model that needs creation timestamp tracking.
    """

    created_at: Mapped[str] = mapped_column(
        String,
        default=utc_now,
        nullable=False,
    )


class UUIDPrimaryKeyMixin:
    """Mixin that provides a UUID text primary key.

    Every model should use this unless it has a composite key.
    UUIDs are generated in Python (not by the DB) to enable batch
    inserts without round-trips for generated IDs.
    """

    id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=generate_uuid,
    )

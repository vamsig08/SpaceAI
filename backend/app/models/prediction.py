"""SQLAlchemy ORM model for storage growth predictions."""

from sqlalchemy import Float, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class Prediction(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Stores storage growth forecasting results from predictive analytics.

    Each record represents a prediction computed at a point in time,
    using historical storage_snapshots data to project future usage.
    """

    __tablename__ = "predictions"

    model_type: Mapped[str] = mapped_column(String(30), nullable=False)
    input_snapshots: Mapped[int] = mapped_column(Integer, nullable=False)
    daily_growth_bytes: Mapped[float] = mapped_column(Float, nullable=False)
    weekly_growth_bytes: Mapped[float] = mapped_column(Float, nullable=False)
    predicted_total_30d: Mapped[int | None] = mapped_column(Integer, nullable=True)
    predicted_total_90d: Mapped[int | None] = mapped_column(Integer, nullable=True)
    exhaustion_date: Mapped[str | None] = mapped_column(String(10), nullable=True)
    days_until_full: Mapped[int | None] = mapped_column(Integer, nullable=True)
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    confidence_interval: Mapped[str | None] = mapped_column(String, nullable=True)
    model_metadata: Mapped[str | None] = mapped_column(
        "metadata", String, nullable=True
    )

    def __repr__(self) -> str:
        return (
            f"Prediction(id={self.id!r}, model={self.model_type!r}, "
            f"exhaustion={self.exhaustion_date!r})"
        )

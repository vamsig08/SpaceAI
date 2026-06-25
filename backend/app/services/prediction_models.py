"""Storage prediction models — deterministic, offline, explainable.

Implements three forecasting approaches:
1. Linear Regression: Best for steady, consistent growth patterns
2. Moving Average: Best for smoothing noisy data, short-term forecasts
3. Exponential Smoothing: Best for data with changing trends

Model selection is automatic based on data characteristics (R² fit, variance).

All models operate on storage_snapshots data (date → total_size_bytes)
and require no external dependencies (no scipy, no sklearn).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any


@dataclass
class ForecastResult:
    """Output of a forecasting model."""

    model_type: str              # linear_regression | moving_average | exponential_smoothing
    daily_growth_bytes: float
    weekly_growth_bytes: float
    predicted_total_30d: int     # Predicted total bytes in 30 days
    predicted_total_90d: int     # Predicted total bytes in 90 days
    exhaustion_date: str | None  # YYYY-MM-DD when disk is full (None if not applicable)
    days_until_full: int | None
    confidence: float            # 0.0-1.0
    trend: str                   # stable|slow_growth|moderate_growth|rapid_growth|critical_growth
    r_squared: float             # Goodness of fit (linear regression only, 0 for others)
    data_points_used: int


@dataclass
class TimeSeriesPoint:
    """A single data point in the time series."""

    day_index: int      # Days since first snapshot
    total_bytes: int


# ─── Trend Classification ──────────────────────────────────────────────────────

def classify_trend(daily_growth_bytes: float, total_bytes: int) -> str:
    """Classify growth trend based on daily growth rate relative to total storage.

    Args:
        daily_growth_bytes: Average bytes added per day.
        total_bytes: Current total storage used.

    Returns:
        Trend category string.
    """
    if total_bytes <= 0 or daily_growth_bytes <= 0:
        return "stable"

    # Growth as percentage of total per month
    monthly_growth_pct = (daily_growth_bytes * 30) / total_bytes * 100

    if monthly_growth_pct < 0.5:
        return "stable"
    elif monthly_growth_pct < 2.0:
        return "slow_growth"
    elif monthly_growth_pct < 5.0:
        return "moderate_growth"
    elif monthly_growth_pct < 10.0:
        return "rapid_growth"
    else:
        return "critical_growth"


# ─── Linear Regression ─────────────────────────────────────────────────────────

def linear_regression_forecast(
    data_points: list[TimeSeriesPoint],
    disk_capacity: int,
    forecast_days: int = 90,
) -> ForecastResult:
    """Forecast using simple linear regression (least squares fit).

    Best for: Steady, consistent growth patterns.
    Requires: At least 3 data points.

    The model fits y = mx + b where:
    - x = day index
    - y = total storage bytes
    - m = daily growth rate
    - b = initial storage at day 0

    Args:
        data_points: Time series of (day_index, total_bytes).
        disk_capacity: Total disk capacity in bytes (for exhaustion calc).
        forecast_days: How many days to project forward.

    Returns:
        ForecastResult with predictions and confidence.
    """
    n = len(data_points)
    if n < 2:
        return _empty_result("linear_regression", n)

    # Compute linear regression coefficients using least squares
    sum_x = sum(p.day_index for p in data_points)
    sum_y = sum(p.total_bytes for p in data_points)
    sum_xy = sum(p.day_index * p.total_bytes for p in data_points)
    sum_x2 = sum(p.day_index ** 2 for p in data_points)

    denominator = n * sum_x2 - sum_x ** 2
    if denominator == 0:
        return _empty_result("linear_regression", n)

    # Slope (daily growth rate) and intercept
    slope = (n * sum_xy - sum_x * sum_y) / denominator
    intercept = (sum_y - slope * sum_x) / n

    # R² (coefficient of determination)
    y_mean = sum_y / n
    ss_tot = sum((p.total_bytes - y_mean) ** 2 for p in data_points)
    ss_res = sum((p.total_bytes - (slope * p.day_index + intercept)) ** 2 for p in data_points)
    r_squared = 1.0 - (ss_res / ss_tot) if ss_tot > 0 else 0.0
    r_squared = max(0.0, min(1.0, r_squared))

    # Current position (last data point)
    last_day = data_points[-1].day_index
    current_bytes = data_points[-1].total_bytes

    # Predictions
    predicted_30d = int(slope * (last_day + 30) + intercept)
    predicted_90d = int(slope * (last_day + 90) + intercept)

    # Days until full
    days_until_full = None
    exhaustion_date = None
    if slope > 0 and disk_capacity > current_bytes:
        remaining = disk_capacity - current_bytes
        days_until_full = min(int(remaining / slope), 99999)
        if days_until_full > 0:
            from datetime import datetime, timedelta, timezone
            exhaustion_dt = datetime.now(timezone.utc) + timedelta(days=days_until_full)
            exhaustion_date = exhaustion_dt.strftime("%Y-%m-%d")

    # Confidence based on R² and number of data points
    confidence = _compute_confidence(r_squared, n)

    daily_growth = max(0.0, slope)
    trend = classify_trend(daily_growth, current_bytes)

    return ForecastResult(
        model_type="linear_regression",
        daily_growth_bytes=daily_growth,
        weekly_growth_bytes=daily_growth * 7,
        predicted_total_30d=max(predicted_30d, current_bytes),
        predicted_total_90d=max(predicted_90d, current_bytes),
        exhaustion_date=exhaustion_date,
        days_until_full=days_until_full,
        confidence=confidence,
        trend=trend,
        r_squared=r_squared,
        data_points_used=n,
    )


# ─── Moving Average ────────────────────────────────────────────────────────────

def moving_average_forecast(
    data_points: list[TimeSeriesPoint],
    disk_capacity: int,
    window_size: int = 7,
) -> ForecastResult:
    """Forecast using simple moving average of growth deltas.

    Best for: Noisy data, short-term forecasts.
    Requires: At least window_size + 1 data points.

    Computes the average daily growth over the last `window_size` intervals
    and extrapolates linearly.

    Args:
        data_points: Time series of (day_index, total_bytes).
        disk_capacity: Total disk capacity for exhaustion calc.
        window_size: Number of recent intervals to average.

    Returns:
        ForecastResult with predictions.
    """
    n = len(data_points)
    if n < 3:
        return _empty_result("moving_average", n)

    # Compute daily deltas
    deltas: list[float] = []
    for i in range(1, n):
        day_diff = data_points[i].day_index - data_points[i - 1].day_index
        if day_diff > 0:
            byte_diff = data_points[i].total_bytes - data_points[i - 1].total_bytes
            deltas.append(byte_diff / day_diff)

    if not deltas:
        return _empty_result("moving_average", n)

    # Use last `window_size` deltas (or all if fewer available)
    window = deltas[-window_size:]
    avg_daily_growth = sum(window) / len(window)

    current_bytes = data_points[-1].total_bytes
    predicted_30d = int(current_bytes + avg_daily_growth * 30)
    predicted_90d = int(current_bytes + avg_daily_growth * 90)

    # Days until full
    days_until_full = None
    exhaustion_date = None
    if avg_daily_growth > 0 and disk_capacity > current_bytes:
        remaining = disk_capacity - current_bytes
        days_until_full = min(int(remaining / avg_daily_growth), 99999)
        if days_until_full > 0:
            from datetime import datetime, timedelta, timezone
            exhaustion_dt = datetime.now(timezone.utc) + timedelta(days=days_until_full)
            exhaustion_date = exhaustion_dt.strftime("%Y-%m-%d")

    # Confidence: lower than linear regression (less sophisticated)
    confidence = min(0.75, 0.5 + 0.03 * len(window))

    daily_growth = max(0.0, avg_daily_growth)
    trend = classify_trend(daily_growth, current_bytes)

    return ForecastResult(
        model_type="moving_average",
        daily_growth_bytes=daily_growth,
        weekly_growth_bytes=daily_growth * 7,
        predicted_total_30d=max(predicted_30d, current_bytes),
        predicted_total_90d=max(predicted_90d, current_bytes),
        exhaustion_date=exhaustion_date,
        days_until_full=days_until_full,
        confidence=confidence,
        trend=trend,
        r_squared=0.0,
        data_points_used=n,
    )


# ─── Exponential Smoothing ─────────────────────────────────────────────────────

def exponential_smoothing_forecast(
    data_points: list[TimeSeriesPoint],
    disk_capacity: int,
    alpha: float = 0.3,
) -> ForecastResult:
    """Forecast using simple exponential smoothing on growth deltas.

    Best for: Data with changing trends (gives more weight to recent observations).
    Requires: At least 4 data points.

    Uses alpha smoothing factor where:
    - alpha close to 1.0 = more weight on recent data
    - alpha close to 0.0 = more weight on historical data

    Args:
        data_points: Time series of (day_index, total_bytes).
        disk_capacity: Total disk capacity.
        alpha: Smoothing factor (0.0-1.0). Default 0.3.

    Returns:
        ForecastResult with predictions.
    """
    n = len(data_points)
    if n < 3:
        return _empty_result("exponential_smoothing", n)

    # Compute daily deltas
    deltas: list[float] = []
    for i in range(1, n):
        day_diff = data_points[i].day_index - data_points[i - 1].day_index
        if day_diff > 0:
            byte_diff = data_points[i].total_bytes - data_points[i - 1].total_bytes
            deltas.append(byte_diff / day_diff)

    if not deltas:
        return _empty_result("exponential_smoothing", n)

    # Apply exponential smoothing
    smoothed = deltas[0]
    for delta in deltas[1:]:
        smoothed = alpha * delta + (1 - alpha) * smoothed

    current_bytes = data_points[-1].total_bytes
    predicted_30d = int(current_bytes + smoothed * 30)
    predicted_90d = int(current_bytes + smoothed * 90)

    # Days until full
    days_until_full = None
    exhaustion_date = None
    if smoothed > 0 and disk_capacity > current_bytes:
        remaining = disk_capacity - current_bytes
        days_until_full = min(int(remaining / smoothed), 99999)
        if days_until_full > 0:
            from datetime import datetime, timedelta, timezone
            exhaustion_dt = datetime.now(timezone.utc) + timedelta(days=days_until_full)
            exhaustion_date = exhaustion_dt.strftime("%Y-%m-%d")

    # Confidence: moderate, benefits from longer series
    confidence = min(0.80, 0.45 + 0.04 * n)

    daily_growth = max(0.0, smoothed)
    trend = classify_trend(daily_growth, current_bytes)

    return ForecastResult(
        model_type="exponential_smoothing",
        daily_growth_bytes=daily_growth,
        weekly_growth_bytes=daily_growth * 7,
        predicted_total_30d=max(predicted_30d, current_bytes),
        predicted_total_90d=max(predicted_90d, current_bytes),
        exhaustion_date=exhaustion_date,
        days_until_full=days_until_full,
        confidence=confidence,
        trend=trend,
        r_squared=0.0,
        data_points_used=n,
    )


# ─── Model Selection ──────────────────────────────────────────────────────────

def select_best_model(
    data_points: list[TimeSeriesPoint],
    disk_capacity: int,
) -> ForecastResult:
    """Automatically select the best forecasting model based on data characteristics.

    Selection logic:
    - If <5 data points: moving average (insufficient for regression)
    - If linear regression R² > 0.85: use linear (strong linear trend)
    - If data has high variance: use exponential smoothing (adapts to change)
    - Otherwise: use linear regression (most interpretable)

    Args:
        data_points: Time series data.
        disk_capacity: Total disk capacity.

    Returns:
        ForecastResult from the best-fitting model.
    """
    n = len(data_points)

    if n < 3:
        return _empty_result("auto", n)

    if n < 5:
        return moving_average_forecast(data_points, disk_capacity)

    # Try linear regression first
    lr_result = linear_regression_forecast(data_points, disk_capacity)

    # If strong linear fit, use it
    if lr_result.r_squared >= 0.85:
        return lr_result

    # Check variance of growth deltas
    deltas: list[float] = []
    for i in range(1, n):
        day_diff = data_points[i].day_index - data_points[i - 1].day_index
        if day_diff > 0:
            deltas.append(
                (data_points[i].total_bytes - data_points[i - 1].total_bytes) / day_diff
            )

    if deltas:
        mean_delta = sum(deltas) / len(deltas)
        variance = sum((d - mean_delta) ** 2 for d in deltas) / len(deltas)
        cv = math.sqrt(variance) / abs(mean_delta) if mean_delta != 0 else 0

        # High coefficient of variation → use exponential smoothing
        if cv > 0.5:
            return exponential_smoothing_forecast(data_points, disk_capacity)

    # Default: linear regression (most interpretable)
    return lr_result


# ─── Helpers ───────────────────────────────────────────────────────────────────

def _compute_confidence(r_squared: float, data_points: int) -> float:
    """Compute confidence score from R² and sample size.

    Confidence increases with:
    - Better fit (higher R²)
    - More data points (up to a plateau)
    """
    # Base from R²
    base = r_squared * 0.7

    # Bonus from sample size (diminishing returns after 30 points)
    size_bonus = min(0.25, 0.01 * data_points)

    return round(min(0.95, base + size_bonus), 3)


def _empty_result(model_type: str, data_points: int) -> ForecastResult:
    """Return a zero-prediction result when insufficient data."""
    return ForecastResult(
        model_type=model_type,
        daily_growth_bytes=0.0,
        weekly_growth_bytes=0.0,
        predicted_total_30d=0,
        predicted_total_90d=0,
        exhaustion_date=None,
        days_until_full=None,
        confidence=0.0,
        trend="stable",
        r_squared=0.0,
        data_points_used=data_points,
    )

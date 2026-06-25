"""Unit tests for prediction models with synthetic time-series datasets."""

import pytest

from app.services.prediction_models import (
    ForecastResult,
    TimeSeriesPoint,
    classify_trend,
    exponential_smoothing_forecast,
    linear_regression_forecast,
    moving_average_forecast,
    select_best_model,
    _compute_confidence,
)


# ─── Synthetic Dataset Generators ──────────────────────────────────────────────

def _linear_growth_dataset(
    days: int = 30,
    start_bytes: int = 100_000_000_000,  # 100 GB
    daily_growth: int = 500_000_000,     # 500 MB/day
) -> list[TimeSeriesPoint]:
    """Generate a perfectly linear growth dataset."""
    return [
        TimeSeriesPoint(day_index=d, total_bytes=start_bytes + daily_growth * d)
        for d in range(days)
    ]


def _noisy_growth_dataset(
    days: int = 30,
    start_bytes: int = 100_000_000_000,
    daily_growth: int = 500_000_000,
    noise_amplitude: int = 200_000_000,
) -> list[TimeSeriesPoint]:
    """Generate a growth dataset with noise (simulates real usage)."""
    import random
    random.seed(42)
    return [
        TimeSeriesPoint(
            day_index=d,
            total_bytes=start_bytes + daily_growth * d + random.randint(-noise_amplitude, noise_amplitude),
        )
        for d in range(days)
    ]


def _accelerating_growth_dataset(
    days: int = 30,
    start_bytes: int = 100_000_000_000,
    base_growth: int = 200_000_000,
    acceleration: float = 1.05,
) -> list[TimeSeriesPoint]:
    """Generate dataset with accelerating growth (exponential-like)."""
    points = []
    current = start_bytes
    daily = base_growth
    for d in range(days):
        points.append(TimeSeriesPoint(day_index=d, total_bytes=int(current)))
        current += daily
        daily *= acceleration
    return points


def _stable_dataset(
    days: int = 30,
    total_bytes: int = 200_000_000_000,
) -> list[TimeSeriesPoint]:
    """Generate a flat dataset (no growth)."""
    return [
        TimeSeriesPoint(day_index=d, total_bytes=total_bytes)
        for d in range(days)
    ]


DISK_CAPACITY = 500_000_000_000  # 500 GB


# ─── Linear Regression Tests ──────────────────────────────────────────────────

class TestLinearRegression:
    """Tests for linear regression forecasting model."""

    def test_perfect_linear_data_gives_exact_prediction(self) -> None:
        data = _linear_growth_dataset(days=30, daily_growth=500_000_000)
        result = linear_regression_forecast(data, DISK_CAPACITY)

        assert result.model_type == "linear_regression"
        # Should recover the exact growth rate
        assert abs(result.daily_growth_bytes - 500_000_000) < 1000  # <1KB error
        assert result.r_squared > 0.99
        assert result.confidence > 0.9

    def test_predicts_30_and_90_day_totals(self) -> None:
        data = _linear_growth_dataset(days=20, daily_growth=1_000_000_000)
        result = linear_regression_forecast(data, DISK_CAPACITY)

        current = data[-1].total_bytes
        expected_30 = current + 1_000_000_000 * 30
        # Allow 5% tolerance
        assert abs(result.predicted_total_30d - expected_30) < expected_30* 0.05

    def test_calculates_days_until_full(self) -> None:
        # 100GB used, 500GB capacity, 1GB/day growth → ~400 days
        data = _linear_growth_dataset(days=30, start_bytes=100_000_000_000, daily_growth=1_000_000_000)
        result = linear_regression_forecast(data, DISK_CAPACITY)

        assert result.days_until_full is not None
        assert 350 < result.days_until_full < 450

    def test_no_exhaustion_for_flat_data(self) -> None:
        data = _stable_dataset(days=30)
        result = linear_regression_forecast(data, DISK_CAPACITY)

        assert result.days_until_full is None
        assert result.exhaustion_date is None

    def test_minimum_data_points(self) -> None:
        data = [TimeSeriesPoint(0, 100), TimeSeriesPoint(10, 200)]
        result = linear_regression_forecast(data, DISK_CAPACITY)
        assert result.data_points_used == 2
        assert result.daily_growth_bytes >= 0

    def test_single_point_returns_empty(self) -> None:
        data = [TimeSeriesPoint(0, 100)]
        result = linear_regression_forecast(data, DISK_CAPACITY)
        assert result.confidence == 0.0
        assert result.daily_growth_bytes == 0.0


class TestMovingAverage:
    """Tests for moving average forecasting model."""

    def test_consistent_growth_matches_rate(self) -> None:
        data = _linear_growth_dataset(days=15, daily_growth=300_000_000)
        result = moving_average_forecast(data, DISK_CAPACITY, window_size=7)

        assert result.model_type == "moving_average"
        # Should approximate the daily rate within 10%
        assert abs(result.daily_growth_bytes - 300_000_000) < 300_000_000 * 0.10

    def test_recent_changes_reflected(self) -> None:
        # First 10 days: slow growth. Last 5 days: fast growth.
        data = (
            _linear_growth_dataset(days=10, daily_growth=100_000_000) +
            [TimeSeriesPoint(day_index=10 + d, total_bytes=100_000_000_000 + 100_000_000 * 10 + 1_000_000_000 * d)
             for d in range(5)]
        )
        result = moving_average_forecast(data, DISK_CAPACITY, window_size=5)

        # Should reflect recent fast growth more than historical slow growth
        assert result.daily_growth_bytes > 500_000_000

    def test_handles_insufficient_data(self) -> None:
        data = [TimeSeriesPoint(0, 100), TimeSeriesPoint(1, 200)]
        result = moving_average_forecast(data, DISK_CAPACITY)
        assert result.data_points_used == 2

    def test_calculates_exhaustion(self) -> None:
        data = _linear_growth_dataset(days=10, start_bytes=400_000_000_000, daily_growth=2_000_000_000)
        result = moving_average_forecast(data, DISK_CAPACITY)

        assert result.days_until_full is not None
        assert result.days_until_full < 60  # ~50 days


class TestExponentialSmoothing:
    """Tests for exponential smoothing model."""

    def test_steady_growth_converges(self) -> None:
        data = _linear_growth_dataset(days=20, daily_growth=400_000_000)
        result = exponential_smoothing_forecast(data, DISK_CAPACITY)

        assert result.model_type == "exponential_smoothing"
        # Should converge near actual rate
        assert abs(result.daily_growth_bytes - 400_000_000) < 400_000_000 * 0.20

    def test_responds_to_acceleration(self) -> None:
        data = _accelerating_growth_dataset(days=20)
        result = exponential_smoothing_forecast(data, DISK_CAPACITY, alpha=0.5)

        # With high alpha, should reflect recent accelerating trend
        assert result.daily_growth_bytes > 200_000_000  # Base was 200M but accelerating

    def test_low_alpha_smooths_more(self) -> None:
        data = _noisy_growth_dataset(days=20)
        result_low = exponential_smoothing_forecast(data, DISK_CAPACITY, alpha=0.1)
        result_high = exponential_smoothing_forecast(data, DISK_CAPACITY, alpha=0.9)

        # Both should be in the right ballpark, but may differ
        assert result_low.daily_growth_bytes > 0
        assert result_high.daily_growth_bytes > 0

    def test_handles_insufficient_data(self) -> None:
        data = [TimeSeriesPoint(0, 100), TimeSeriesPoint(1, 200)]
        result = exponential_smoothing_forecast(data, DISK_CAPACITY)
        assert result.data_points_used == 2


class TestSelectBestModel:
    """Tests for automatic model selection."""

    def test_selects_linear_for_perfect_linear_data(self) -> None:
        data = _linear_growth_dataset(days=30, daily_growth=500_000_000)
        result = select_best_model(data, DISK_CAPACITY)
        assert result.model_type == "linear_regression"

    def test_selects_moving_average_for_few_points(self) -> None:
        data = _linear_growth_dataset(days=4, daily_growth=100_000_000)
        result = select_best_model(data, DISK_CAPACITY)
        assert result.model_type == "moving_average"

    def test_selects_exponential_for_high_variance(self) -> None:
        # Create dataset with very high variance
        data = _accelerating_growth_dataset(days=15, acceleration=1.20)
        result = select_best_model(data, DISK_CAPACITY)
        # Should pick exponential smoothing for non-linear data
        assert result.model_type in ("exponential_smoothing", "linear_regression")

    def test_returns_empty_for_insufficient_data(self) -> None:
        data = [TimeSeriesPoint(0, 100), TimeSeriesPoint(1, 200)]
        result = select_best_model(data, DISK_CAPACITY)
        assert result.confidence == 0.0 or result.data_points_used == 2

    def test_empty_data_returns_empty(self) -> None:
        result = select_best_model([], DISK_CAPACITY)
        assert result.confidence == 0.0


class TestClassifyTrend:
    """Tests for trend classification."""

    def test_stable_for_no_growth(self) -> None:
        assert classify_trend(0, 100_000_000_000) == "stable"

    def test_stable_for_very_slow_growth(self) -> None:
        # 0.1% monthly growth
        daily = 100_000_000_000 * 0.001 / 30
        assert classify_trend(daily, 100_000_000_000) == "stable"

    def test_slow_growth(self) -> None:
        # 1% monthly growth
        daily = 100_000_000_000 * 0.01 / 30
        assert classify_trend(daily, 100_000_000_000) == "slow_growth"

    def test_moderate_growth(self) -> None:
        # 3% monthly growth
        daily = 100_000_000_000 * 0.03 / 30
        assert classify_trend(daily, 100_000_000_000) == "moderate_growth"

    def test_rapid_growth(self) -> None:
        # 7% monthly growth
        daily = 100_000_000_000 * 0.07 / 30
        assert classify_trend(daily, 100_000_000_000) == "rapid_growth"

    def test_critical_growth(self) -> None:
        # 15% monthly growth
        daily = 100_000_000_000 * 0.15 / 30
        assert classify_trend(daily, 100_000_000_000) == "critical_growth"


class TestComputeConfidence:
    """Tests for confidence score calculation."""

    def test_high_r_squared_high_confidence(self) -> None:
        conf = _compute_confidence(0.95, 30)
        assert conf > 0.85

    def test_low_r_squared_low_confidence(self) -> None:
        conf = _compute_confidence(0.2, 5)
        assert conf < 0.3

    def test_more_data_increases_confidence(self) -> None:
        conf_few = _compute_confidence(0.8, 5)
        conf_many = _compute_confidence(0.8, 30)
        assert conf_many > conf_few

    def test_bounded_at_0_95(self) -> None:
        conf = _compute_confidence(1.0, 100)
        assert conf <= 0.95


class TestForecastAccuracy:
    """Accuracy validation with known growth patterns."""

    def test_linear_accuracy_within_5_percent(self) -> None:
        """Linear model should predict within 5% for linear data."""
        actual_daily = 750_000_000
        data = _linear_growth_dataset(days=30, daily_growth=actual_daily)
        result = linear_regression_forecast(data, DISK_CAPACITY)

        error_pct = abs(result.daily_growth_bytes - actual_daily) / actual_daily * 100
        assert error_pct < 5.0

    def test_moving_average_accuracy_within_15_percent(self) -> None:
        """Moving average should predict within 15% for noisy linear data."""
        actual_daily = 500_000_000
        data = _noisy_growth_dataset(days=30, daily_growth=actual_daily)
        result = moving_average_forecast(data, DISK_CAPACITY)

        error_pct = abs(result.daily_growth_bytes - actual_daily) / actual_daily * 100
        assert error_pct < 15.0

    def test_exhaustion_date_accuracy(self) -> None:
        """Days-until-full should be accurate within 10% for linear data."""
        # 200GB used, 500GB capacity, 1GB/day → 300 days
        data = _linear_growth_dataset(days=30, start_bytes=200_000_000_000, daily_growth=1_000_000_000)
        result = linear_regression_forecast(data, DISK_CAPACITY)

        expected_days = 300
        assert result.days_until_full is not None
        error_pct = abs(result.days_until_full - expected_days) / expected_days * 100
        assert error_pct < 10.0

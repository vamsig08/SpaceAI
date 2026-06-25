"""Unit tests for structured logging setup."""

import logging

from app.core.config import Settings
from app.core.logging import (
    bind_correlation_id,
    correlation_id_var,
    get_logger,
    setup_logging,
    unbind_correlation_id,
)


class TestSetupLogging:
    """Tests for logging initialization."""

    def test_setup_development_mode(self) -> None:
        settings = Settings(env="development", log_level="DEBUG")
        setup_logging(settings)

        root = logging.getLogger()
        assert root.level == logging.DEBUG
        assert len(root.handlers) >= 1

    def test_setup_production_mode(self) -> None:
        settings = Settings(env="production", log_level="INFO")
        setup_logging(settings)

        root = logging.getLogger()
        assert root.level == logging.INFO

    def test_suppresses_noisy_loggers(self) -> None:
        settings = Settings(env="production", log_level="INFO")
        setup_logging(settings)

        uvicorn_logger = logging.getLogger("uvicorn.access")
        assert uvicorn_logger.level == logging.WARNING


class TestGetLogger:
    """Tests for logger retrieval."""

    def test_returns_bound_logger(self) -> None:
        logger = get_logger("test_module")
        assert logger is not None

    def test_returns_named_logger(self) -> None:
        logger = get_logger("my.module")
        assert logger is not None


class TestCorrelationId:
    """Tests for correlation ID context management."""

    def test_bind_sets_context_var(self) -> None:
        bind_correlation_id("req-abc-123")
        assert correlation_id_var.get() == "req-abc-123"
        unbind_correlation_id()

    def test_unbind_clears_context_var(self) -> None:
        bind_correlation_id("req-xyz")
        unbind_correlation_id()
        assert correlation_id_var.get() is None

    def test_default_is_none(self) -> None:
        unbind_correlation_id()
        assert correlation_id_var.get() is None

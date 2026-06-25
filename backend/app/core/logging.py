"""Structured logging setup using structlog.

Provides JSON output in production, colored console output in development.
Supports correlation ID propagation via contextvars for tracing across
async boundaries and background tasks.
"""

import logging
import sys
from contextvars import ContextVar
from typing import Any

import structlog

from app.core.config import Environment, LogLevel, Settings

# Context variable for correlation ID propagation across async boundaries
correlation_id_var: ContextVar[str | None] = ContextVar("correlation_id", default=None)


def _get_log_level(level: LogLevel) -> int:
    """Convert LogLevel enum to stdlib logging level int."""
    mapping: dict[LogLevel, int] = {
        LogLevel.DEBUG: logging.DEBUG,
        LogLevel.INFO: logging.INFO,
        LogLevel.WARNING: logging.WARNING,
        LogLevel.ERROR: logging.ERROR,
        LogLevel.CRITICAL: logging.CRITICAL,
    }
    return mapping[level]


def _add_correlation_id(
    logger: Any, method_name: str, event_dict: dict[str, Any]
) -> dict[str, Any]:
    """Processor that injects correlation_id from contextvars into every log entry."""
    cid = correlation_id_var.get()
    if cid is not None:
        event_dict["correlation_id"] = cid
    return event_dict


def _add_app_context(
    logger: Any, method_name: str, event_dict: dict[str, Any]
) -> dict[str, Any]:
    """Processor that adds standard application context fields."""
    event_dict.setdefault("app", "spaceai")
    return event_dict


def setup_logging(settings: Settings) -> None:
    """Configure structlog and stdlib logging based on application settings.

    Must be called once at application startup before any logging occurs.

    Args:
        settings: Application settings controlling log level and format.
    """
    log_level = _get_log_level(settings.log_level)

    # Shared processors for all modes
    shared_processors: list[structlog.types.Processor] = [
        structlog.contextvars.merge_contextvars,
        _add_correlation_id,
        _add_app_context,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.UnicodeDecoder(),
    ]

    if settings.env == Environment.DEVELOPMENT:
        # Development: colored console output, human-readable
        renderer: structlog.types.Processor = structlog.dev.ConsoleRenderer(
            colors=True,
            pad_event=40,
        )
    else:
        # Production/Testing: JSON output for machine parsing
        shared_processors.append(structlog.processors.format_exc_info)
        renderer = structlog.processors.JSONRenderer()

    structlog.configure(
        processors=[
            *shared_processors,
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )

    # Configure stdlib logging to route through structlog's formatter
    formatter = structlog.stdlib.ProcessorFormatter(
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            renderer,
        ],
    )

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter)

    # Configure root logger
    root_logger = logging.getLogger()
    root_logger.handlers.clear()
    root_logger.addHandler(handler)
    root_logger.setLevel(log_level)

    # Suppress noisy third-party loggers
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
    logging.getLogger("sqlalchemy.engine").setLevel(
        logging.DEBUG if settings.debug else logging.WARNING
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)

    # Optional file handler
    if settings.log_file:
        file_handler = logging.FileHandler(settings.log_file)
        file_handler.setFormatter(formatter)
        root_logger.addHandler(file_handler)


def get_logger(name: str | None = None) -> structlog.stdlib.BoundLogger:
    """Get a bound structured logger.

    Args:
        name: Logger name, typically __name__ of the calling module.

    Returns:
        A bound structlog logger instance.
    """
    return structlog.get_logger(name)


def bind_correlation_id(correlation_id: str) -> None:
    """Set the correlation ID for the current async context.

    This ID will be automatically included in all subsequent log entries
    within the same context (request, background task, etc.).

    Args:
        correlation_id: Unique identifier for tracing related operations.
    """
    correlation_id_var.set(correlation_id)
    structlog.contextvars.bind_contextvars(correlation_id=correlation_id)


def unbind_correlation_id() -> None:
    """Clear the correlation ID from the current context."""
    correlation_id_var.set(None)
    structlog.contextvars.unbind_contextvars("correlation_id")

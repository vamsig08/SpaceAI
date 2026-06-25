"""Unit tests for custom exception hierarchy."""

from app.core.exceptions import (
    AIProviderError,
    ConflictError,
    NotFoundError,
    ScanError,
    SpaceAIError,
    TaskError,
    ValidationError,
)


class TestSpaceAIError:
    """Tests for base exception."""

    def test_has_message_and_code(self) -> None:
        err = SpaceAIError("something broke", "BROKEN")
        assert err.message == "something broke"
        assert err.code == "BROKEN"
        assert str(err) == "something broke"

    def test_default_code(self) -> None:
        err = SpaceAIError("oops")
        assert err.code == "INTERNAL_ERROR"


class TestNotFoundError:
    """Tests for NotFoundError."""

    def test_formats_message(self) -> None:
        err = NotFoundError("scan", "abc-123")
        assert "scan" in err.message
        assert "abc-123" in err.message
        assert err.code == "SCAN_NOT_FOUND"
        assert err.entity == "scan"
        assert err.entity_id == "abc-123"


class TestConflictError:
    """Tests for ConflictError."""

    def test_uses_conflict_code(self) -> None:
        err = ConflictError("already running")
        assert err.code == "CONFLICT"


class TestValidationError:
    """Tests for ValidationError."""

    def test_includes_field(self) -> None:
        err = ValidationError("invalid path", field="root_path")
        assert err.field == "root_path"
        assert err.code == "VALIDATION_ERROR"


class TestScanError:
    """Tests for ScanError."""

    def test_includes_scan_id(self) -> None:
        err = ScanError("permission denied", scan_id="scan-1")
        assert err.scan_id == "scan-1"


class TestTaskError:
    """Tests for TaskError."""

    def test_includes_task_id(self) -> None:
        err = TaskError("timed out", task_id="task-1")
        assert err.task_id == "task-1"


class TestAIProviderError:
    """Tests for AIProviderError."""

    def test_includes_provider(self) -> None:
        err = AIProviderError("rate limited", provider="openai")
        assert err.provider == "openai"
        assert err.code == "AI_PROVIDER_ERROR"

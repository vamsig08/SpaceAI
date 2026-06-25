"""Custom exception hierarchy for SpaceAI.

All application-specific exceptions inherit from SpaceAIError.
This enables consistent error handling in API middleware.
"""


class SpaceAIError(Exception):
    """Base exception for all SpaceAI application errors."""

    def __init__(self, message: str, code: str = "INTERNAL_ERROR") -> None:
        self.message = message
        self.code = code
        super().__init__(message)


class NotFoundError(SpaceAIError):
    """Raised when a requested resource does not exist."""

    def __init__(self, entity: str, entity_id: str) -> None:
        self.entity = entity
        self.entity_id = entity_id
        super().__init__(
            message=f"{entity} with id '{entity_id}' not found",
            code=f"{entity.upper()}_NOT_FOUND",
        )


class ConflictError(SpaceAIError):
    """Raised when an operation conflicts with current state."""

    def __init__(self, message: str) -> None:
        super().__init__(message=message, code="CONFLICT")


class ValidationError(SpaceAIError):
    """Raised when input validation fails at the service layer."""

    def __init__(self, message: str, field: str | None = None) -> None:
        self.field = field
        super().__init__(message=message, code="VALIDATION_ERROR")


class ScanError(SpaceAIError):
    """Raised when a filesystem scan operation fails."""

    def __init__(self, message: str, scan_id: str | None = None) -> None:
        self.scan_id = scan_id
        super().__init__(message=message, code="SCAN_ERROR")


class TaskError(SpaceAIError):
    """Raised when a background task operation fails."""

    def __init__(self, message: str, task_id: str | None = None) -> None:
        self.task_id = task_id
        super().__init__(message=message, code="TASK_ERROR")


class AIProviderError(SpaceAIError):
    """Raised when an AI provider call fails."""

    def __init__(self, message: str, provider: str | None = None) -> None:
        self.provider = provider
        super().__init__(message=message, code="AI_PROVIDER_ERROR")

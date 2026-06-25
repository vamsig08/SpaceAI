"""Application configuration via pydantic-settings.

All settings are loaded from environment variables with the SPACEAI_ prefix.
A .env file is loaded automatically if present in the working directory.
"""

from enum import Enum
from pathlib import Path
from typing import Annotated

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Environment(str, Enum):
    """Application runtime environment."""

    DEVELOPMENT = "development"
    PRODUCTION = "production"
    TESTING = "testing"


class AIProvider(str, Enum):
    """Supported AI provider backends."""

    OPENAI = "openai"
    OLLAMA = "ollama"


class LogLevel(str, Enum):
    """Supported log levels."""

    DEBUG = "DEBUG"
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"
    CRITICAL = "CRITICAL"


class Settings(BaseSettings):
    """SpaceAI application settings.

    All settings are configurable via environment variables prefixed with SPACEAI_.
    Example: SPACEAI_DB_PATH=./data/spaceai.db
    """

    model_config = SettingsConfigDict(
        env_prefix="SPACEAI_",
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # Application
    env: Environment = Environment.DEVELOPMENT
    debug: bool = True
    host: str = "0.0.0.0"
    port: int = 8000
    app_name: str = "SpaceAI"
    version: str = "0.1.0"

    # Database
    db_path: Annotated[Path, Field(description="Path to SQLite database file")] = Path(
        "./data/spaceai.db"
    )

    # Scanner
    scanner_batch_size: Annotated[
        int, Field(ge=100, le=10000, description="Records per batch insert")
    ] = 1000
    scanner_checkpoint_interval: Annotated[
        int, Field(ge=1000, le=100000, description="Files between checkpoints")
    ] = 10000
    scanner_thread_pool_size: Annotated[
        int, Field(ge=1, le=32, description="Thread pool workers for filesystem I/O")
    ] = 4

    # AI Provider
    ai_provider: AIProvider = AIProvider.OLLAMA
    openai_api_key: str = ""
    openai_model: str = "gpt-4o"
    ollama_base_url: str = "http://localhost:11434"
    ollama_model: str = "llama3"

    # Logging
    log_level: LogLevel = LogLevel.DEBUG
    log_file: str | None = None

    # Cleanup
    trash_retention_days: Annotated[
        int, Field(ge=1, le=365, description="Days to retain trashed files")
    ] = 30

    # CORS
    cors_origins: list[str] = ["http://localhost:3000"]

    @field_validator("db_path", mode="before")
    @classmethod
    def resolve_db_path(cls, v: str | Path) -> Path:
        """Ensure database path is resolved and parent directory exists."""
        path = Path(v) if isinstance(v, str) else v
        return path

    @property
    def database_url(self) -> str:
        """Construct async SQLAlchemy database URL."""
        return f"sqlite+aiosqlite:///{self.db_path.resolve()}"

    @property
    def is_development(self) -> bool:
        """Check if running in development mode."""
        return self.env == Environment.DEVELOPMENT

    @property
    def is_production(self) -> bool:
        """Check if running in production mode."""
        return self.env == Environment.PRODUCTION

    @property
    def is_testing(self) -> bool:
        """Check if running in test mode."""
        return self.env == Environment.TESTING


def get_settings() -> Settings:
    """Create and return application settings.

    Settings are loaded fresh each call to support test overrides.
    For production, cache this at the module level or via FastAPI dependency.
    """
    return Settings()

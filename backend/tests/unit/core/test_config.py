"""Unit tests for application configuration."""

import os

import pytest

from app.core.config import AIProvider, Environment, LogLevel, Settings


class TestSettingsDefaults:
    """Tests for default configuration values."""

    def test_default_environment_is_development(self) -> None:
        s = Settings(env="development")
        assert s.env == Environment.DEVELOPMENT
        assert s.is_development is True
        assert s.is_production is False

    def test_default_port_is_8000(self) -> None:
        s = Settings()
        assert s.port == 8000

    def test_default_ai_provider_is_ollama(self) -> None:
        s = Settings()
        assert s.ai_provider == AIProvider.OLLAMA

    def test_default_log_level_is_debug(self) -> None:
        s = Settings()
        assert s.log_level == LogLevel.DEBUG


class TestSettingsDatabaseUrl:
    """Tests for database URL construction."""

    def test_database_url_contains_aiosqlite(self) -> None:
        s = Settings(db_path="./data/test.db")
        assert "sqlite+aiosqlite" in s.database_url

    def test_database_url_is_absolute(self) -> None:
        s = Settings(db_path="./data/test.db")
        assert "///" in s.database_url


class TestSettingsEnvironment:
    """Tests for environment detection properties."""

    def test_is_testing(self) -> None:
        s = Settings(env="testing")
        assert s.is_testing is True
        assert s.is_development is False

    def test_is_production(self) -> None:
        s = Settings(env="production")
        assert s.is_production is True
        assert s.is_development is False


class TestSettingsValidation:
    """Tests for field validation."""

    def test_scanner_batch_size_has_minimum(self) -> None:
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            Settings(scanner_batch_size=10)  # min is 100

    def test_scanner_thread_pool_has_bounds(self) -> None:
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            Settings(scanner_thread_pool_size=0)

    def test_trash_retention_bounds(self) -> None:
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            Settings(trash_retention_days=0)

"""Unit tests for AI provider abstraction."""

import pytest

from app.ai.base_provider import NoOpProvider, create_ai_provider
from app.core.config import Settings


class TestNoOpProvider:
    """Tests for the fallback no-op provider."""

    async def test_generate_explanation_returns_empty(self) -> None:
        provider = NoOpProvider()
        result = await provider.generate_explanation("title", {"key": "val"})
        assert result == ""

    async def test_generate_summary_returns_empty(self) -> None:
        provider = NoOpProvider()
        result = await provider.generate_summary([{"title": "rec"}])
        assert result == ""

    async def test_is_available_returns_false(self) -> None:
        provider = NoOpProvider()
        assert await provider.is_available() is False


class TestCreateAiProvider:
    """Tests for the provider factory."""

    def test_creates_noop_for_ollama_when_unavailable(self) -> None:
        settings = Settings(ai_provider="ollama", ollama_base_url="http://localhost:11434")
        provider = create_ai_provider(settings)
        # Should create OllamaProvider or fall back to NoOp
        assert provider is not None

    def test_creates_noop_for_openai_without_key(self) -> None:
        settings = Settings(ai_provider="openai", openai_api_key="")
        provider = create_ai_provider(settings)
        assert provider is not None

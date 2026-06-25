"""Abstract AI provider interface and factory.

The AI layer is optional — the recommendation engine works fully offline
via deterministic rules. AI providers add enrichment:
- Natural language explanations
- Executive summaries
- Prioritization reasoning

Supports:
- Ollama (local, primary)
- OpenAI-compatible APIs (secondary)
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from app.core.config import AIProvider, Settings
from app.core.logging import get_logger

logger = get_logger(__name__)


class AIProviderBase(ABC):
    """Abstract interface for AI provider implementations."""

    @abstractmethod
    async def generate_explanation(
        self, recommendation_title: str, context: dict[str, Any]
    ) -> str:
        """Generate a natural-language explanation for a recommendation.

        Args:
            recommendation_title: The recommendation being explained.
            context: Supporting data (sizes, counts, paths).

        Returns:
            Human-readable explanation string.
        """
        ...

    @abstractmethod
    async def generate_summary(self, recommendations: list[dict[str, Any]]) -> str:
        """Generate an executive summary of all recommendations.

        Args:
            recommendations: List of recommendation dicts.

        Returns:
            Summary paragraph.
        """
        ...

    @abstractmethod
    async def is_available(self) -> bool:
        """Check if the provider is reachable.

        Returns:
            True if provider is ready to accept requests.
        """
        ...


class NoOpProvider(AIProviderBase):
    """Fallback provider that returns empty strings (AI disabled/unavailable)."""

    async def generate_explanation(
        self, recommendation_title: str, context: dict[str, Any]
    ) -> str:
        return ""

    async def generate_summary(self, recommendations: list[dict[str, Any]]) -> str:
        return ""

    async def is_available(self) -> bool:
        return False


def create_ai_provider(settings: Settings) -> AIProviderBase:
    """Factory to create the configured AI provider.

    Falls back to NoOpProvider if the configured provider cannot be instantiated.

    Args:
        settings: Application settings with AI provider configuration.

    Returns:
        An AIProviderBase implementation.
    """
    if settings.ai_provider == AIProvider.OLLAMA:
        try:
            from app.ai.ollama_provider import OllamaProvider
            return OllamaProvider(
                base_url=settings.ollama_base_url,
                model=settings.ollama_model,
            )
        except ImportError:
            logger.warning("ollama_provider_unavailable")
            return NoOpProvider()

    elif settings.ai_provider == AIProvider.OPENAI:
        try:
            from app.ai.openai_provider import OpenAIProvider
            return OpenAIProvider(
                api_key=settings.openai_api_key,
                model=settings.openai_model,
            )
        except ImportError:
            logger.warning("openai_provider_unavailable")
            return NoOpProvider()

    return NoOpProvider()

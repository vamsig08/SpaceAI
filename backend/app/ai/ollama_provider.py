"""Ollama AI provider — local LLM integration.

Connects to a local Ollama instance for generating natural-language
explanations and summaries. Falls back gracefully when unavailable.
"""

from __future__ import annotations

from typing import Any

import httpx

from app.ai.base_provider import AIProviderBase
from app.core.logging import get_logger

logger = get_logger(__name__)

RECOMMENDATION_EXPLAIN_PROMPT = """You are a storage optimization assistant. 
Explain the following recommendation in 2-3 sentences:

Title: {title}
Context: {context}

Be concise and actionable."""

SUMMARY_PROMPT = """Summarize these storage optimization recommendations in a brief executive summary (3-5 sentences):

{recommendations}

Focus on total recoverable space and highest-priority actions."""


class OllamaProvider(AIProviderBase):
    """AI provider using a local Ollama instance."""

    def __init__(self, base_url: str, model: str, timeout: float = 60.0) -> None:
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._timeout = timeout
        self._client = httpx.AsyncClient(timeout=timeout)

    async def generate_explanation(
        self, recommendation_title: str, context: dict[str, Any]
    ) -> str:
        """Generate explanation via Ollama chat completion.

        Args:
            recommendation_title: What to explain.
            context: Supporting data.

        Returns:
            Generated explanation text, or empty string on failure.
        """
        prompt = RECOMMENDATION_EXPLAIN_PROMPT.format(
            title=recommendation_title,
            context=str(context)[:500],
        )

        try:
            response = await self._client.post(
                f"{self._base_url}/api/generate",
                json={"model": self._model, "prompt": prompt, "stream": False},
            )
            response.raise_for_status()
            data = response.json()
            return data.get("response", "").strip()
        except (httpx.HTTPError, KeyError, ValueError) as e:
            logger.warning("ollama_explanation_failed", error=str(e))
            return ""

    async def generate_summary(self, recommendations: list[dict[str, Any]]) -> str:
        """Generate executive summary of recommendations.

        Args:
            recommendations: List of recommendation dicts.

        Returns:
            Summary text, or empty string on failure.
        """
        rec_text = "\n".join(
            f"- [{r.get('priority', 'medium')}] {r.get('title', '')}: {r.get('description', '')}"
            for r in recommendations[:10]
        )
        prompt = SUMMARY_PROMPT.format(recommendations=rec_text)

        try:
            response = await self._client.post(
                f"{self._base_url}/api/generate",
                json={"model": self._model, "prompt": prompt, "stream": False},
            )
            response.raise_for_status()
            data = response.json()
            return data.get("response", "").strip()
        except (httpx.HTTPError, KeyError, ValueError) as e:
            logger.warning("ollama_summary_failed", error=str(e))
            return ""

    async def is_available(self) -> bool:
        """Check if Ollama is reachable and the model is loaded.

        Returns:
            True if Ollama responds to a tags request.
        """
        try:
            response = await self._client.get(f"{self._base_url}/api/tags")
            return response.status_code == 200
        except httpx.HTTPError:
            return False

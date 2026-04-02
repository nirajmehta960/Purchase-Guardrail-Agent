"""
LLM provider abstraction — Groq (primary) and mock fallback.
"""

from __future__ import annotations

import logging
import os
from abc import ABC, abstractmethod
from typing import Optional

logger = logging.getLogger(__name__)


class BaseLLMProvider(ABC):
    """Minimal interface used by intent_parser and response_generator."""

    provider_name: str = "base"

    @abstractmethod
    def generate(self, prompt: str, max_tokens: int = 512, temperature: float = 0.3) -> str:
        """Return model text completion for the given prompt."""


class MockProvider(BaseLLMProvider):
    """Deterministic stub when no API key or for tests."""

    provider_name = "mock"

    def generate(self, prompt: str, max_tokens: int = 512, temperature: float = 0.3) -> str:
        # Intent prompts: return empty product_reference so intent_parser uses heuristics
        if "intent" in prompt.lower() and "json" in prompt.lower():
            return '{"intent": "purchase_query", "product_reference": null, "user_context": null}'
        return (
            "Recommendation follows the deterministic engine. "
            "Review your emergency fund and debt obligations before purchasing."
        )


class GroqProvider(BaseLLMProvider):
    """Groq OpenAI-compatible chat completions."""

    provider_name = "groq"
    _DEFAULT_MODEL = "llama-3.3-70b-versatile"

    def __init__(self, api_key: Optional[str] = None, model: Optional[str] = None):
        key = api_key or os.environ.get("GROQ_API_KEY", "").strip()
        if not key:
            raise ValueError("GROQ_API_KEY is not set")
        self._api_key = key
        self._model = model or os.environ.get("GROQ_MODEL", self._DEFAULT_MODEL)

    def generate(self, prompt: str, max_tokens: int = 512, temperature: float = 0.3) -> str:
        try:
            import urllib.request
            import json

            url = "https://api.groq.com/openai/v1/chat/completions"
            body = {
                "model": self._model,
                "messages": [
                    {"role": "system", "content": "You are a concise assistant for a financial purchase advisor."},
                    {"role": "user", "content": prompt},
                ],
                "max_tokens": max_tokens,
                "temperature": temperature,
            }
            req = urllib.request.Request(
                url,
                data=json.dumps(body).encode("utf-8"),
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {self._api_key}",
                },
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=60) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            return data["choices"][0]["message"]["content"].strip()
        except Exception as e:
            logger.warning("Groq generate failed: %s", e)
            raise


def get_provider() -> BaseLLMProvider:
    """Prefer Groq when GROQ_API_KEY is set; otherwise mock."""
    try:
        if os.environ.get("GROQ_API_KEY", "").strip():
            p = GroqProvider()
            logger.info("Using LLM provider: %s", p.provider_name)
            return p
    except Exception as e:
        logger.warning("Could not init GroqProvider: %s — using mock", e)
    logger.info("Using LLM provider: mock")
    return MockProvider()

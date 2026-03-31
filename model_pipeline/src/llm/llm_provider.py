"""
LLM Provider Abstraction — Strategy Pattern.

Allows swapping between mock (template-based) and real LLM providers
(OpenAI, Gemini, Claude) without changing calling code.

Usage:
    from llm.llm_provider import get_provider
    provider = get_provider("mock")
    response = provider.generate(system_prompt="...", user_message="...")
"""

from __future__ import annotations

import json
import logging
from abc import ABC, abstractmethod
from typing import Any

from llm.config import LLMConfig

logger = logging.getLogger(__name__)


class LLMProvider(ABC):
    """Abstract base for LLM providers."""

    @abstractmethod
    def generate(self, system_prompt: str, user_message: str, **kwargs) -> str:
        """Generate a free-form text response."""
        ...

    @abstractmethod
    def generate_structured(
        self, system_prompt: str, user_message: str, schema: dict, **kwargs
    ) -> dict:
        """Generate a structured (JSON) response matching the given schema."""
        ...

    @property
    @abstractmethod
    def provider_name(self) -> str:
        ...


class MockProvider(LLMProvider):
    """
    Template-based mock — no API calls, fully deterministic, for testing.

    generate():
        Returns a canned response based on keywords in the user message.
    generate_structured():
        Returns a parsed JSON dict based on keyword analysis.
    """

    @property
    def provider_name(self) -> str:
        return "mock"

    def generate(self, system_prompt: str, user_message: str, **kwargs) -> str:
        logger.debug("MockProvider.generate called with: %s", user_message[:80])
        return f"[Mock LLM Response] Processed input: {user_message[:100]}"

    def generate_structured(
        self, system_prompt: str, user_message: str, schema: dict, **kwargs
    ) -> dict:
        logger.debug("MockProvider.generate_structured called")
        # Return a default structure matching common schema patterns
        result: dict[str, Any] = {}
        if "intent" in str(schema):
            result["intent"] = "purchase_query"
            result["product_reference"] = user_message
            result["user_context"] = None
        return result


class OpenAIProvider(LLMProvider):
    """OpenAI GPT integration via the openai SDK."""

    def __init__(self):
        try:
            import openai  # noqa: F811
        except ImportError:
            raise ImportError(
                "OpenAI provider requires the 'openai' package. "
                "Install with: pip install openai"
            )
        if not LLMConfig.OPENAI_API_KEY:
            raise ValueError("OPENAI_API_KEY environment variable is not set.")
        self._client = openai.OpenAI(api_key=LLMConfig.OPENAI_API_KEY)
        self._model = LLMConfig.OPENAI_MODEL
        logger.info("OpenAI provider initialized with model: %s", self._model)

    @property
    def provider_name(self) -> str:
        return "openai"

    def generate(self, system_prompt: str, user_message: str, **kwargs) -> str:
        response = self._client.chat.completions.create(
            model=self._model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_message},
            ],
            temperature=kwargs.get("temperature", LLMConfig.TEMPERATURE),
            max_tokens=kwargs.get("max_tokens", LLMConfig.MAX_TOKENS),
        )
        return response.choices[0].message.content.strip()

    def generate_structured(
        self, system_prompt: str, user_message: str, schema: dict, **kwargs
    ) -> dict:
        enhanced_prompt = (
            f"{system_prompt}\n\n"
            f"You MUST respond with valid JSON matching this schema:\n"
            f"{json.dumps(schema, indent=2)}\n"
            f"Return ONLY the JSON object, no other text."
        )
        raw = self.generate(enhanced_prompt, user_message, **kwargs)
        # Strip markdown fencing if present
        raw = raw.strip()
        if raw.startswith("```"):
            raw = raw.split("\n", 1)[1] if "\n" in raw else raw[3:]
            if raw.endswith("```"):
                raw = raw[:-3]
            raw = raw.strip()
        return json.loads(raw)


class GeminiProvider(LLMProvider):
    """Google Gemini integration via the google-genai SDK (v1.x+)."""

    def __init__(self):
        try:
            from google import genai as genai_sdk  # noqa: F811
        except ImportError:
            raise ImportError(
                "Gemini provider requires the 'google-genai' package. "
                "Install with: pip install google-genai"
            )
        if not LLMConfig.GEMINI_API_KEY:
            raise ValueError("GEMINI_API_KEY environment variable is not set.")
        self._client = genai_sdk.Client(api_key=LLMConfig.GEMINI_API_KEY)
        self._model_name = LLMConfig.GEMINI_MODEL
        logger.info("Gemini provider initialized with model: %s", self._model_name)

    @property
    def provider_name(self) -> str:
        return "gemini"

    def generate(self, system_prompt: str, user_message: str, **kwargs) -> str:
        from google.genai import types

        response = self._client.models.generate_content(
            model=self._model_name,
            contents=user_message,
            config=types.GenerateContentConfig(
                system_instruction=system_prompt,
                temperature=kwargs.get("temperature", LLMConfig.TEMPERATURE),
                max_output_tokens=kwargs.get("max_tokens", LLMConfig.MAX_TOKENS),
            ),
        )
        return response.text.strip()

    def generate_structured(
        self, system_prompt: str, user_message: str, schema: dict, **kwargs
    ) -> dict:
        enhanced_prompt = (
            f"{system_prompt}\n\n"
            f"Respond with valid JSON matching this schema:\n"
            f"{json.dumps(schema, indent=2)}\n"
            f"Return ONLY the JSON object."
        )
        raw = self.generate(enhanced_prompt, user_message, **kwargs)
        raw = raw.strip()
        if raw.startswith("```"):
            raw = raw.split("\n", 1)[1] if "\n" in raw else raw[3:]
            if raw.endswith("```"):
                raw = raw[:-3]
            raw = raw.strip()
        return json.loads(raw)


class ClaudeProvider(LLMProvider):
    """Anthropic Claude integration via the anthropic SDK."""

    def __init__(self):
        try:
            import anthropic  # noqa: F811
        except ImportError:
            raise ImportError(
                "Claude provider requires the 'anthropic' package. "
                "Install with: pip install anthropic"
            )
        if not LLMConfig.ANTHROPIC_API_KEY:
            raise ValueError("ANTHROPIC_API_KEY environment variable is not set.")
        self._client = anthropic.Anthropic(api_key=LLMConfig.ANTHROPIC_API_KEY)
        self._model = LLMConfig.ANTHROPIC_MODEL
        logger.info("Claude provider initialized with model: %s", self._model)

    @property
    def provider_name(self) -> str:
        return "claude"

    def generate(self, system_prompt: str, user_message: str, **kwargs) -> str:
        response = self._client.messages.create(
            model=self._model,
            system=system_prompt,
            messages=[{"role": "user", "content": user_message}],
            temperature=kwargs.get("temperature", LLMConfig.TEMPERATURE),
            max_tokens=kwargs.get("max_tokens", LLMConfig.MAX_TOKENS),
        )
        return response.content[0].text.strip()

    def generate_structured(
        self, system_prompt: str, user_message: str, schema: dict, **kwargs
    ) -> dict:
        enhanced_prompt = (
            f"{system_prompt}\n\n"
            f"Respond with valid JSON matching this schema:\n"
            f"{json.dumps(schema, indent=2)}\n"
            f"Return ONLY the JSON object."
        )
        raw = self.generate(enhanced_prompt, user_message, **kwargs)
        raw = raw.strip()
        if raw.startswith("```"):
            raw = raw.split("\n", 1)[1] if "\n" in raw else raw[3:]
            if raw.endswith("```"):
                raw = raw[:-3]
            raw = raw.strip()
        return json.loads(raw)


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

_PROVIDERS: dict[str, type[LLMProvider]] = {
    "mock": MockProvider,
    "openai": OpenAIProvider,
    "gemini": GeminiProvider,
    "claude": ClaudeProvider,
}

# Singleton cache — avoid re-initializing providers (especially real ones)
_provider_cache: dict[str, LLMProvider] = {}


def get_provider(provider_name: str | None = None) -> LLMProvider:
    """
    Get or create an LLM provider instance.

    Args:
        provider_name: One of "mock", "openai", "gemini", "claude".
                       Defaults to LLMConfig.PROVIDER (env: LLM_PROVIDER).

    Returns:
        An LLMProvider instance (cached singleton per provider name).
    """
    name = provider_name or LLMConfig.PROVIDER
    if name not in _PROVIDERS:
        raise ValueError(
            f"Unknown LLM provider '{name}'. "
            f"Choose from: {list(_PROVIDERS.keys())}"
        )

    if name not in _provider_cache:
        logger.info("Initializing LLM provider: %s", name)
        _provider_cache[name] = _PROVIDERS[name]()

    return _provider_cache[name]

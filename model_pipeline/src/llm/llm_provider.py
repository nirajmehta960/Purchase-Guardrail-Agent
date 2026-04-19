"""
LLM provider abstraction for SavVio.

Active provider: Vertex AI (GCP-native, no API key required).
  Model: gemini-2.5-flash
  Why: strong instruction-following for structured 4-part financial advice prompts,
       auth via Cloud Run service account (no secrets), same GCP region = low latency.
  Requires: roles/aiplatform.user on the Cloud Run service account.
  Local dev: run `gcloud auth application-default login` and set VERTEX_PROJECT.

Fallback: MockProvider (deterministic stub for tests / no-key environments).

Previous providers (OpenRouter, Gemini direct, Groq) are preserved below but commented out.
Re-enable by swapping get_provider() and uncommenting the class definitions.
"""

from __future__ import annotations

import json
import logging
import os
import time
import urllib.error
import urllib.request
from abc import ABC, abstractmethod
from typing import List, Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Shared system message — canonical persona + rules for every LLM call.
# Imported from prompts/system_prompt.py so there is one source of truth.
# ---------------------------------------------------------------------------
from llm.prompts.system_prompt import SYSTEM_PROMPT as _SYSTEM_MESSAGE

# ---------------------------------------------------------------------------
# Retry helper — handles transient 429 / 503 errors.
# ---------------------------------------------------------------------------
_RETRYABLE_CODES = {429, 503}
_RETRY_DELAYS = (4, 10)  # seconds; two retries before propagating the error


def _post_with_retry(req: urllib.request.Request, timeout: int = 60) -> bytes:
    """POST the request, retrying on retryable HTTP errors with backoff."""
    last_exc: Exception = RuntimeError("no attempts made")
    delays = list(_RETRY_DELAYS) + [None]
    for attempt, delay in enumerate(delays, start=1):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.read()
        except urllib.error.HTTPError as e:
            last_exc = e
            if e.code in _RETRYABLE_CODES and delay is not None:
                logger.warning("HTTP %s on attempt %d — retrying in %ds", e.code, attempt, delay)
                time.sleep(delay)
            else:
                raise
    raise last_exc


# ---------------------------------------------------------------------------
# Base interface
# ---------------------------------------------------------------------------

class BaseLLMProvider(ABC):
    """Minimal interface used by intent_parser and response_generator."""

    provider_name: str = "base"

    @abstractmethod
    def generate(self, prompt: str, max_tokens: int = 512, temperature: float = 0.3) -> str:
        """Return model text completion for the given prompt."""


# ---------------------------------------------------------------------------
# Backward compatibility
# ---------------------------------------------------------------------------
# Older code paths (and some modules in this repo) import `LLMProvider` from
# this module. The current abstraction is `BaseLLMProvider`, so we alias it.
# This avoids runtime ImportError in the API inference pipeline.
LLMProvider = BaseLLMProvider


# ---------------------------------------------------------------------------
# Mock provider — deterministic stub for tests / no-key environments.
# ---------------------------------------------------------------------------

class MockProvider(BaseLLMProvider):
    provider_name = "mock"

    def generate(self, prompt: str, max_tokens: int = 512, temperature: float = 0.3) -> str:
        if "intent" in prompt.lower() and "json" in prompt.lower():
            return '{"intent": "purchase_query", "product_reference": null, "user_context": null}'
        return (
            "Recommendation follows the deterministic engine. "
            "Review your emergency fund and debt obligations before purchasing."
        )


# ---------------------------------------------------------------------------
# OpenRouter provider — active, paid, no rate limits.
# ---------------------------------------------------------------------------

class OpenRouterProvider(BaseLLMProvider):
    """
    OpenRouter unified API (OpenAI-compatible format).

    Model: google/gemini-2.5-flash
      - Strong instruction-following for structured financial advice prompts
      - ~$0.30 / M input tokens, $2.50 / M output tokens
      - Production-stable (no deprecation), thinking mode off by default
      - No RPM / RPD limits on paid credits

    Switch model via OPENROUTER_MODEL env var if needed, e.g.:
      meta-llama/llama-3.3-70b-instruct  (~$0.12/M in, $0.30/M out)
      mistralai/mistral-small-3.1-24b-instruct  (~$0.10/M in, $0.30/M out)
    """

    provider_name = "openrouter"
    _API_URL = "https://openrouter.ai/api/v1/chat/completions"
    _DEFAULT_MODEL = "google/gemini-2.5-flash"

    def __init__(self, api_key: Optional[str] = None, model: Optional[str] = None):
        key = api_key or os.environ.get("OPEN_ROUTER_API_KEY", "").strip()
        if not key:
            raise ValueError("OPEN_ROUTER_API_KEY is not set")
        self._api_key = key
        self._model = model or os.environ.get("OPENROUTER_MODEL", self._DEFAULT_MODEL)

    def generate(self, prompt: str, max_tokens: int = 512, temperature: float = 0.3) -> str:
        body = {
            "model": self._model,
            "messages": [
                {"role": "system", "content": _SYSTEM_MESSAGE},
                {"role": "user", "content": prompt},
            ],
            "max_tokens": max_tokens,
            "temperature": temperature,
        }
        req = urllib.request.Request(
            self._API_URL,
            data=json.dumps(body).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self._api_key}",
                "HTTP-Referer": "https://savvio.ai",
                "X-Title": "SavVio",
            },
            method="POST",
        )
        raw = _post_with_retry(req)
        data = json.loads(raw.decode("utf-8"))
        return data["choices"][0]["message"]["content"].strip()     

# ---------------------------------------------------------------------------
# Vertex AI provider — active, GCP-native, no API key required.
# ---------------------------------------------------------------------------

class VertexAIProvider(BaseLLMProvider):
    """
    Google Vertex AI — Gemini via GCP-native auth (Application Default Credentials).

    On Cloud Run the service account token is fetched automatically.
    Locally: run `gcloud auth application-default login` and set VERTEX_PROJECT.

    Model: gemini-2.5-flash (latest stable on Vertex AI, lower latency in GCP).
    Cost: ~$0.075/M input tokens, $0.30/M output tokens (direct GCP pricing).
    No API key needed — IAM controls access via roles/aiplatform.user.
    """

    provider_name = "vertex"
    # Defaults can be overridden via env vars so the same image deploys to any
    # GCP region/model without code changes.
    _DEFAULT_MODEL = os.environ.get("VERTEX_DEFAULT_MODEL", "gemini-2.5-flash")
    _DEFAULT_LOCATION = os.environ.get(
        "VERTEX_DEFAULT_LOCATION",
        os.environ.get("GCP_REGION", "us-east1"),
    )

    def __init__(
        self,
        project: Optional[str] = None,
        location: Optional[str] = None,
        model: Optional[str] = None,
    ):
        self._project = (
            project
            or os.environ.get("VERTEX_PROJECT")
            or os.environ.get("GCP_PROJECT_ID", "").strip()
            or os.environ.get("GOOGLE_CLOUD_PROJECT", "").strip()
        )
        if not self._project:
            raise ValueError(
                "Vertex AI project not set — provide VERTEX_PROJECT, GCP_PROJECT_ID, or GOOGLE_CLOUD_PROJECT env var"
            )
        self._location = location or os.environ.get("VERTEX_LOCATION", self._DEFAULT_LOCATION)
        self._model = model or os.environ.get("VERTEX_MODEL", self._DEFAULT_MODEL)

    def _get_access_token(self) -> str:
        """Fetch a GCP access token using Application Default Credentials."""
        try:
            import google.auth
            import google.auth.transport.requests as google_requests

            creds, _ = google.auth.default(
                scopes=["https://www.googleapis.com/auth/cloud-platform"]
            )
            creds.refresh(google_requests.Request())
            return creds.token
        except ImportError:
            # Fallback: fetch from the GCP metadata server (Cloud Run / GCE only)
            req = urllib.request.Request(
                "http://metadata.google.internal/computeMetadata/v1/instance/service-accounts/default/token",
                headers={"Metadata-Flavor": "Google"},
            )
            raw = urllib.request.urlopen(req, timeout=5).read()
            return json.loads(raw.decode("utf-8"))["access_token"]

    def generate(self, prompt: str, max_tokens: int = 512, temperature: float = 0.3) -> str:
        url = (
            f"https://{self._location}-aiplatform.googleapis.com/v1"
            f"/projects/{self._project}/locations/{self._location}"
            f"/publishers/google/models/{self._model}:generateContent"
        )
        body = {
            "system_instruction": {"parts": [{"text": _SYSTEM_MESSAGE}]},
            "contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "generationConfig": {
                "temperature": temperature,
                "maxOutputTokens": max_tokens,
                # Disable thinking — the LLM narrates pre-computed decisions,
                # it doesn't need internal reasoning. Saves 3-5s latency.
                "thinkingConfig": {"thinkingBudget": 0},
            },
        }
        token = self._get_access_token()
        req = urllib.request.Request(
            url,
            data=json.dumps(body).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {token}",
            },
            method="POST",
        )
        raw = _post_with_retry(req)
        data = json.loads(raw.decode("utf-8"))
        return data["candidates"][0]["content"]["parts"][0]["text"].strip()


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

def get_provider() -> BaseLLMProvider:
    """
    Return the active LLM provider.

    Priority: Vertex AI → Mock (no GCP project configured).
    Previous providers (OpenRouter, Gemini direct, Groq) are disabled — see commented classes above.
    """
    project = (
        os.environ.get("VERTEX_PROJECT")
        or os.environ.get("GCP_PROJECT_ID", "").strip()
        or os.environ.get("GOOGLE_CLOUD_PROJECT", "").strip()
    )
    if project:
        try:
            p = VertexAIProvider()
            logger.info(
                "Using LLM provider: %s (%s) in %s", p.provider_name, p._model, p._location
            )
            return p
        except Exception as e:
            logger.warning("Could not init VertexAIProvider: %s — falling back to mock", e)

    logger.info("Using LLM provider: mock (VERTEX_PROJECT / GCP_PROJECT_ID not set)")
    return MockProvider()

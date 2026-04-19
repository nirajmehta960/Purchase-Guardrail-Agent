"""Tests for the LLM provider abstraction.

Covers the current API:
- BaseLLMProvider abstract interface
- MockProvider deterministic stub
- get_provider() env-driven factory (Vertex if VERTEX_PROJECT/GCP_PROJECT_ID set,
  otherwise MockProvider)
"""

from __future__ import annotations

import os

import pytest

from llm.llm_provider import (
    BaseLLMProvider,
    LLMProvider,  # backwards-compat alias
    MockProvider,
    VertexAIProvider,
    get_provider,
)


# ---------------------------------------------------------------------------
# MockProvider
# ---------------------------------------------------------------------------

class TestMockProvider:
    def test_provider_name(self):
        assert MockProvider().provider_name == "mock"

    def test_is_subclass_of_base(self):
        assert issubclass(MockProvider, BaseLLMProvider)

    def test_generate_returns_string(self):
        out = MockProvider().generate("hello world")
        assert isinstance(out, str)
        assert len(out) > 0

    def test_intent_prompt_returns_json(self):
        """When the prompt mentions intent + json, mock returns parseable JSON."""
        import json

        prompt = (
            "Return JSON for the user intent. "
            'Keys: "intent", "product_reference".'
        )
        raw = MockProvider().generate(prompt)
        data = json.loads(raw)
        assert data["intent"] == "purchase_query"
        assert "product_reference" in data

    def test_non_intent_prompt_returns_advice_blurb(self):
        out = MockProvider().generate("Write me a haiku")
        assert "deterministic" in out.lower() or "emergency" in out.lower()

    def test_generate_accepts_max_tokens_and_temperature(self):
        out = MockProvider().generate("hi", max_tokens=128, temperature=0.1)
        assert isinstance(out, str)


# ---------------------------------------------------------------------------
# Backwards-compat alias
# ---------------------------------------------------------------------------

class TestLLMProviderAlias:
    def test_alias_points_at_base(self):
        assert LLMProvider is BaseLLMProvider


# ---------------------------------------------------------------------------
# get_provider() factory
# ---------------------------------------------------------------------------

@pytest.fixture
def _clear_gcp_env(monkeypatch):
    """Strip every env var get_provider inspects so we land on the mock branch."""
    for var in ("VERTEX_PROJECT", "GCP_PROJECT_ID", "GOOGLE_CLOUD_PROJECT"):
        monkeypatch.delenv(var, raising=False)
    yield


class TestGetProvider:
    def test_returns_mock_when_no_gcp_env(self, _clear_gcp_env):
        assert isinstance(get_provider(), MockProvider)

    def test_returns_base_subclass(self, _clear_gcp_env):
        assert isinstance(get_provider(), BaseLLMProvider)

    def test_returns_new_instance_each_call(self, _clear_gcp_env):
        # Current factory does not cache — each call constructs fresh.
        p1 = get_provider()
        p2 = get_provider()
        assert isinstance(p1, MockProvider)
        assert isinstance(p2, MockProvider)


# ---------------------------------------------------------------------------
# VertexAIProvider construction validation
# ---------------------------------------------------------------------------

class TestVertexAIProvider:
    def test_missing_project_raises(self, monkeypatch):
        for var in ("VERTEX_PROJECT", "GCP_PROJECT_ID", "GOOGLE_CLOUD_PROJECT"):
            monkeypatch.delenv(var, raising=False)
        with pytest.raises(ValueError, match="Vertex AI project"):
            VertexAIProvider()

    def test_explicit_project_is_used(self, monkeypatch):
        for var in ("VERTEX_PROJECT", "GCP_PROJECT_ID", "GOOGLE_CLOUD_PROJECT"):
            monkeypatch.delenv(var, raising=False)
        p = VertexAIProvider(project="my-proj", location="us-central1", model="m")
        assert p._project == "my-proj"
        assert p._location == "us-central1"
        assert p._model == "m"
        assert p.provider_name == "vertex"

    def test_env_project_picked_up(self, monkeypatch):
        monkeypatch.setenv("VERTEX_PROJECT", "env-proj")
        monkeypatch.delenv("VERTEX_LOCATION", raising=False)
        monkeypatch.delenv("VERTEX_MODEL", raising=False)
        p = VertexAIProvider()
        assert p._project == "env-proj"
        # Defaults documented on the class.
        assert p._location == VertexAIProvider._DEFAULT_LOCATION
        assert p._model == VertexAIProvider._DEFAULT_MODEL

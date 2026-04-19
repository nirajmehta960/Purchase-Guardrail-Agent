"""Tests for the intent parser (current heuristic + LLM-assisted implementation).

The current parser produces only two intents:
- "purchase_query"  — user wants to evaluate buying something
- "out_of_scope"    — greetings, unrelated topics, empty inputs

It does NOT produce a separate "general_question" intent and does NOT
attach a confidence score (those were earlier design ideas).
"""

from __future__ import annotations

import json

import pytest

from llm.intent_parser import (
    ParsedIntent,
    _heuristic_parse,
    clean_product_reference,
    extract_price_hint,
    parse_user_input,
)
from llm.llm_provider import MockProvider


# ---------------------------------------------------------------------------
# Stub provider — emulates a structured LLM that genuinely classifies intent.
# ---------------------------------------------------------------------------

class _StubLLM:
    """Returns intent JSON shaped by the user message embedded in the prompt."""

    def __init__(self, payload: dict | None = None, raise_on_call: bool = False):
        self._payload = payload
        self._raise = raise_on_call

    def generate(self, prompt: str, max_tokens: int = 256, temperature: float = 0.0) -> str:
        if self._raise:
            raise RuntimeError("LLM unavailable")
        if self._payload is not None:
            return json.dumps(self._payload)
        # Default: claim purchase but force the heuristic via null reference.
        return json.dumps(
            {"intent": "purchase_query", "product_reference": None, "user_context": None}
        )


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------

class TestExtractPriceHint:
    def test_dollar_amount(self):
        assert extract_price_hint("Should I buy this $350 thing?") == 350.0

    def test_with_comma_and_decimal(self):
        assert extract_price_hint("Worth $1,299.99?") == 1299.99

    def test_no_price(self):
        assert extract_price_hint("What about this product?") is None

    def test_empty(self):
        assert extract_price_hint("") is None


class TestCleanProductReference:
    def test_strips_leading_dollar_amount(self):
        assert clean_product_reference("$350 headphones") == "headphones"

    def test_strips_leading_bare_number(self):
        assert clean_product_reference("350 headphones") == "headphones"

    def test_collapses_whitespace(self):
        assert clean_product_reference("  Sony   WH-1000   ") == "Sony WH-1000"

    def test_too_short_returns_none(self):
        assert clean_product_reference("a") is None

    def test_none_input(self):
        assert clean_product_reference(None) is None


# ---------------------------------------------------------------------------
# Heuristic parser (no LLM)
# ---------------------------------------------------------------------------

class TestHeuristicParse:
    def test_empty_is_out_of_scope(self):
        assert _heuristic_parse("").intent == "out_of_scope"

    def test_greeting_is_out_of_scope(self):
        assert _heuristic_parse("hi").intent == "out_of_scope"
        assert _heuristic_parse("hello").intent == "out_of_scope"
        assert _heuristic_parse("thanks!").intent == "out_of_scope"

    def test_quoted_product_extracted(self):
        result = _heuristic_parse('Can I buy "Sony WH-1000XM5"?')
        assert result.intent == "purchase_query"
        assert result.product_reference == "Sony WH-1000XM5"

    def test_buy_pattern_extracts_product(self):
        result = _heuristic_parse("Can I buy the iPhone 15?")
        assert result.intent == "purchase_query"
        assert "iPhone 15" in result.product_reference

    def test_purchase_pattern_extracts_product(self):
        result = _heuristic_parse("Can I purchase the MacBook Pro?")
        assert result.intent == "purchase_query"
        assert "MacBook Pro" in result.product_reference

    def test_afford_pattern_extracts_product(self):
        result = _heuristic_parse("Could I afford the Nike Air Max?")
        assert result.intent == "purchase_query"
        assert "Nike Air Max" in result.product_reference

    def test_get_pattern_extracts_product(self):
        result = _heuristic_parse("Should I get the LG OLED TV?")
        assert result.intent == "purchase_query"
        assert "LG OLED TV" in result.product_reference

    def test_dollar_then_product_with_worth(self):
        result = _heuristic_parse("Is the $2,500 Peloton worth it?")
        assert result.intent == "purchase_query"
        assert "Peloton" in result.product_reference

    def test_product_id_token(self):
        result = _heuristic_parse("Can I buy B0BX1234?")
        assert result.intent == "purchase_query"
        assert "B0BX1234" in result.product_reference


# ---------------------------------------------------------------------------
# Full parse_user_input — LLM path + fallback wiring
# ---------------------------------------------------------------------------

class TestParseUserInput:
    def test_empty_query_short_circuits(self):
        result = parse_user_input("", _StubLLM())
        assert result.intent == "out_of_scope"
        assert result.product_reference is None

    def test_whitespace_only_short_circuits(self):
        result = parse_user_input("    ", _StubLLM())
        assert result.intent == "out_of_scope"

    def test_llm_out_of_scope_respected(self):
        stub = _StubLLM({"intent": "out_of_scope", "product_reference": None})
        result = parse_user_input("Tell me a joke", stub)
        assert result.intent == "out_of_scope"
        assert result.product_reference is None

    def test_llm_purchase_with_reference(self):
        stub = _StubLLM(
            {
                "intent": "purchase_query",
                "product_reference": "Sony WH-1000XM5",
                "price_hint": 349.99,
                "user_context": "saving for travel",
            }
        )
        result = parse_user_input("Should I buy the Sony WH-1000XM5?", stub)
        assert result.intent == "purchase_query"
        assert result.product_reference == "Sony WH-1000XM5"
        assert result.user_context == "saving for travel"
        assert result.price_hint == 349.99

    def test_llm_unknown_reference_falls_back_to_heuristic(self):
        """LLM says purchase but produces 'unknown' product → heuristic kicks in."""
        stub = _StubLLM(
            {"intent": "purchase_query", "product_reference": "unknown"}
        )
        result = parse_user_input("Can I buy the iPhone 15?", stub)
        assert result.intent == "purchase_query"
        assert "iPhone" in result.product_reference

    def test_llm_failure_falls_back_to_heuristic(self):
        result = parse_user_input(
            "Can I buy the iPhone 15?", _StubLLM(raise_on_call=True)
        )
        assert result.intent == "purchase_query"
        assert "iPhone" in result.product_reference

    def test_mock_provider_works_end_to_end(self):
        """Smoke test: real MockProvider doesn't raise and returns sensible output."""
        result = parse_user_input("Can I buy the iPhone 15?", MockProvider())
        assert isinstance(result, ParsedIntent)
        assert result.intent in ("purchase_query", "out_of_scope")

    def test_price_hint_extracted_from_query_text(self):
        """Even if LLM omits price, query-level $ is attached."""
        result = parse_user_input(
            "Should I buy the $499 Nintendo Switch?", _StubLLM()
        )
        assert result.price_hint == 499.0

    def test_invalid_intent_value_normalized(self):
        stub = _StubLLM(
            {"intent": "weird-mode", "product_reference": "iPad"}
        )
        result = parse_user_input("Can I buy this?", stub)
        # Non-canonical intent + a product_reference => coerced to purchase_query.
        assert result.intent == "purchase_query"
        assert result.product_reference == "iPad"


# ---------------------------------------------------------------------------
# Dataclass shape
# ---------------------------------------------------------------------------

class TestParsedIntentDataclass:
    def test_minimum_fields(self):
        p = ParsedIntent(intent="out_of_scope")
        assert p.intent == "out_of_scope"
        assert p.product_reference is None
        assert p.user_context is None
        assert p.price_hint is None

    def test_full_fields(self):
        p = ParsedIntent(
            intent="purchase_query",
            product_reference="iPhone 15",
            user_context="birthday gift",
            price_hint=999.0,
        )
        assert p.intent == "purchase_query"
        assert p.product_reference == "iPhone 15"
        assert p.user_context == "birthday gift"
        assert p.price_hint == 999.0

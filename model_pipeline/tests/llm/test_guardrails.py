"""Tests for the guardrails module — the current heuristic layer that
flags LLM output which contradicts the deterministic recommendation color.

Scope: this layer only enforces color/tone consistency (not hallucinated
figures, internal-leakage, or length checks — those belong to a future
NeMo-style policy engine).
"""

from __future__ import annotations

import pytest

from llm.guardrails import GuardrailResult, check_response
from llm.response_generator import RecommendationContext


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def green_context():
    return RecommendationContext(
        product_name="Sony WH-1000XM5",
        product_price=349.99,
        recommendation_color="GREEN",
        original_color="GREEN",
        was_downgraded=False,
    )


@pytest.fixture
def red_context():
    return RecommendationContext(
        product_name="MacBook Pro",
        product_price=2499.00,
        recommendation_color="RED",
        original_color="RED",
        was_downgraded=False,
    )


@pytest.fixture
def yellow_context():
    return RecommendationContext(
        product_name="Nintendo Switch",
        product_price=349.99,
        recommendation_color="YELLOW",
        original_color="YELLOW",
        was_downgraded=False,
    )


# ---------------------------------------------------------------------------
# GuardrailResult dataclass
# ---------------------------------------------------------------------------

class TestGuardrailResult:
    def test_returns_guardrail_result(self, green_context):
        result = check_response("ok", "GREEN", green_context)
        assert isinstance(result, GuardrailResult)
        assert isinstance(result.violations, list)

    def test_passed_field_reflects_violations(self, green_context):
        clean = check_response("This fits comfortably in your plan.", "GREEN", green_context)
        assert clean.passed is True
        assert clean.violations == []


# ---------------------------------------------------------------------------
# Empty / missing input
# ---------------------------------------------------------------------------

class TestEmptyResponse:
    def test_empty_string_is_violation(self, green_context):
        result = check_response("", "GREEN", green_context)
        assert not result.passed
        assert "empty_response" in result.violations

    def test_whitespace_only_is_violation(self, green_context):
        result = check_response("   \n  ", "GREEN", green_context)
        assert not result.passed
        assert "empty_response" in result.violations


# ---------------------------------------------------------------------------
# Color contradiction — GREEN must not contain "do not buy" style language
# ---------------------------------------------------------------------------

class TestGreenContradiction:
    def test_dont_buy_flagged(self, green_context):
        result = check_response(
            "Don't buy this — you can't afford it.", "GREEN", green_context
        )
        assert not result.passed
        assert any("contradicts_green" in v for v in result.violations)

    def test_should_not_buy_flagged(self, green_context):
        result = check_response(
            "You should not buy this product.", "GREEN", green_context
        )
        assert not result.passed
        assert any("contradicts_green" in v for v in result.violations)

    def test_avoid_this_purchase_flagged(self, green_context):
        result = check_response(
            "Avoid this purchase entirely.", "GREEN", green_context
        )
        assert not result.passed

    def test_encouraging_passes(self, green_context):
        result = check_response(
            "This purchase fits well within your budget.", "GREEN", green_context
        )
        assert result.passed
        assert result.violations == []


# ---------------------------------------------------------------------------
# Color contradiction — RED must not contain "go ahead and buy" language
# ---------------------------------------------------------------------------

class TestRedContradiction:
    def test_go_ahead_and_buy_flagged(self, red_context):
        result = check_response(
            "Go ahead and buy this — perfect choice!", "RED", red_context
        )
        assert not result.passed
        assert any("contradicts_red" in v for v in result.violations)

    def test_safe_to_buy_flagged(self, red_context):
        result = check_response(
            "It is safe to buy this right now.", "RED", red_context
        )
        assert not result.passed
        assert any("contradicts_red" in v for v in result.violations)

    def test_definitely_buy_flagged(self, red_context):
        result = check_response(
            "Definitely buy it — great pick!", "RED", red_context
        )
        assert not result.passed

    def test_discouraging_passes(self, red_context):
        result = check_response(
            "I'd recommend holding off on this purchase right now.",
            "RED",
            red_context,
        )
        assert result.passed
        assert result.violations == []


# ---------------------------------------------------------------------------
# YELLOW — only flagged when the text gives both extremes at once
# ---------------------------------------------------------------------------

class TestYellowSignals:
    def test_balanced_text_passes(self, yellow_context):
        result = check_response(
            "Consider waiting a pay cycle before this purchase.",
            "YELLOW",
            yellow_context,
        )
        assert result.passed

    def test_mixed_signals_flagged(self, yellow_context):
        result = check_response(
            "Don't buy this. But also go ahead — it's safe to buy.",
            "YELLOW",
            yellow_context,
        )
        assert not result.passed
        assert "mixed_signals_yellow" in result.violations

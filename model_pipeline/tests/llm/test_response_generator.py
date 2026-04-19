"""Tests for the response generator (template path + LLM-driven path).

The current generator:
- Calls llm_provider.generate(prompt, temperature=...) to get a 4-section explanation
- Falls back to `_generate_from_template(ctx)` on empty / short / failed output
- The template fallback always cites the product name and price, the
  authoritative color, and the headline financial signals
"""

from __future__ import annotations

import pytest

from llm.response_generator import (
    RecommendationContext,
    _color_key,
    _generate_from_template,
    generate_response,
)


# ---------------------------------------------------------------------------
# Stub providers (deterministic, configurable)
# ---------------------------------------------------------------------------

class _FailingLLM:
    def generate(self, *args, **kwargs):
        raise RuntimeError("LLM offline")


class _EchoLLM:
    """Returns a long, well-formed 4-section response so generate_response
    keeps the LLM output instead of falling back to the template."""

    def generate(self, prompt: str, temperature: float = 0.35, **_) -> str:
        return (
            "**💰 Your Financial Picture**\nLooks great.\n\n"
            "**🛍️ About This Product**\nSolid track record.\n\n"
            "**💬 What Buyers Are Saying**\nMostly positive reviews.\n\n"
            "**✅ Our Analysis**\nGo ahead — within budget."
        )


class _ShortLLM:
    """Returns text below the 20-char threshold → forces template fallback."""

    def generate(self, *args, **kwargs):
        return "ok"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _ctx(
    color: str = "GREEN",
    *,
    product_name: str = "Sony WH-1000XM5",
    product_price: float = 349.99,
    triggered_rules=None,
    was_downgraded: bool = False,
    original_color: str | None = None,
    affordability_score: float = 1650.0,
    savings_to_price_ratio: float = 28.5,
    emergency_fund_months: float = 8.3,
    debt_to_income_ratio: float = 0.15,
) -> RecommendationContext:
    return RecommendationContext(
        product_name=product_name,
        product_price=product_price,
        recommendation_color=color,
        original_color=original_color or color,
        was_downgraded=was_downgraded,
        triggered_rules=triggered_rules or [],
        affordability_score=affordability_score,
        savings_to_price_ratio=savings_to_price_ratio,
        emergency_fund_months=emergency_fund_months,
        debt_to_income_ratio=debt_to_income_ratio,
    )


# ---------------------------------------------------------------------------
# RecommendationContext dataclass
# ---------------------------------------------------------------------------

class TestRecommendationContext:
    def test_required_fields_only(self):
        ctx = RecommendationContext(
            product_name="Test",
            product_price=10.0,
            recommendation_color="GREEN",
            original_color="GREEN",
            was_downgraded=False,
        )
        assert ctx.triggered_rules == []
        assert ctx.confidence_scores == {}
        assert ctx.was_downgraded is False
        assert ctx.user_context is None
        assert ctx.review_snippets == []


# ---------------------------------------------------------------------------
# _color_key normalisation
# ---------------------------------------------------------------------------

class TestColorKey:
    @pytest.mark.parametrize("inp,expected", [
        ("GREEN", "GREEN"),
        ("yellow", "YELLOW"),
        ("Red", "RED"),
        ("PURPLE", "YELLOW"),
        ("", "YELLOW"),
        (None, "YELLOW"),
    ])
    def test_cases(self, inp, expected):
        assert _color_key(inp) == expected


# ---------------------------------------------------------------------------
# Template fallback (deterministic, no LLM)
# ---------------------------------------------------------------------------

class TestTemplateFallback:
    def test_returns_non_empty_string(self):
        out = _generate_from_template(_ctx("GREEN"))
        assert isinstance(out, str)
        assert len(out) > 0

    def test_includes_product_name(self):
        out = _generate_from_template(_ctx("GREEN"))
        assert "Sony WH-1000XM5" in out

    def test_includes_price(self):
        out = _generate_from_template(_ctx("GREEN"))
        assert "$349.99" in out

    def test_green_tone_is_encouraging(self):
        out = _generate_from_template(_ctx("GREEN")).lower()
        assert any(w in out for w in ("proceed", "go ahead", "comfortably"))

    def test_yellow_tone_is_cautious(self):
        out = _generate_from_template(_ctx("YELLOW")).lower()
        assert any(w in out for w in ("consider", "wait", "compare"))

    def test_red_tone_discourages(self):
        out = _generate_from_template(_ctx("RED")).lower()
        assert any(w in out for w in ("prioritize", "essentials", "before"))

    def test_unknown_color_defaults_to_yellow(self):
        out = _generate_from_template(_ctx("PURPLE"))
        # Falls into the YELLOW branch — check for one of its closing lines.
        assert "consider" in out.lower() or "compare" in out.lower()

    def test_includes_triggered_rules_when_present(self):
        out = _generate_from_template(_ctx("YELLOW", triggered_rules=["YELLOW_2", "YELLOW_3"]))
        assert "Rules considered" in out
        assert "YELLOW_2" in out

    def test_downgrade_note_appears(self):
        out = _generate_from_template(_ctx("YELLOW", was_downgraded=True, original_color="GREEN"))
        assert "downgrade" in out.lower()

    def test_affordability_appears(self):
        out = _generate_from_template(_ctx("GREEN"))
        assert "Affordability score" in out
        assert "1650" in out or "1,650" in out  # value rendered to 2dp


# ---------------------------------------------------------------------------
# generate_response — LLM-driven path with stubs
# ---------------------------------------------------------------------------

class TestGenerateResponse:
    def test_uses_llm_when_output_is_long_enough(self):
        out = generate_response(_ctx("GREEN"), _EchoLLM())
        assert "Your Financial Picture" in out
        assert out.startswith("**💰")

    def test_short_llm_output_falls_back_to_template(self):
        out = generate_response(_ctx("GREEN"), _ShortLLM())
        # Template includes product name; the ShortLLM 'ok' would not.
        assert "Sony WH-1000XM5" in out

    def test_llm_failure_falls_back_to_template(self):
        out = generate_response(_ctx("RED"), _FailingLLM())
        assert "Sony WH-1000XM5" in out
        assert "$349.99" in out

    def test_falls_back_template_preserves_color(self):
        red = generate_response(_ctx("RED"), _FailingLLM()).lower()
        green = generate_response(_ctx("GREEN"), _FailingLLM()).lower()
        # RED template ends with 'Prioritize essentials...'; GREEN does not.
        assert "prioritize" in red
        assert "prioritize" not in green

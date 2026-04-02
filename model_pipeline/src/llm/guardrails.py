"""
Guardrails — Output Safety Verification.

Six code-level safety checks applied to every LLM response before it
reaches the user. Structured with a NeMo-ready interface so that
NVIDIA NeMo Guardrails can be plugged in later.

Guardrail Checks:
    G1 — Color contradiction: response contradicts the recommendation color
    G2 — Hallucinated figures: response contains dollar amounts not in input
    G3 — Out-of-scope advice: response gives investment/credit/stock tips
    G4 — Internal leakage: response mentions system internals
    G5 — Tone mismatch: GREEN response uses alarm words, or RED uses encouragement
    G6 — Length check: response exceeds maximum word count

Usage:
    from llm.guardrails import check_response
    result = check_response(response, "GREEN", context)
    if not result.passed:
        response = result.sanitized_response or fallback_response
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

from llm.config import LLMConfig

logger = logging.getLogger(__name__)


@dataclass
class GuardrailResult:
    """Result of running all guardrail checks on an LLM response."""
    passed: bool
    violations: list[str] = field(default_factory=list)
    sanitized_response: str | None = None


# ---------------------------------------------------------------------------
# Internal system terms that must never appear in responses
# ---------------------------------------------------------------------------

_INTERNAL_TERMS = [
    "deterministic engine", "deterministic", "decision engine",
    "financial engine", "downgrade engine",
    "rule red", "rule yellow", "rule green",
    # Old placeholder rule names
    "red_1", "red_2", "red_3", "red_4",
    "yellow_1", "yellow_2", "yellow_3", "yellow_4", "yellow_5",
    # Actual engine rule keys (must never leak to user)
    "red:cant_afford", "red:maxed_budget", "red:underwater", "red:paycheck_to_paycheck",
    "yellow:income_pressure", "yellow:savings_strain", "yellow:debt_stress",
    "yellow:low_resilience", "yellow:weak_profile",
    "pr1:", "pr2:", "pr3:", "rr1:", "rr2:", "rr3:",
    "lightgbm", "xgboost", "lgbm", "ml model",
    "confidence score", "predict_proba", "pgvector",
    "embedding", "sentence-transformer", "cosine similarity",
    "feature pipeline", "feature vector", "pydantic", "fastapi",
    "nemo guardrail",
]

# Color-specific words that should NOT appear in contradictory contexts
_ENCOURAGE_WORDS = [
    "go ahead", "great choice", "perfect", "excellent",
    "definitely buy", "absolutely", "no concerns",
    "fits perfectly", "no worries", "strongly recommend buying",
]

_ALARM_WORDS = [
    "don't buy", "do not buy", "avoid", "stay away",
    "can't afford", "cannot afford", "strongly advise against",
    "highly recommend against", "dangerous", "reckless",
    "irresponsible",
]

# Out-of-scope advice indicators
_OUT_OF_SCOPE_PHRASES = [
    "invest in", "investment", "stock market", "stocks",
    "crypto", "bitcoin", "mutual fund", "index fund",
    "credit card", "apply for a",
    "open an account", "refinance", "mortgage",
    "financial advisor", "consult a professional",
    "401k", "ira", "roth",
]


# ---------------------------------------------------------------------------
# Individual guardrail checks
# ---------------------------------------------------------------------------

def _check_color_contradiction(
    response: str, color: str
) -> str | None:
    """G1: Check if the response contradicts the recommendation color."""
    response_lower = response.lower()
    color = color.upper()

    if color == "RED":
        for phrase in _ENCOURAGE_WORDS:
            if phrase in response_lower:
                return f"G1: Response encourages purchase ('{phrase}') but color is RED"

    elif color == "GREEN":
        for phrase in _ALARM_WORDS:
            if phrase in response_lower:
                return f"G1: Response discourages purchase ('{phrase}') but color is GREEN"

    return None


def _check_hallucinated_figures(
    response: str, context_price: float
) -> str | None:
    """G2: Check if the response contains dollar amounts not in the input context."""
    # Extract all dollar amounts from the response
    dollar_pattern = re.compile(r"\$[\d,]+\.?\d*")
    amounts_in_response = dollar_pattern.findall(response)

    if not amounts_in_response:
        return None

    # The only dollar amount allowed is the product price
    known_amount = f"${context_price:.2f}"
    known_amount_int = f"${int(context_price)}"

    for amount in amounts_in_response:
        cleaned = amount.replace(",", "")
        if cleaned != known_amount and cleaned != known_amount_int:
            return f"G2: Response contains ungrounded dollar amount: {amount}"

    return None


def _check_out_of_scope(response: str) -> str | None:
    """G3: Check if the response contains out-of-scope financial advice."""
    response_lower = response.lower()
    for phrase in _OUT_OF_SCOPE_PHRASES:
        if phrase in response_lower:
            return f"G3: Response contains out-of-scope advice ('{phrase}')"
    return None


def _check_internal_leakage(response: str) -> str | None:
    """G4: Check if the response exposes internal system details."""
    response_lower = response.lower()
    for term in _INTERNAL_TERMS:
        if term in response_lower:
            return f"G4: Response leaks internal term ('{term}')"
    return None


def _check_tone_mismatch(response: str, color: str) -> str | None:
    """G5: Check if the response tone mismatches the recommendation color."""
    response_lower = response.lower()
    color = color.upper()

    if color == "GREEN":
        # GREEN responses should not sound alarming
        alarm_count = sum(1 for w in _ALARM_WORDS if w in response_lower)
        if alarm_count >= 2:
            return "G5: GREEN response has too many alarm words"

    elif color == "RED":
        # RED responses should not sound encouraging
        encourage_count = sum(1 for w in _ENCOURAGE_WORDS if w in response_lower)
        if encourage_count >= 2:
            return "G5: RED response has too many encouragement words"

    return None


def _check_length(response: str) -> str | None:
    """G6: Check if the response exceeds the maximum word count."""
    word_count = len(response.split())
    if word_count > LLMConfig.MAX_RESPONSE_WORDS:
        return f"G6: Response is {word_count} words (max: {LLMConfig.MAX_RESPONSE_WORDS})"
    return None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def check_response(
    response: str,
    recommendation_color: str,
    context: object | None = None,
) -> GuardrailResult:
    """
    Run all guardrail checks on an LLM response.

    Args:
        response: The LLM-generated response text.
        recommendation_color: The authoritative color (GREEN/YELLOW/RED).
        context: A RecommendationContext object (if available) for grounding checks.

    Returns:
        GuardrailResult with passed=True if all checks pass.
    """
    violations: list[str] = []

    # Get product price from context if available
    context_price = 0.0
    if context and hasattr(context, "product_price"):
        context_price = context.product_price

    # Run all checks
    checks = [
        _check_color_contradiction(response, recommendation_color),
        _check_hallucinated_figures(response, context_price),
        _check_out_of_scope(response),
        _check_internal_leakage(response),
        _check_tone_mismatch(response, recommendation_color),
        _check_length(response),
    ]

    for result in checks:
        if result is not None:
            violations.append(result)

    if violations:
        logger.warning(
            "Guardrail violations (%d): %s",
            len(violations),
            "; ".join(violations),
        )

    return GuardrailResult(
        passed=len(violations) == 0,
        violations=violations,
        sanitized_response=None,
    )

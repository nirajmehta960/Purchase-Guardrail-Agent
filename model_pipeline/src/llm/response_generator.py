"""
Response Generator — LLM Role 2: Conversational Recommendation Output.

Takes the deterministic engine result, ML confidence scores, and triggered rules,
then generates a user-facing natural language recommendation.

Uses template-based responses for MockProvider (deterministic, safe)
and real LLM generation for other providers (with guardrail verification).

Usage:
    from llm.response_generator import generate_response, RecommendationContext

    context = RecommendationContext(
        product_name="Sony WH-1000XM5",
        product_price=349.99,
        recommendation_color="GREEN",
        ...
    )
    response = generate_response(context, provider)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from llm.config import LLMConfig
from llm.llm_provider import LLMProvider, MockProvider
from llm.prompts.response_templates import (
    GREEN_TEMPLATES,
    YELLOW_TEMPLATES,
    RED_TEMPLATES,
    get_concern_summary,
)
from llm.prompts.system_prompt import SYSTEM_PROMPT

logger = logging.getLogger(__name__)


@dataclass
class RecommendationContext:
    """All the context needed to generate a recommendation response."""

    # Product info
    product_name: str
    product_price: float

    # Deterministic engine output
    recommendation_color: str                  # GREEN / YELLOW / RED
    original_color: str = ""                   # Pre-downgrade color (if different)
    was_downgraded: bool = False
    triggered_rules: list[str] = field(default_factory=list)

    # ML model output
    confidence_scores: dict[str, float] = field(default_factory=dict)

    # User context from intent parsing
    user_context: str | None = None

    # Financial highlights (for grounding the LLM response)
    affordability_score: float = 0.0
    savings_to_price_ratio: float = 0.0
    emergency_fund_months: float = 0.0
    debt_to_income_ratio: float = 0.0


def generate_response(
    context: RecommendationContext,
    provider: LLMProvider,
) -> str:
    """
    Generate a user-facing natural language recommendation.

    Args:
        context: Full recommendation context from the inference pipeline.
        provider: The LLM provider to use for generation.

    Returns:
        A natural language recommendation string.
    """
    # Use templates for MockProvider — guaranteed safe and deterministic
    if isinstance(provider, MockProvider):
        return _generate_from_template(context)

    # Use real LLM for other providers
    return _generate_with_llm(context, provider)


# ---------------------------------------------------------------------------
# Template-based generation (MockProvider)
# ---------------------------------------------------------------------------

def _generate_from_template(context: RecommendationContext) -> str:
    """Generate a response using pre-written templates."""
    color = context.recommendation_color.upper()

    # Build the concern summary from triggered rules
    concern_summary = get_concern_summary(
        context.triggered_rules, context.was_downgraded
    )

    # Select template based on color
    if color == "GREEN":
        templates = GREEN_TEMPLATES
    elif color == "YELLOW":
        templates = YELLOW_TEMPLATES
    elif color == "RED":
        templates = RED_TEMPLATES
    else:
        logger.warning("Unknown color '%s', defaulting to YELLOW", color)
        templates = YELLOW_TEMPLATES

    # Use the first template (deterministic for tests)
    template = templates[0]

    try:
        response = template.format(
            product_name=context.product_name,
            product_price=context.product_price,
            savings_to_price_ratio=context.savings_to_price_ratio,
            emergency_fund_months=context.emergency_fund_months,
            debt_to_income_ratio=context.debt_to_income_ratio,
            affordability_score=context.affordability_score,
            concern_summary=concern_summary,
        )
    except KeyError as e:
        logger.warning("Template formatting error: %s — using fallback", e)
        response = _fallback_response(context, concern_summary)

    logger.info("Generated template response for %s recommendation", color)
    return response


def _fallback_response(context: RecommendationContext, concern_summary: str) -> str:
    """Minimal fallback response when template formatting fails."""
    color = context.recommendation_color.upper()
    if color == "GREEN":
        return (
            f"Purchasing {context.product_name} at ${context.product_price:.2f} "
            f"looks comfortable for your financial profile."
        )
    elif color == "RED":
        return (
            f"I'd recommend holding off on {context.product_name} at "
            f"${context.product_price:.2f} right now. {concern_summary}"
        )
    else:
        return (
            f"Consider carefully before purchasing {context.product_name} at "
            f"${context.product_price:.2f}. {concern_summary}"
        )


# ---------------------------------------------------------------------------
# LLM-based generation (real providers)
# ---------------------------------------------------------------------------

def _generate_with_llm(
    context: RecommendationContext, provider: LLMProvider
) -> str:
    """Generate a response using a real LLM provider."""
    color = context.recommendation_color.upper()
    confidence = context.confidence_scores.get(color, 0.0)

    # Build the grounding context for the LLM
    downgrade_note = ""
    if context.was_downgraded:
        downgrade_note = (
            f"\nNote: The recommendation was adjusted from {context.original_color} to "
            f"{color} due to product quality and review concerns."
        )

    rules_text = ", ".join(context.triggered_rules) if context.triggered_rules else "None"

    user_message = (
        f"Generate a purchase recommendation for the user.\n\n"
        f"FINANCIAL CONTEXT:\n"
        f"- Recommendation: {color} (confidence: {confidence:.0%})\n"
        f"- Product: {context.product_name} at ${context.product_price:.2f}\n"
        f"- Affordability score: ${context.affordability_score:.2f} "
        f"(positive = can afford from discretionary income)\n"
        f"- Savings can cover this purchase "
        f"{context.savings_to_price_ratio:.1f}x over\n"
        f"- Emergency fund: {context.emergency_fund_months:.1f} months remaining\n"
        f"- Debt-to-income ratio: {context.debt_to_income_ratio:.0%}\n"
        f"- Concerns flagged: {rules_text}\n"
        f"{downgrade_note}\n"
    )

    if context.user_context:
        user_message += f"\nUser also mentioned: {context.user_context}\n"

    try:
        response = provider.generate(
            system_prompt=SYSTEM_PROMPT,
            user_message=user_message,
            temperature=LLMConfig.TEMPERATURE,
            max_tokens=LLMConfig.MAX_TOKENS,
        )
        logger.info("Generated LLM response for %s recommendation (%s)", color, provider.provider_name)
        return response

    except Exception as e:
        logger.error("LLM response generation failed: %s — falling back to template", e)
        return _generate_from_template(context)

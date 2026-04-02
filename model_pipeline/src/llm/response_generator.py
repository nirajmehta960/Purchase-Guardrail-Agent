"""
Generate natural-language explanations aligned with deterministic engine output.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from llm.prompts.response_templates import COLOR_INTROS

logger = logging.getLogger(__name__)


@dataclass
class RecommendationContext:
    product_name: str
    product_price: float
    recommendation_color: str  # GREEN / YELLOW / RED
    original_color: str
    was_downgraded: bool
    triggered_rules: List[str] = field(default_factory=list)
    confidence_scores: Dict[str, float] = field(default_factory=dict)
    ml_confidence: Optional[float] = None  # None => classifier not used
    # When ml_confidence is None: why (clearer than "not loaded" for every case)
    ml_unavailable_reason: Optional[str] = None  # "no_model" | "no_pipeline" | "scoring_error"
    hypothetical_purchase: bool = False
    user_context: Optional[str] = None
    affordability_score: float = 0.0
    affordability_score_unreliable: bool = False
    savings_to_price_ratio: float = 0.0
    emergency_fund_months: float = 0.0
    debt_to_income_ratio: float = 0.0


def _color_key(color: str) -> str:
    c = (color or "YELLOW").upper()
    if c not in ("GREEN", "YELLOW", "RED"):
        return "YELLOW"
    return c


def _generate_from_template(context: RecommendationContext) -> str:
    """Non-LLM fallback — still aligned with signal."""
    ck = _color_key(context.recommendation_color)
    intro_tpl = COLOR_INTROS.get(ck, COLOR_INTROS["YELLOW"])
    intro = intro_tpl.format(
        product_name=context.product_name,
        product_price=context.product_price,
    )

    lines = [intro, ""]
    if context.hypothetical_purchase:
        lines.append(
            "- **Note:** No catalog match — evaluating affordability at **your stated price** "
            f"for **{context.product_name}**."
        )
    if context.ml_confidence is not None:
        # Two decimals: sub-1% scores (common on uncertain rows) otherwise show as 0.0%.
        lines.append(
            f"- **Signal:** {context.recommendation_color} "
            f"(ML confidence: {context.ml_confidence:.2%})."
        )
        if context.hypothetical_purchase and context.ml_confidence < 0.15:
            lines.append(
                "- **Note:** With **no catalog product row**, review/product features are neutralized for ML; "
                "the classifier probability can be **low or flat** even when the rules engine says GREEN."
            )
    else:
        reason = context.ml_unavailable_reason or ""
        if reason == "no_model":
            tail = (
                "no **trained model artifact** found (set `MODEL_ARTIFACT_DIR` or run the training pipeline). "
                "Recommendation is from the **rules engine**."
            )
        elif reason == "no_pipeline":
            tail = (
                "**Feature pipeline** (`FEATURE_PIPELINE_PATH` / `feature_pipeline.pkl`) missing — ML score skipped. "
                "Recommendation is from the **rules engine**."
            )
        elif reason == "scoring_error":
            tail = "ML scoring failed (see API logs); recommendation is from the **rules engine**."
        else:
            tail = (
                "ML score unavailable — recommendation is from the **deterministic rules engine**."
            )
        lines.append(f"- **Signal:** {context.recommendation_color} — {tail}")
    if context.affordability_score_unreliable:
        lines.append(
            "- **Affordability score:** calculation error (inputs out of safe range); "
            f"**Savings-to-price ratio:** {context.savings_to_price_ratio:.2f}."
        )
    else:
        lines.append(
            f"- **Affordability score:** {context.affordability_score:.2f}; "
            f"**Savings-to-price ratio:** {context.savings_to_price_ratio:.2f}."
        )
    lines.append(
        f"- **Emergency fund:** ~{context.emergency_fund_months:.1f} months; "
        f"**Debt-to-income:** {context.debt_to_income_ratio:.2%}."
    )
    if context.triggered_rules:
        lines.append("- **Rules considered:** " + "; ".join(context.triggered_rules[:8]))
    if context.was_downgraded:
        lines.append(
            "- **Note:** Product and review signals caused a **one-step downgrade** from the pure financial assessment."
        )
    if context.user_context:
        lines.append(f"- **Your note:** {context.user_context}")

    lines.append("")
    if ck == "GREEN":
        lines.append(
            "If this remains within your monthly plan, you can proceed — still track discretionary spend."
        )
    elif ck == "YELLOW":
        lines.append(
            "Consider waiting a pay cycle, comparing alternatives, or reducing other discretionary spend first."
        )
    else:
        lines.append(
            "Prioritize essentials, debt minimums, and emergency savings before this purchase."
        )

    return "\n".join(lines)


def generate_response(context: RecommendationContext, llm_provider: Any) -> str:
    """
    Ask the LLM to produce an explanation that respects the authoritative color.

    On failure, uses _generate_from_template.
    """
    ck = _color_key(context.recommendation_color)
    rules_txt = "; ".join(context.triggered_rules[:12]) if context.triggered_rules else "(none listed)"

    prompt = f"""You are SavVio, a fiduciary-style financial assistant.

The deterministic engine has ALREADY decided the recommendation color. You MUST NOT contradict it.

Hard requirements:
- The user's recommendation is **{context.recommendation_color}** (GREEN = okay to buy, YELLOW = caution, RED = avoid).
- Do NOT tell the user to buy if the color is RED, or to avoid if the color is GREEN.
- Keep the tone supportive and specific. Mention the product name and price.
- Short paragraphs, optional bullet points. Use **bold** for key numbers.

Context:
- Product: {context.product_name} at ${context.product_price:,.2f}
- Stated price only (no catalog SKU matched): {context.hypothetical_purchase}
- ML confidence available: {context.ml_confidence is not None}
- Affordability score: {"calculation error (do not quote a numeric AFS)" if context.affordability_score_unreliable else f"{context.affordability_score:.3f}"}
- Savings to price ratio: {context.savings_to_price_ratio:.3f}
- Emergency fund (months): {context.emergency_fund_months:.2f}
- Debt-to-income ratio: {context.debt_to_income_ratio:.3f}
- Triggered rules: {rules_txt}
- Downgraded from financial-only layer: {context.was_downgraded}
- User context: {context.user_context or "none"}

Write the answer (no JSON)."""

    try:
        raw = llm_provider.generate(prompt, max_tokens=600, temperature=0.35)
        if raw and len(raw.strip()) > 20:
            return raw.strip()
    except Exception as e:
        logger.warning("LLM response generation failed: %s", e)

    return _generate_from_template(context)

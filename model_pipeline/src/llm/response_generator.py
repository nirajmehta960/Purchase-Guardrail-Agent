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

    # --- Financial profile (raw) ---
    monthly_income: float = 0.0
    monthly_expenses: float = 0.0
    savings_balance: float = 0.0
    liquid_savings: float = 0.0               # SCF-adjusted liquid portion of savings
    discretionary_income: float = 0.0         # income - expenses - emi
    employment_status: str = ""
    region: str = ""
    credit_score: Optional[int] = None        # Raw FICO score (300–850)
    has_loan: bool = False
    loan_amount: float = 0.0
    monthly_emi: float = 0.0
    loan_interest_rate: float = 0.0
    loan_term_months: int = 0

    # --- Financial profile (computed) ---
    affordability_score: float = 0.0
    affordability_score_unreliable: bool = False
    savings_to_price_ratio: float = 0.0
    emergency_fund_months: float = 0.0
    debt_to_income_ratio: float = 0.0
    monthly_expense_burden_ratio: float = 0.0  # expenses as % of income
    price_to_income_ratio: float = 0.0          # price as % of monthly income
    saving_to_income_ratio: float = 0.0        # liquid savings / income
    residual_utility_score: float = 0.0        # (savings - price) / obligations
    net_worth_indicator: float = 0.0           # (savings - loan) / income
    credit_risk_indicator: float = 0.0         # normalised credit score (0–1)

    # --- Product signals (raw) ---
    category: str = ""
    average_rating: float = 0.0
    rating_count: int = 0
    rating_variance: float = 0.0

    # --- Product signals (computed) ---
    cold_start_flag: bool = False
    category_rating_deviation: float = 0.0
    value_density: float = 0.0                # rating / log(price) — value for money
    review_confidence: float = 0.0            # log(rating_count) normalised
    rating_polarization: float = 0.0          # love/hate extremity
    quality_risk_score: float = 0.0           # combined product quality risk (0–1)
    price_category_rank: float = 0.0          # price position in category (0=cheapest, 1=most expensive)

    # --- Review signals (computed) ---
    verified_purchase_ratio: float = 0.0
    sentiment_spread: float = 0.0             # (positive - negative) / total
    helpful_concentration: float = 0.0        # top reviewer share of helpful votes
    review_depth_score: float = 0.0           # avg review length normalised (0–1)
    reviewer_diversity: float = 0.0           # unique reviewers / total reviews
    extreme_rating_ratio: float = 0.0         # (1★ + 5★) / total reviews

    # Up to 5 formatted review snippets — e.g. '[4★] Great sound: battery lasts forever...'
    # Empty list when product has no reviews or is a hypothetical evaluation.
    review_snippets: List[str] = field(default_factory=list)


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
    if context.review_snippets:
        lines.append("")
        lines.append("**What customers say:**")
        for snippet in context.review_snippets:
            lines.append(f"  - {snippet}")
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

    if context.review_snippets:
        reviews_block = "Customer voice (most helpful reviews):\n" + "\n".join(
            f"  {s}" for s in context.review_snippets
        )
        reviews_instruction = (
            "- Weave 1–2 specific details from the customer reviews into your explanation "
            "so the user understands what real buyers experienced with this product. "
            "If reviews are positive, note what customers praise. "
            "If reviews are mixed or negative, flag the concerns honestly."
        )
    else:
        reviews_block = "Customer voice: no reviews available for this product."
        reviews_instruction = "- Do not fabricate customer opinions; note that no reviews are available."

    # ---------------------------------------------------------------------------
    # Build human-readable context blocks from all 40 features
    # ---------------------------------------------------------------------------
    downgrade_note = (
        f"\nDOWNGRADE: The ML model initially scored this as {context.original_color}, "
        f"but product/review quality signals downgraded it to {context.recommendation_color}. "
        f"Briefly acknowledge this — e.g. 'While your finances could support this purchase, "
        f"product and review signals raised enough concern to warrant caution.'"
        if context.was_downgraded else ""
    )

    # --- Financial: raw profile ---
    dti_pct  = context.debt_to_income_ratio * 100
    meb_pct  = context.monthly_expense_burden_ratio * 100
    pir_pct  = context.price_to_income_ratio * 100
    stir_pct = context.saving_to_income_ratio * 100

    credit_line = (
        f"{context.credit_score} (below 580 = poor, 580–669 = fair, 670+ = good)"
        if context.credit_score is not None else "not available"
    )
    credit_risk_label = (
        "excellent" if context.credit_risk_indicator >= 0.75
        else "good" if context.credit_risk_indicator >= 0.55
        else "fair" if context.credit_risk_indicator >= 0.35
        else "poor"
    ) if context.credit_risk_indicator > 0 else "unknown"

    loan_lines = ""
    if context.has_loan and context.loan_amount > 0:
        loan_lines = (
            f"\n- Active loan: ${context.loan_amount:,.0f} outstanding"
            f" | EMI: ${context.monthly_emi:,.0f}/mo"
            + (f" | Rate: {context.loan_interest_rate:.1f}%" if context.loan_interest_rate > 0 else "")
            + (f" | Term: {context.loan_term_months} months remaining" if context.loan_term_months > 0 else "")
        )

    afs_line = (
        "Affordability score: [calculation error — do not quote]"
        if context.affordability_score_unreliable
        else f"Affordability score (discretionary income minus price): {context.affordability_score:+,.2f}"
    )

    # --- Product quality signals (translated to English) ---
    if not context.hypothetical_purchase and context.rating_count > 0:
        cat_dev_str = ""
        if context.category and abs(context.category_rating_deviation) >= 0.1:
            direction = "above" if context.category_rating_deviation > 0 else "below"
            cat_dev_str = f" ({abs(context.category_rating_deviation):.2f}★ {direction} {context.category} category average)"

        cold_str = " — WARNING: fewer than 10 reviews, rating unreliable" if context.cold_start_flag else ""
        variance_label = (
            "highly inconsistent (polarised)" if context.rating_variance > 2.0
            else "somewhat inconsistent" if context.rating_variance > 1.0
            else "consistent"
        )
        value_label = (
            "strong value for money" if context.value_density > 1.5
            else "moderate value for money" if context.value_density > 0.8
            else "low value for money relative to price"
        )
        confidence_pct = context.review_confidence * 100
        polarization_label = (
            "highly polarised (love-it-or-hate-it)" if context.rating_polarization > 0.4
            else "moderately polarised" if context.rating_polarization > 0.2
            else "consensus rating"
        )
        quality_risk_label = (
            "HIGH quality risk" if context.quality_risk_score > 0.6
            else "MODERATE quality risk" if context.quality_risk_score > 0.3
            else "LOW quality risk"
        )
        price_rank_pct = context.price_category_rank * 100

        product_block = f"""PRODUCT QUALITY SIGNALS (all ML-derived — use to explain the recommendation):
- Rating: {context.average_rating:.2f}★ from {context.rating_count:,} reviews{cold_str}{cat_dev_str}
- Rating variance: {context.rating_variance:.2f} — {variance_label}
- Rating polarisation: {polarization_label} (score: {context.rating_polarization:.2f})
- Value for money: {value_label} (density score: {context.value_density:.2f})
- Review confidence: {confidence_pct:.0f}% (based on review volume relative to category)
- Quality risk: {quality_risk_label} (score: {context.quality_risk_score:.2f}, 0=low 1=high)
- Price tier: {'top' if price_rank_pct > 66 else 'mid' if price_rank_pct > 33 else 'budget'} {price_rank_pct:.0f}th percentile in {context.category or 'its category'}"""
    else:
        product_block = "PRODUCT QUALITY SIGNALS: no catalog data (hypothetical evaluation — product not in database)." if context.hypothetical_purchase else "PRODUCT QUALITY SIGNALS: no product data available."

    # --- Review aggregate signals ---
    if context.rating_count > 0:
        sentiment_label = (
            "strongly positive" if context.sentiment_spread > 0.4
            else "mildly positive" if context.sentiment_spread > 0.1
            else "mixed" if context.sentiment_spread > -0.1
            else "negative-leaning"
        )
        depth_label = (
            "detailed and substantive" if context.review_depth_score > 0.6
            else "moderate depth" if context.review_depth_score > 0.3
            else "brief/shallow"
        )
        diversity_label = (
            "high reviewer diversity (authentic)" if context.reviewer_diversity > 0.8
            else "moderate diversity" if context.reviewer_diversity > 0.5
            else "low diversity — possible review manipulation risk"
        )
        extreme_label = (
            "very polarised (high 1★+5★ concentration)" if context.extreme_rating_ratio > 0.7
            else "moderately polarised" if context.extreme_rating_ratio > 0.4
            else "balanced distribution"
        )
        helpful_label = (
            "helpful votes concentrated in few reviewers" if context.helpful_concentration > 0.6
            else "broadly distributed helpful votes"
        )

        review_signals_block = f"""REVIEW QUALITY SIGNALS:
- Overall sentiment: {sentiment_label} (spread score: {context.sentiment_spread:+.2f})
- Verified buyers: {context.verified_purchase_ratio:.0%} of reviews from verified purchases
- Reviewer diversity: {diversity_label} ({context.reviewer_diversity:.0%} unique reviewers)
- Extreme ratings (1★+5★): {context.extreme_rating_ratio:.0%} — {extreme_label}
- Review depth: {depth_label} (score: {context.review_depth_score:.2f})
- Helpful vote distribution: {helpful_label} (concentration: {context.helpful_concentration:.2f})"""
    else:
        review_signals_block = "REVIEW QUALITY SIGNALS: no review data available."

    prompt = f"""You are SavVio, a fiduciary financial advisor. Explain this purchase recommendation to the user in clear, honest, personalised language using the exact data provided below. Never invent or round numbers.

DECISION: {context.recommendation_color} — do NOT contradict or soften this verdict.{downgrade_note}
{"IMPORTANT: Do NOT suggest buying, proceeding, or frame this purchase positively." if context.recommendation_color == "RED" else ""}
{"IMPORTANT: Do NOT warn against the purchase, call it risky, or suggest hesitation." if context.recommendation_color == "GREEN" else ""}

━━━ PURCHASE ━━━
Product : {context.product_name}
Price   : ${context.product_price:,.2f}{"  (hypothetical — user-stated price, no catalog match)" if context.hypothetical_purchase else ""}

━━━ USER FINANCIAL PROFILE ━━━
Employment  : {context.employment_status or "not specified"} | Region: {context.region or "not specified"}
Monthly income    : ${context.monthly_income:,.2f}
Monthly expenses  : ${context.monthly_expenses:,.2f}
Monthly EMI       : ${context.monthly_emi:,.2f}
Discretionary income (income − expenses − EMI): ${context.discretionary_income:,.2f}
Savings balance   : ${context.savings_balance:,.2f}  |  Liquid savings: ${context.liquid_savings:,.2f}
Credit score      : {credit_line}  |  Credit risk level: {credit_risk_label}
{loan_lines}

Computed financial signals (ML-derived — USE THESE to inform your explanation, but NEVER quote these metric names in your response):
- {afs_line}
- Price-to-income ratio     : {pir_pct:.1f}% of monthly income
- Expense burden ratio      : {meb_pct:.1f}% of income already committed to fixed costs
- Savings-to-price ratio    : {context.savings_to_price_ratio:.2f}x (savings cover the price this many times over)
- Saving-to-income ratio    : {stir_pct:.1f}% (liquid savings relative to monthly income)
- Emergency fund            : {context.emergency_fund_months:.1f} months (target: 3–6)
- Debt-to-income ratio      : {dti_pct:.1f}%
- Residual utility score    : {context.residual_utility_score:+.3f} (headroom after purchase relative to obligations)
- Net worth indicator       : {context.net_worth_indicator:+.3f} (savings minus debt relative to income)
- Rules triggered           : {rules_txt}
{f"- User added context: {context.user_context}" if context.user_context else ""}

━━━ {product_block}

━━━ {review_signals_block}

━━━ CUSTOMER VOICE ━━━
{reviews_block}

━━━ YOUR TASK ━━━
Write the response in exactly 4 named sections. Each section has a bold markdown heading followed by 2–4 sentences of flowing prose. Bold (**like this**) every specific dollar amount, percentage, or number you cite.

CRITICAL OUTPUT RULES — violating any of these will cause the response to be rejected:
1. Use ONLY these four headings (exactly as written, including the emoji):
   **💰 Your Financial Picture**
   **🛍️ About This Product**
   **💬 What Buyers Are Saying**
   **✅ Our Analysis**   (change emoji to ⚠️ for YELLOW, 🚫 for RED)
2. PLAIN ENGLISH ONLY — never use any of these terms in your response: discretionary income, affordability score, debt-to-income, savings-to-price ratio, residual utility, net worth indicator, credit risk indicator, price-to-income ratio, rating polarization, value density, review confidence, cold start, sentiment spread, helpful concentration, review depth, reviewer diversity, extreme rating ratio, expense burden, saving-to-income ratio.
   Translate everything into words anyone would use in a normal conversation. Examples:
   ❌ "Your discretionary income is $2,349"  →  ✅ "After all your bills, you have **$2,349** left over each month"
   ❌ "Your debt-to-income is 28%"  →  ✅ "**28%** of your income already goes to debt payments"
   ❌ "Affordability score: +2,000"  →  ✅ "This purchase leaves you with a comfortable financial cushion"
   ❌ "Rating polarization is high"  →  ✅ "Opinions are split — buyers either love it or don't"
3. Each section must be prose — no nested bullets or sub-lists inside a section.
4. Keep the total response under 180 words.

Section guidance:
**💰 Your Financial Picture** — 2 sentences MAX. Pick the 1–2 most telling facts about whether the user can afford this (e.g. monthly leftover money, savings buffer). Skip anything the user doesn't need to act on. No jargon, no ratios by name — just the human implication.
**🛍️ About This Product** — 2 sentences. Plain-English quality summary. Mention the star rating and number of reviews; translate everything else into everyday language (e.g. "reviews are mixed", "considered a budget-friendly option in its category").
**💬 What Buyers Are Saying** — 1–2 sentences. Quote or closely paraphrase 1–2 real reviewer lines. If no reviews, say so in one sentence.
**✅ Our Analysis** — 2 sentences. Start with "Based on your finances and this product's track record, we found this purchase [safe / needs caution / not advisable]." Follow with one concrete, specific action the user can take right now.

Write warmly but with fiduciary honesty. A teenager with no finance knowledge should understand every sentence."""

    try:
        raw = llm_provider.generate(prompt, temperature=0.35)
        if raw and len(raw.strip()) > 20:
            return raw.strip()
    except Exception as e:
        logger.warning("LLM response generation failed: %s", e)

    return _generate_from_template(context)

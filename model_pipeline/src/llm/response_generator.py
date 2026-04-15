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
        # Rating-based review framing — mirrors how a shopper reads Amazon reviews
        if context.average_rating >= 4.0:
            review_tone = (
                "This product is rated above **4 stars**, so most buyers are happy. "
                "Lead with what customers praise, then briefly mention any concerns if present. "
                "Think of it like scanning Amazon — you'd see mostly positive reviews with a few complaints."
            )
        elif context.average_rating >= 3.0:
            review_tone = (
                "This product is rated between **3 and 4 stars**, so opinions are mixed. "
                "Give equal weight to positive and negative feedback. "
                "Think of it like scanning Amazon — you'd see a split of happy and unhappy buyers."
            )
        else:
            review_tone = (
                "This product is rated below **3 stars**, so most buyers are unhappy. "
                "Lead with the main complaints, then mention any positives if present. "
                "Think of it like scanning Amazon — you'd see mostly negative reviews with a few defenders."
            )

        reviews_instruction = (
            f"- {review_tone}\n"
            "- Quote or closely paraphrase 1–2 real review lines from the CUSTOMER VOICE data. "
            "Present reviews HONESTLY regardless of the recommendation color — "
            "the color reflects the user's FINANCIAL situation, not the product quality."
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

        cold_str = " — very few reviews, so the rating may not be reliable" if context.cold_start_flag else ""
        variance_label = (
            "ratings are all over the place — buyers disagree strongly" if context.rating_variance > 2.0
            else "ratings vary a fair bit" if context.rating_variance > 1.0
            else "ratings are consistent"
        )
        value_label = (
            "buyers feel they get a lot for the price" if context.value_density > 1.5
            else "reasonable for its price range" if context.value_density > 0.8
            else "pricey for what you get compared to alternatives"
        )
        confidence_pct = context.review_confidence * 100
        polarization_label = (
            "opinions are really split — people either love it or hate it" if context.rating_polarization > 0.4
            else "some disagreement among buyers" if context.rating_polarization > 0.2
            else "most buyers agree on the rating"
        )
        quality_concern_label = (
            "there are significant concerns about product quality" if context.quality_risk_score > 0.6
            else "there are some questions about product quality" if context.quality_risk_score > 0.3
            else "product quality appears solid based on reviews"
        )
        price_rank_pct = context.price_category_rank * 100
        price_tier_label = (
            f"one of the more expensive options" if price_rank_pct > 66
            else f"a mid-range option" if price_rank_pct > 33
            else f"a budget-friendly option"
        )

        product_block = f"""WHAT WE KNOW ABOUT THIS PRODUCT (describe these findings in your own words):
- Rating: {context.average_rating:.2f}★ from {context.rating_count:,} reviews{cold_str}{cat_dev_str}
- {variance_label}
- {polarization_label}
- Price vs. quality: {value_label}
- Enough reviews to be confident? {"Yes" if confidence_pct > 50 else "Not really"} ({confidence_pct:.0f}% confidence based on review volume)
- Quality outlook: {quality_concern_label}
- In {context.category or 'its category'}, this is {price_tier_label}"""
    else:
        product_block = "PRODUCT QUALITY SIGNALS: no catalog data (hypothetical evaluation — product not in database)." if context.hypothetical_purchase else "PRODUCT QUALITY SIGNALS: no product data available."

    # --- Review aggregate signals (plain-English labels) ---
    if context.rating_count > 0:
        sentiment_label = (
            "buyers are overwhelmingly happy" if context.sentiment_spread > 0.4
            else "most buyers are satisfied" if context.sentiment_spread > 0.1
            else "buyer opinions are mixed" if context.sentiment_spread > -0.1
            else "more buyers are unhappy than happy"
        )
        depth_label = (
            "reviews are detailed and thoughtful" if context.review_depth_score > 0.6
            else "reviews have a fair amount of detail" if context.review_depth_score > 0.3
            else "most reviews are short and lack detail"
        )
        diversity_label = (
            "reviews come from many different people" if context.reviewer_diversity > 0.8
            else "a decent variety of reviewers" if context.reviewer_diversity > 0.5
            else "a small group of people wrote most reviews — take ratings with a grain of salt"
        )
        extreme_label = (
            "mostly 5-star or 1-star — very few middle-ground ratings" if context.extreme_rating_ratio > 0.7
            else "a fair number of extreme ratings" if context.extreme_rating_ratio > 0.4
            else "ratings are spread across the full range"
        )

        review_signals_block = f"""REVIEW TRUSTWORTHINESS (paraphrase these findings — never quote the labels directly):
- Overall mood: {sentiment_label}
- {context.verified_purchase_ratio:.0%} of reviews are from verified buyers
- Who's reviewing: {diversity_label}
- Rating spread: {extreme_label}
- Review quality: {depth_label}"""
    else:
        review_signals_block = "REVIEW TRUSTWORTHINESS: no reviews available for this product."

    # --- Color-specific tone guidance (positive framing) ---
    if context.recommendation_color == "GREEN":
        color_tone = (
            "TONE: This is a GREEN recommendation — be encouraging and affirming. "
            "Highlight what makes this purchase comfortable for the user. "
            "Keep the tone confident and supportive."
        )
    elif context.recommendation_color == "YELLOW":
        color_tone = (
            "TONE: This is a YELLOW recommendation — be balanced and honest. "
            "Acknowledge the user can technically afford it, but surface the specific concerns. "
            "Suggest a concrete step before buying (e.g. wait a pay cycle, compare alternatives)."
        )
    else:
        color_tone = (
            "TONE: This is a RED recommendation — be kind but firm. "
            "Lead with empathy, then clearly explain why now is not the right time. "
            "Recommend a specific financial priority instead (e.g. emergency fund, debt reduction)."
        )

    prompt = f"""Explain this purchase recommendation using the exact data below. Only cite numbers from the data — never invent figures.

DECISION: {context.recommendation_color} — your response must align with this verdict.
{color_tone}{downgrade_note}

━━━ PURCHASE ━━━
Product : {context.product_name}
Price   : ${context.product_price:,.2f}{"  (hypothetical — user-stated price, no catalog match)" if context.hypothetical_purchase else ""}

━━━ USER FINANCIAL PROFILE ━━━
Employment  : {context.employment_status or "not specified"} | Region: {context.region or "not specified"}
Monthly income    : ${context.monthly_income:,.2f}
Monthly expenses  : ${context.monthly_expenses:,.2f}
Monthly EMI       : ${context.monthly_emi:,.2f}
Left over each month (income − expenses − EMI): ${context.discretionary_income:,.2f}
Savings balance   : ${context.savings_balance:,.2f}  |  Liquid savings: ${context.liquid_savings:,.2f}
Credit score      : {credit_line}  |  Credit risk level: {credit_risk_label}
{loan_lines}

Computed signals (use these to inform your explanation, but describe them in everyday language):
- {afs_line}
- {pir_pct:.1f}% of monthly income would go to this purchase
- {meb_pct:.1f}% of income already committed to fixed costs
- Savings cover the price {context.savings_to_price_ratio:.2f}x over
- Liquid savings = {stir_pct:.1f}% of monthly income
- Emergency fund: {context.emergency_fund_months:.1f} months (target: 3–6)
- {dti_pct:.1f}% of income goes to debt payments
- Headroom after purchase: {context.residual_utility_score:+.3f}
- Savings minus debt relative to income: {context.net_worth_indicator:+.3f}
- Rules triggered: {rules_txt}
{f"- User added context: {context.user_context}" if context.user_context else ""}

━━━ {product_block}

━━━ {review_signals_block}

━━━ CUSTOMER VOICE ━━━
{reviews_block}

━━━ INSTRUCTIONS ━━━
Start your response DIRECTLY with the first heading — no greeting, no intro sentence, no preamble.
Write exactly 4 sections using these headings (with emojis). Output nothing before the first heading.

  **💰 Your Financial Picture**
  **🛍️ About This Product**
  **💬 What Buyers Are Saying**
  **{"✅" if context.recommendation_color == "GREEN" else "⚠️" if context.recommendation_color == "YELLOW" else "🚫"} Our Analysis**

Style:
- Write in plain English a teenager would understand. Describe what the data means
  for the user instead of naming the metric. For example:
  Say "you have **$423** left each month after bills" instead of "discretionary income is $423".
  Say "opinions are really split — buyers either love it or hate it" instead of "highly polarized".
  Say "it's pricey for what you get" instead of "low value for money".
- Bold (**like this**) every dollar amount, percentage, or number you cite.
- Each section is 2–3 sentences of flowing prose — no bullet points inside sections.
- Keep the total response under 180 words.

Section guidance:
💰 Your Financial Picture — Pick the 1–2 most telling facts about affordability. This is about the USER's finances, not the product.
🛍️ About This Product — Mention star rating and review count; summarise quality in everyday words. Be honest — a great product is great even if the user can't afford it.
💬 What Buyers Are Saying — Quote or closely paraphrase 1–2 real reviewer lines from the CUSTOMER VOICE data. Present both positive AND negative feedback honestly. Do NOT cherry-pick only positive reviews for GREEN or only negative reviews for RED. The reviews reflect the PRODUCT, not the user's wallet.
Our Analysis — Open with "Based on your finances and this product's track record, we found this purchase [safe / needs caution / not advisable]." The verdict is primarily driven by FINANCIAL health. Follow with one concrete next step.

━━━ REFERENCE EXAMPLES ━━━
These examples use FICTIONAL data to demonstrate format, tone, and structure ONLY.
DO NOT copy any numbers, product names, review quotes, or facts from these examples.
Your response must use ONLY the real data provided in the context sections above.

EXAMPLE (RED — tone: kind but firm):
💰 Your Financial Picture
These headphones cost **$349**, which is more than the **$210** you have left each month after all your bills. With **82%** of your income already committed to fixed costs, there's very little room for a purchase this size.

🛍️ About This Product
This model has a **3.6-star** rating from **1,450** reviews, which is below average for wireless headphones. Opinions are mixed, and it ranks as one of the pricier options in its category.

💬 What Buyers Are Saying
One reviewer called them "comfortable for long flights," but several others reported the noise cancellation failing after a few months, with one writing "bass died completely within 6 weeks."

🚫 Our Analysis
Based on your finances and this product's track record, we found this purchase not advisable at this time. We strongly recommend building your emergency fund past **1.5** months before considering big purchases.

EXAMPLE (YELLOW — tone: balanced, cautious):
💰 Your Financial Picture
You have **$720** left each month after bills, and these **$159** running shoes would take about **22%** of that. Your savings could cover the price **5** times over, but your emergency fund is sitting at only **2.8** months.

🛍️ About This Product
These running shoes have a **4.1-star** rating from **3,800** reviews. They're a mid-range option in the athletic footwear category, and quality feedback is mostly positive though not unanimous.

💬 What Buyers Are Saying
Many runners praise the comfort, with one saying "best shoes I've trained in all year." However, a few noted the sole wears down quickly, with one reviewer mentioning "traction was gone after 3 months of regular use."

⚠️ Our Analysis
Based on your finances and this product's track record, we found this purchase needs caution. You can afford it, but your emergency savings are below the **3-month** target. Consider waiting until next month to keep your buffer intact.

EXAMPLE (GREEN — tone: encouraging, affirming):
💰 Your Financial Picture
After all your monthly bills, you still have **$1,850** left over, and your savings could cover this **$79** coffee maker more than **20** times over. You're in a strong position for this purchase.

🛍️ About This Product
This coffee maker has a strong **4.7-star** rating from **12,400** reviews and is one of the top-rated options in its price range. Buyers consistently praise the brew quality and durability.

💬 What Buyers Are Saying
One reviewer called it "the best coffee I've made at home," while another noted it "paid for itself in two weeks of skipping the cafe."

✅ Our Analysis
Based on your finances and this product's track record, we found this purchase safe and well within your means. Go ahead if it fits your needs — just keep an eye on your monthly spending as usual.

REMINDER: The examples above are for FORMAT and TONE only. Every number, product detail, and review quote in YOUR response must come from the real data provided in the sections above. Never copy or reuse content from the examples."""

    try:
        raw = llm_provider.generate(prompt, temperature=0.35)
        if raw and len(raw.strip()) > 20:
            text = raw.strip()
            # Strip any preamble the LLM adds before the first heading.
            for anchor in ("**💰", "💰"):
                idx = text.find(anchor)
                if idx > 0:
                    text = text[idx:]
                    break
            # LLMs sometimes drop the opening ** on the very first token.
            # Ensure the first heading has proper bold markers.
            if text.startswith("💰") and not text.startswith("**💰"):
                text = "**" + text
            return text
    except Exception as e:
        logger.warning("LLM response generation failed: %s", e)

    return _generate_from_template(context)

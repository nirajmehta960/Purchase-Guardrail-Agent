"""
Response templates — used by the MockProvider and as fallbacks for guardrail failures.

Each template is a Python format string with named placeholders that
match fields in RecommendationContext. The response_generator fills them in.
"""

VERSION = "v1.0"


# ---------------------------------------------------------------------------
# GREEN — purchase is financially comfortable
# ---------------------------------------------------------------------------

GREEN_TEMPLATES = [
    (
        "Great news! Purchasing {product_name} at ${product_price:.2f} fits "
        "comfortably within your budget. Your savings cover this purchase "
        "{savings_to_price_ratio:.0f}x over, and you still have "
        "{emergency_fund_months:.1f} months of emergency fund remaining. "
        "This looks like a financially sound decision."
    ),
    (
        "Based on your financial profile, buying {product_name} at "
        "${product_price:.2f} looks very manageable. You have healthy savings "
        "and a solid emergency fund, so this purchase shouldn't create any "
        "financial strain."
    ),
]

# ---------------------------------------------------------------------------
# YELLOW — purchase is possible but comes with concerns
# ---------------------------------------------------------------------------

YELLOW_TEMPLATES = [
    (
        "I'd suggest thinking carefully before buying {product_name} at "
        "${product_price:.2f}. {concern_summary} While you can technically "
        "afford this, it would be worth considering whether this is a need "
        "or a want right now."
    ),
    (
        "You could purchase {product_name} at ${product_price:.2f}, but I'd "
        "recommend some caution. {concern_summary} It might be worth waiting "
        "a bit or looking at more affordable alternatives."
    ),
]

# ---------------------------------------------------------------------------
# RED — purchase is financially inadvisable
# ---------------------------------------------------------------------------

RED_TEMPLATES = [
    (
        "I'd recommend holding off on purchasing {product_name} at "
        "${product_price:.2f} right now. {concern_summary} Consider building "
        "up your savings first, or looking at more affordable alternatives "
        "that better fit your current budget."
    ),
    (
        "This might not be the best time to buy {product_name} at "
        "${product_price:.2f}. {concern_summary} Your financial health should "
        "come first — let's make sure the essentials are covered before this "
        "purchase."
    ),
]


# ---------------------------------------------------------------------------
# Concern explanations — human-readable translations of triggered rules
# ---------------------------------------------------------------------------

RULE_EXPLANATIONS = {
    # Layer 1 — Financial Engine RED rules (keys match financial_engine.py triggered_rules strings)
    "red:cant_afford_from_any_angle":       "this purchase exceeds what your income and savings can comfortably cover",
    "red:maxed_budget_significant_purchase": "your monthly expenses are already very high relative to income, and "
                                             "this purchase would further stretch your budget",
    "red:underwater_no_surplus_significant": "your current debt position means this purchase could put you in a "
                                             "difficult financial spot",
    "red:paycheck_to_paycheck_wipes_net":   "your emergency fund is very thin, and taking on this expense could "
                                             "leave you without a financial safety net",

    # Layer 1 — Financial Engine YELLOW rules
    "yellow:income_pressure": "this purchase would push your discretionary spending into uncomfortable territory",
    "yellow:savings_strain":  "your savings relative to this purchase price suggest it's a significant expense for you",
    "yellow:debt_stress":     "your monthly obligations are already high, and this would add more pressure",
    "yellow:low_resilience":  "your emergency fund could use some building up before taking on additional expenses",
    "yellow:weak_profile":    "a few indicators in your financial profile suggest some caution with larger purchases right now",

    # Layer 2 — Downgrade Engine product rules
    "PR1:underrated_unverified":     "the product's quality signals raise some concerns",
    "PR2:worst_premium_in_category": "the product's quality signals raise some concerns",
    "PR3:polarized_new_expensive":   "the product's quality signals raise some concerns",

    # Layer 2 — Downgrade Engine review rules
    "RR1:fake_review_pattern":           "the product's review patterns suggest mixed feedback",
    "RR2:unverified_opinion_domination": "the product's review patterns suggest mixed feedback",
    "RR3:negative_shallow_unverified":   "the product's review patterns suggest mixed feedback",
}

# Fallback for unknown rules
DEFAULT_CONCERN = "some aspects of your financial profile suggest caution with this purchase"


def get_concern_summary(triggered_rules: list[str], was_downgraded: bool) -> str:
    """Build a human-readable concern summary from triggered rule codes."""
    if not triggered_rules and not was_downgraded:
        return ""

    concerns = []
    for rule in triggered_rules:
        explanation = RULE_EXPLANATIONS.get(rule, DEFAULT_CONCERN)
        if explanation not in concerns:
            concerns.append(explanation)

    if was_downgraded:
        concerns.append(
            "additionally, the product's quality and review signals indicate "
            "some extra caution may be warranted"
        )

    if not concerns:
        concerns.append(DEFAULT_CONCERN)

    # Capitalize first concern, join with semicolons
    first = concerns[0][0].upper() + concerns[0][1:]
    if len(concerns) == 1:
        return first + "."
    rest = "; ".join(concerns[1:])
    return f"{first}; {rest}."


# ---------------------------------------------------------------------------
# Out-of-scope and error responses
# ---------------------------------------------------------------------------

OUT_OF_SCOPE_RESPONSE = (
    "I'm SavVio, your purchase advisor! I can help you decide whether a "
    "product purchase is a good fit for your finances. Try asking me "
    "something like 'Can I buy <product name>?'"
)

PRODUCT_NOT_FOUND_RESPONSE = (
    "I wasn't able to find that product in our catalog. Could you try "
    "rephrasing with the full product name? For example: "
    "'Can I buy the Sony WH-1000XM5?'"
)

USER_NOT_FOUND_RESPONSE = (
    "I don't have your financial profile on file yet. Please make sure "
    "your account is set up before asking for purchase recommendations."
)

GENERAL_ERROR_RESPONSE = (
    "I encountered an issue processing your request. Please try again, "
    "or rephrase your question."
)

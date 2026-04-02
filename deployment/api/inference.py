"""
Inference Orchestrator — Full prediction pipeline for the /predict endpoint.

Ties together all existing SavVio modules for a single inference request:
    1. Load user financial profile from DB
    2. Parse intent via LLM (or skip if product_id provided)
    3. Resolve product via pgvector similarity search (or direct DB lookup)
    4. Look up product reviews from DB
    5. Compute affordability features (6 financial features)
    6. Compute product features (7 features) + review features (6 features)
    7. Run Deterministic Engine (Layer 1) → authoritative GREEN/YELLOW/RED
    8. Run Downgrade Engine (Layer 2) → potential one-step downgrade
    9. Run ML Model → confidence score (cannot override deterministic color)
    10. Run LLM response generation + guardrail verification
    11. Return structured PredictResponse

Usage:
    from deployment.api.inference import run_inference
    from deployment.api.model_loader import model_manager

    response = run_inference(request, model_manager)
"""

from __future__ import annotations

import logging
import time

import pandas as pd
from sqlalchemy import text

from deployment.api.schemas import PredictRequest, PredictResponse

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# User & Product Lookup Helpers
# ---------------------------------------------------------------------------

def _load_user_financial_profile(user_id: str, db_engine) -> dict | None:
    """Load a user's financial profile from the financial_profiles table."""
    sql = text("""
        SELECT user_id, monthly_income, monthly_expenses, savings_balance,
               has_loan, loan_amount, monthly_emi, loan_interest_rate,
               loan_term_months, credit_score, employment_status, region,
               liquid_savings, discretionary_income, debt_to_income_ratio,
               saving_to_income_ratio, monthly_expense_burden_ratio,
               emergency_fund_months
        FROM financial_profiles
        WHERE user_id = :user_id
    """)

    try:
        with db_engine.connect() as conn:
            row = conn.execute(sql, {"user_id": user_id}).fetchone()
    except Exception as e:
        logger.error("Failed to load financial profile for user_id=%s: %s", user_id, e)
        return None

    if row is None:
        return None

    # Convert row to dict — handles both Row and LegacyRow
    columns = [
        "user_id", "monthly_income", "monthly_expenses", "savings_balance",
        "has_loan", "loan_amount", "monthly_emi", "loan_interest_rate",
        "loan_term_months", "credit_score", "employment_status", "region",
        "liquid_savings", "discretionary_income", "debt_to_income_ratio",
        "saving_to_income_ratio", "monthly_expense_burden_ratio",
        "emergency_fund_months",
    ]
    return dict(zip(columns, row))


def _load_product_reviews(product_id: str, db_engine) -> pd.DataFrame:
    """Load all reviews for a product from the reviews table."""
    sql = text("""
        SELECT user_id, product_id, rating, review_title, review_text,
               verified_purchase, helpful_vote
        FROM reviews
        WHERE product_id = :product_id
    """)

    try:
        with db_engine.connect() as conn:
            rows = conn.execute(sql, {"product_id": product_id}).fetchall()
    except Exception as e:
        logger.error("Failed to load reviews for product_id=%s: %s", product_id, e)
        return pd.DataFrame()

    if not rows:
        return pd.DataFrame()

    columns = [
        "user_id", "product_id", "rating", "review_title", "review_text",
        "verified_purchase", "helpful_vote",
    ]
    return pd.DataFrame(rows, columns=columns)


def _load_product_row(product_id: str, db_engine) -> pd.Series | None:
    """Load a single product row for feature computation."""
    sql = text("""
        SELECT product_id, product_name, price, average_rating,
               rating_number, rating_variance, category
        FROM products
        WHERE product_id = :product_id
    """)

    try:
        with db_engine.connect() as conn:
            row = conn.execute(sql, {"product_id": product_id}).fetchone()
    except Exception as e:
        logger.error("Failed to load product row for product_id=%s: %s", product_id, e)
        return None

    if row is None:
        return None

    columns = [
        "product_id", "product_name", "price", "average_rating",
        "rating_number", "rating_variance", "category",
    ]
    return pd.Series(dict(zip(columns, row)))


# ---------------------------------------------------------------------------
# Main Inference Flow
# ---------------------------------------------------------------------------

def run_inference(request: PredictRequest, manager) -> PredictResponse:
    """Execute the full inference pipeline for a single /predict request.

    Args:
        request: Validated PredictRequest from the API endpoint.
        manager: The ModelManager singleton with loaded resources.

    Returns:
        PredictResponse with recommendation, confidence, and explanation.
    """
    start_time = time.time()

    # Import existing modules (already on sys.path via model_loader)
    from deterministic_engine.financial_engine import DecisionEngine
    from deterministic_engine.downgrade_engine import DowngradeEngine
    from features.financial_features import compute_affordability
    from features.product_features import compute_product_features, ProductFeatures
    from features.review_features import compute_review_features, ReviewFeatures
    from llm.response_generator import RecommendationContext, generate_response, _generate_from_template
    from llm.guardrails import check_response
    from llm.product_resolver import resolve_product, resolve_product_by_id
    from llm.intent_parser import parse_user_input
    from llm.prompts.response_templates import (
        OUT_OF_SCOPE_RESPONSE,
        PRODUCT_NOT_FOUND_RESPONSE,
        USER_NOT_FOUND_RESPONSE,
    )

    # parsed is only assigned in the NL branch; initialise so the
    # context-building step at the bottom can safely reference it.
    parsed = None
    user_context_val = None

    # ------------------------------------------------------------------
    # Step 1: Load user financial profile
    # ------------------------------------------------------------------
    if manager.db_engine is None:
        logger.error("No DB connection — cannot process request.")
        return PredictResponse(
            recommendation="YELLOW",
            confidence=0.0,
            explanation="Service is temporarily unavailable. Please try again later.",
            guardrail_passed=True,
        )

    user_profile = _load_user_financial_profile(request.user_id, manager.db_engine)
    if user_profile is None:
        logger.warning("User not found: %s", request.user_id)
        return PredictResponse(
            recommendation="YELLOW",
            confidence=0.0,
            explanation=USER_NOT_FOUND_RESPONSE,
            guardrail_passed=True,
        )

    logger.info("User profile loaded for: %s", request.user_id)

    # ------------------------------------------------------------------
    # Step 2: Parse intent / Resolve product
    # ------------------------------------------------------------------
    product_match = None
    product_name = None
    product_price = None
    product_id = None

    if request.product_id:
        # Direct product_id mode — skip intent parsing
        product_match = resolve_product_by_id(request.product_id, manager.db_engine)
        if product_match:
            product_name = product_match.product_name
            product_price = product_match.price
            product_id = product_match.product_id
            logger.info("Direct product lookup: %s → %s ($%.2f)", request.product_id, product_name, product_price)
        else:
            return PredictResponse(
                recommendation="YELLOW",
                confidence=0.0,
                explanation=PRODUCT_NOT_FOUND_RESPONSE,
                guardrail_passed=True,
            )
    else:
        # Natural language mode — parse intent first
        parsed = parse_user_input(request.user_query, manager.llm_provider)
        logger.info("Intent parsed: %s, product_ref=%s", parsed.intent, parsed.product_reference)

        if parsed.intent == "out_of_scope":
            return PredictResponse(
                recommendation="YELLOW",
                confidence=0.0,
                explanation=OUT_OF_SCOPE_RESPONSE,
                guardrail_passed=True,
            )

        # Resolve product via pgvector similarity search
        if parsed.product_reference:
            product_match = resolve_product(parsed.product_reference, manager.db_engine)

        if product_match:
            product_name = product_match.product_name
            product_price = product_match.price
            product_id = product_match.product_id
            user_context_val = getattr(parsed, "user_context", None)
            logger.info("Product resolved: '%s' → '%s' ($%.2f)",
                        parsed.product_reference, product_name, product_price)
        else:
            return PredictResponse(
                recommendation="YELLOW",
                confidence=0.0,
                explanation=PRODUCT_NOT_FOUND_RESPONSE,
                guardrail_passed=True,
            )

    # ------------------------------------------------------------------
    # Step 3: Compute affordability features (6 financial features)
    # ------------------------------------------------------------------
    affordability = compute_affordability(
        user_financial_profile=user_profile,
        product_price=product_price,
    )
    financial_dict = affordability.to_dict()

    # Merge DB-level financial features into the dict for the engine
    financial_dict["debt_to_income_ratio"] = float(user_profile.get("debt_to_income_ratio", 0) or 0)
    financial_dict["monthly_expense_burden_ratio"] = float(user_profile.get("monthly_expense_burden_ratio", 0) or 0)
    financial_dict["emergency_fund_months"] = float(user_profile.get("emergency_fund_months", 0) or 0)
    financial_dict["saving_to_income_ratio"] = float(user_profile.get("saving_to_income_ratio", 0) or 0)

    logger.info("Affordability computed: score=%.2f, SPR=%.2f",
                financial_dict.get("affordability_score", 0),
                financial_dict.get("savings_to_price_ratio", 0))

    # ------------------------------------------------------------------
    # Step 4: Run Deterministic Engine (Layer 1)
    # ------------------------------------------------------------------
    engine = DecisionEngine()
    decision = engine.decide(financial_dict, {"price": product_price})

    l1_color = decision.decision_category
    triggered_rules = decision.triggered_rules
    logger.info("Layer 1 decision: %s, rules=%s", l1_color, triggered_rules)

    # ------------------------------------------------------------------
    # Step 5: Run Downgrade Engine (Layer 2)
    # ------------------------------------------------------------------
    was_downgraded = False
    final_color = l1_color
    product_row = None
    product_feats = None
    review_feats = None

    if product_id:
        product_row = _load_product_row(product_id, manager.db_engine)
        reviews_df = _load_product_reviews(product_id, manager.db_engine)

        if product_row is not None:
            product_feats = compute_product_features(
                product_row,
                manager.category_stats,
                manager.max_rating_number,
            )
            review_feats = compute_review_features(reviews_df)

            downgrade_engine = DowngradeEngine()
            downgrade_result = downgrade_engine.evaluate(
                financial_label=l1_color,
                product_features=product_feats,
                review_features=review_feats,
            )
            final_color = downgrade_result.final_label
            was_downgraded = downgrade_result.was_downgraded

            if was_downgraded:
                triggered_rules.extend(downgrade_result.product_triggers)
                triggered_rules.extend(downgrade_result.review_triggers)
                logger.info("Layer 2 downgrade: %s → %s", l1_color, final_color)

    # ------------------------------------------------------------------
    # Step 6: Run ML Model → confidence score
    # ------------------------------------------------------------------
    # The ML model provides a confidence score but CANNOT override the
    # deterministic engine's color. It's informational only.
    model_confidence = 0.0
    if manager.model is not None and manager.feature_pipeline is not None:
        try:
            import pandas as _pd

            # Assemble the raw feature row matching the training schema.
            # Raw user fields from DB
            raw_row = {
                "monthly_income":          float(user_profile.get("monthly_income", 0) or 0),
                "monthly_expenses":        float(user_profile.get("monthly_expenses", 0) or 0),
                "savings_balance":         float(user_profile.get("savings_balance", 0) or 0),
                "has_loan":                int(bool(user_profile.get("has_loan", 0))),
                "loan_amount":             float(user_profile.get("loan_amount", 0) or 0),
                "monthly_emi":             float(user_profile.get("monthly_emi", 0) or 0),
                "loan_interest_rate":      float(user_profile.get("loan_interest_rate", 0) or 0),
                "loan_term_months":        float(user_profile.get("loan_term_months", 0) or 0),
                "credit_score":            float(user_profile.get("credit_score", 0) or 0),
                "employment_status":       str(user_profile.get("employment_status", "unknown") or "unknown"),
                "region":                  str(user_profile.get("region", "unknown") or "unknown"),
                # DB-precomputed financial ratios
                "liquid_savings":          float(user_profile.get("liquid_savings", 0) or 0),
                "discretionary_income":    float(user_profile.get("discretionary_income", 0) or 0),
                "debt_to_income_ratio":    float(financial_dict.get("debt_to_income_ratio", 0)),
                "saving_to_income_ratio":  float(user_profile.get("saving_to_income_ratio", 0) or 0),
                "monthly_expense_burden_ratio": float(financial_dict.get("monthly_expense_burden_ratio", 0)),
                "emergency_fund_months":   float(financial_dict.get("emergency_fund_months", 0)),
                # Affordability computed features
                "affordability_score":     float(financial_dict.get("affordability_score", 0)),
                "price_to_income_ratio":   float(financial_dict.get("price_to_income_ratio", 0)),
                "savings_to_price_ratio":  float(financial_dict.get("savings_to_price_ratio", 0)),
                "net_worth_indicator":     float(financial_dict.get("net_worth_indicator", 0)),
                "credit_risk_indicator":   float(financial_dict.get("credit_risk_indicator", 0) or 0),
                # Product raw fields — column name matches training data
                "product_price":           float(product_price or 0),
                "average_rating":          float(product_row["average_rating"] if product_row is not None else 0),
                "rating_number":           float(product_row["rating_number"] if product_row is not None else 0),
                "rating_variance":         float(product_row["rating_variance"] if product_row is not None else 0),
                "category":                str(product_row["category"] if product_row is not None else "unknown"),
                # Product computed features
                "value_density":           float(product_feats.value_density if product_row is not None else 0),
                "review_confidence":       float(product_feats.review_confidence if product_row is not None else 0),
                "rating_polarization":     float(product_feats.rating_polarization if product_row is not None else 0),
                "quality_risk_score":      float(product_feats.quality_risk_score if product_row is not None else 0),
                "cold_start_flag":         int(product_feats.cold_start_flag if product_row is not None else 0),
                "price_category_rank":     float(product_feats.price_category_rank if product_row is not None else 0),
                "category_rating_deviation": float(product_feats.category_rating_deviation if product_row is not None else 0),
                # Review computed features
                "verified_purchase_ratio": float(review_feats.verified_purchase_ratio if product_row is not None else 0),
                "helpful_concentration":   float(review_feats.helpful_concentration if product_row is not None else 0),
                "sentiment_spread":        float(review_feats.sentiment_spread if product_row is not None else 0),
                "review_depth_score":      float(review_feats.review_depth_score if product_row is not None else 0),
                "reviewer_diversity":      float(review_feats.reviewer_diversity if product_row is not None else 0),
                "extreme_rating_ratio":    float(review_feats.extreme_rating_ratio if product_row is not None else 0),
            }
            feature_df = _pd.DataFrame([raw_row])
            X_preprocessed = manager.feature_pipeline.transform(feature_df)
            _, model_confidence = manager.predict(X_preprocessed)
            logger.info("ML confidence: %.2f", model_confidence)
        except Exception as e:
            logger.warning("ML model scoring failed: %s", e)

    # ------------------------------------------------------------------
    # Step 7: Run LLM response generation + guardrails
    # ------------------------------------------------------------------
    context = RecommendationContext(
        product_name=product_name or "the product",
        product_price=product_price or 0.0,
        recommendation_color=final_color,
        original_color=l1_color if was_downgraded else final_color,
        was_downgraded=was_downgraded,
        triggered_rules=triggered_rules,
        confidence_scores={final_color: model_confidence},
        user_context=user_context_val,
        affordability_score=float(financial_dict.get("affordability_score", 0.0)),
        savings_to_price_ratio=float(financial_dict.get("savings_to_price_ratio", 0.0)),
        emergency_fund_months=float(financial_dict.get("emergency_fund_months", 0.0)),
        debt_to_income_ratio=float(financial_dict.get("debt_to_income_ratio", 0.0)),
    )

    response_text = generate_response(context, manager.llm_provider)
    guardrail_result = check_response(response_text, final_color, context)

    if not guardrail_result.passed:
        logger.warning(
            "Guardrail violations: %s — falling back to template",
            guardrail_result.violations,
        )
        response_text = _generate_from_template(context)
        guardrail_result = check_response(response_text, final_color, context)

    elapsed = time.time() - start_time
    logger.info(
        "Inference complete: color=%s, confidence=%.2f, downgraded=%s, "
        "guardrail_passed=%s, latency=%.3fs",
        final_color, model_confidence, was_downgraded,
        guardrail_result.passed, elapsed,
    )

    return PredictResponse(
        recommendation=final_color,
        confidence=model_confidence,
        explanation=response_text,
        product_name=product_name,
        product_price=product_price,
        triggered_rules=triggered_rules,
        was_downgraded=was_downgraded,
        guardrail_passed=guardrail_result.passed,
    )

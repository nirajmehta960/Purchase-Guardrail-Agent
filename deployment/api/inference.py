"""
Inference Orchestrator — Full prediction pipeline for the /predict endpoint.

    _load_user_financial_profile()  → DB lookup
    _resolve_product()              → intent parsing / product resolution
    _load_product_data()            → product row + reviews from DB
    _score_ml_model()               → ML prediction (AUTHORITY)
    _generate_explanation()         → LLM response + guardrails

    run_inference()                 → orchestrates the above stages
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any

import pandas as pd
from sqlalchemy import text

from deployment.api.config import APIConfig
from deployment.api.schemas import (
    FinancialFeaturesView,
    PredictRequest,
    PredictResponse,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Result Types
# ---------------------------------------------------------------------------

@dataclass
class ProductResolution:
    product_name: str | None = None
    product_price: float | None = None
    product_id: str | None = None
    evaluation_mode: str = "catalog"
    user_context: str | None = None
    parsed_intent: Any = None


@dataclass
class MLScore:
    confidence: float | None = None
    predicted_label: str | None = None
    unavailable_reason: str | None = None
    # Financial features returned by model for API response / LLM
    affordability_score: float = 0.0
    affordability_unreliable: bool = False
    savings_to_price_ratio: float = 0.0
    price_to_income_ratio: float = 0.0
    residual_utility_score: float = 0.0
    net_worth_indicator: float = 0.0
    credit_risk_indicator: float = 0.0


# ---------------------------------------------------------------------------
# Stage 1: Load User Financial Profile
# ---------------------------------------------------------------------------

def _load_user_financial_profile(user_id: str, db_engine) -> dict | None:
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

    columns = [
        "user_id", "monthly_income", "monthly_expenses", "savings_balance",
        "has_loan", "loan_amount", "monthly_emi", "loan_interest_rate",
        "loan_term_months", "credit_score", "employment_status", "region",
        "liquid_savings", "discretionary_income", "debt_to_income_ratio",
        "saving_to_income_ratio", "monthly_expense_burden_ratio",
        "emergency_fund_months",
    ]
    return dict(zip(columns, row))


# ---------------------------------------------------------------------------
# Stage 2: Resolve Product
# ---------------------------------------------------------------------------

def _resolve_product(request: PredictRequest, manager) -> ProductResolution | PredictResponse:
    from llm.product_resolver import resolve_product, resolve_product_by_id
    from llm.intent_parser import parse_user_input
    from llm.prompts.response_templates import OUT_OF_SCOPE_RESPONSE, PRODUCT_NOT_FOUND_RESPONSE

    if request.product_id:
        match = resolve_product_by_id(request.product_id, manager.db_engine)
        if match:
            return ProductResolution(
                product_name=match.product_name, product_price=match.price,
                product_id=match.product_id,
            )
        return PredictResponse(
            recommendation="YELLOW", confidence=None,
            explanation=PRODUCT_NOT_FOUND_RESPONSE, evaluation_mode="none",
        )

    parsed = parse_user_input(request.user_query, manager.llm_provider)

    if parsed.intent == "out_of_scope":
        return PredictResponse(
            recommendation="YELLOW", confidence=None,
            explanation=OUT_OF_SCOPE_RESPONSE, evaluation_mode="none",
        )

    price_hint = getattr(parsed, "price_hint", None)
    user_ctx = getattr(parsed, "user_context", None)
    match = None

    if parsed.product_reference:
        match = resolve_product(parsed.product_reference, manager.db_engine, price_hint=price_hint)

    if match:
        return ProductResolution(
            product_name=match.product_name, product_price=match.price,
            product_id=match.product_id, user_context=user_ctx, parsed_intent=parsed,
        )

    if price_hint is not None and parsed.product_reference:
        return ProductResolution(
            product_name=parsed.product_reference.strip(), product_price=float(price_hint),
            evaluation_mode="hypothetical", user_context=user_ctx, parsed_intent=parsed,
        )

    return PredictResponse(
        recommendation="YELLOW", confidence=None,
        explanation=PRODUCT_NOT_FOUND_RESPONSE, evaluation_mode="none",
    )


# ---------------------------------------------------------------------------
# Stage 3: Load Product Data
# ---------------------------------------------------------------------------

def _load_product_data(product: ProductResolution, manager) -> dict:
    """Load product row + reviews from DB, return flat dict of raw + computed features."""
    from features.product_features import compute_product_features, compute_category_stats
    from features.review_features import compute_review_features

    empty = {
        "average_rating": 0, "rating_number": 0, "rating_variance": 0, "category": "unknown",
        "value_density": 0, "review_confidence": 0, "rating_polarization": 0,
        "quality_risk_score": 0, "cold_start_flag": 0, "price_category_rank": 0,
        "category_rating_deviation": 0, "verified_purchase_ratio": 0,
        "helpful_concentration": 0, "sentiment_spread": 0, "review_depth_score": 0,
        "reviewer_diversity": 0, "extreme_rating_ratio": 0,
    }

    if not product.product_id or not manager.db_engine:
        return empty

    # Load product row
    try:
        with manager.db_engine.connect() as conn:
            row = conn.execute(text(
                "SELECT product_id, product_name, price, average_rating, "
                "rating_number, rating_variance, category FROM products WHERE product_id = :pid"
            ), {"pid": product.product_id}).fetchone()
    except Exception as e:
        logger.error("Failed to load product %s: %s", product.product_id, e)
        return empty

    if row is None:
        return empty

    cols = ["product_id", "product_name", "price", "average_rating", "rating_number", "rating_variance", "category"]
    product_row = pd.Series(dict(zip(cols, row)))

    # Load reviews
    try:
        with manager.db_engine.connect() as conn:
            review_rows = conn.execute(text(
                "SELECT user_id, product_id, rating, review_title, review_text, "
                "verified_purchase, helpful_vote FROM reviews WHERE product_id = :pid"
            ), {"pid": product.product_id}).fetchall()
    except Exception as e:
        logger.error("Failed to load reviews for %s: %s", product.product_id, e)
        review_rows = []

    review_cols = ["user_id", "product_id", "rating", "review_title", "review_text", "verified_purchase", "helpful_vote"]
    reviews_df = pd.DataFrame(review_rows, columns=review_cols) if review_rows else pd.DataFrame()

    # Compute product + review features
    products_df = pd.read_sql("SELECT product_id, price, average_rating, rating_number, rating_variance, category FROM products", manager.db_engine)
    cat_stats = compute_category_stats(products_df)
    max_rn = float(products_df["rating_number"].max() or 0)

    pf = compute_product_features(product_row, cat_stats, max_rn)
    rf = compute_review_features(reviews_df)

    return {
        "average_rating": float(product_row["average_rating"] or 0),
        "rating_number": float(product_row["rating_number"] or 0),
        "rating_variance": float(product_row["rating_variance"] or 0),
        "category": str(product_row["category"] or "unknown"),
        **pf.to_dict(), **rf.to_dict(),
    }


# ---------------------------------------------------------------------------
# Stage 4: ML Model Scoring (AUTHORITY)
# ---------------------------------------------------------------------------

def _score_ml_model(user_profile: dict, product: ProductResolution, product_data: dict, manager) -> MLScore:
    if manager.model is None:
        return MLScore(unavailable_reason="no_model")

    try:
        raw_row = {**user_profile, "product_price": float(product.product_price or 0), **product_data}
        result = manager.predict(pd.DataFrame([raw_row]))

        r = result.iloc[0]
        return MLScore(
            predicted_label=str(r["label"]).strip().upper(),
            confidence=float(r["confidence"]),
            affordability_score=float(r.get("affordability_score", 0)),
            affordability_unreliable=bool(r.get("affordability_score_unreliable", False)),
            savings_to_price_ratio=float(r.get("savings_to_price_ratio", 0)),
            price_to_income_ratio=float(r.get("price_to_income_ratio", 0)),
            residual_utility_score=float(r.get("residual_utility_score", 0)),
            net_worth_indicator=float(r.get("net_worth_indicator", 0)),
            credit_risk_indicator=float(r.get("credit_risk_indicator", 0)),
        )

    except Exception as e:
        logger.warning("ML model scoring failed: %s", e, exc_info=True)
        return MLScore(unavailable_reason="scoring_error")


# ---------------------------------------------------------------------------
# Stage 5: LLM Response Generation + Guardrails
# ---------------------------------------------------------------------------

def _generate_explanation(product: ProductResolution, ml: MLScore, user_profile: dict, manager) -> str:
    from llm.response_generator import RecommendationContext, generate_response, _generate_from_template
    from llm.guardrails import check_response

    recommendation_color = ml.predicted_label or "YELLOW"

    context = RecommendationContext(
        product_name=product.product_name or "the product",
        product_price=product.product_price or 0.0,
        recommendation_color=recommendation_color,
        original_color=recommendation_color,
        was_downgraded=False,
        triggered_rules=[],
        confidence_scores={recommendation_color: ml.confidence or 0.0},
        ml_confidence=ml.confidence,
        ml_unavailable_reason=ml.unavailable_reason,
        hypothetical_purchase=(product.evaluation_mode == "hypothetical"),
        user_context=product.user_context,
        affordability_score=ml.affordability_score,
        affordability_score_unreliable=ml.affordability_unreliable,
        savings_to_price_ratio=ml.savings_to_price_ratio,
        emergency_fund_months=float(user_profile.get("emergency_fund_months", 0) or 0),
        debt_to_income_ratio=float(user_profile.get("debt_to_income_ratio", 0) or 0),
        monthly_income=float(user_profile.get("monthly_income", 0) or 0),
        monthly_expense_burden_ratio=float(user_profile.get("monthly_expense_burden_ratio", 0) or 0),
        price_to_income_ratio=ml.price_to_income_ratio,
        credit_score=user_profile.get("credit_score"),
        review_snippets=[],
    )

    response_text = generate_response(context, manager.llm_provider)
    guardrail = check_response(response_text, recommendation_color, context)

    if not guardrail.passed:
        logger.warning("Guardrail violations: %s — falling back to template", guardrail.violations)
        response_text = _generate_from_template(context)

    return response_text


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------

def run_inference(request: PredictRequest, manager) -> PredictResponse:
    from llm.prompts.response_templates import USER_NOT_FOUND_RESPONSE

    start_time = time.time()

    if manager.db_engine is None:
        return PredictResponse(
            recommendation="YELLOW", confidence=None,
            explanation="Service is temporarily unavailable.", evaluation_mode="none",
        )

    user_profile = _load_user_financial_profile(request.user_id, manager.db_engine)
    if user_profile is None:
        return PredictResponse(
            recommendation="YELLOW", confidence=None,
            explanation=USER_NOT_FOUND_RESPONSE, evaluation_mode="none",
        )

    resolution = _resolve_product(request, manager)
    if isinstance(resolution, PredictResponse):
        return resolution

    product_data = _load_product_data(resolution, manager)

    ml = _score_ml_model(user_profile, resolution, product_data, manager)

    explanation = _generate_explanation(resolution, ml, user_profile, manager)

    elapsed = time.time() - start_time
    logger.info("Inference complete: %s (%.3fs)", ml.predicted_label, elapsed)

    return PredictResponse(
        recommendation=ml.predicted_label or "YELLOW",
        confidence=ml.confidence,
        ml_unavailable_reason=ml.unavailable_reason,
        explanation=explanation,
        product_name=resolution.product_name,
        product_price=resolution.product_price,
        evaluation_mode=resolution.evaluation_mode,
        affordability_score=ml.affordability_score,
        affordability_score_unreliable=ml.affordability_unreliable,
        emergency_fund_months=float(user_profile.get("emergency_fund_months", 0) or 0),
        debt_to_income_ratio=float(user_profile.get("debt_to_income_ratio", 0) or 0),
        financial_features=FinancialFeaturesView(
            discretionary_income=float(user_profile.get("discretionary_income", 0) or 0),
            debt_to_income_ratio=float(user_profile.get("debt_to_income_ratio", 0) or 0),
            saving_to_income_ratio=float(user_profile.get("saving_to_income_ratio", 0) or 0),
            monthly_expense_burden_ratio=float(user_profile.get("monthly_expense_burden_ratio", 0) or 0),
            emergency_fund_months=float(user_profile.get("emergency_fund_months", 0) or 0),
            affordability_score=ml.affordability_score,
            price_to_income_ratio=ml.price_to_income_ratio,
            residual_utility_score=ml.residual_utility_score,
            savings_to_price_ratio=ml.savings_to_price_ratio,
            net_worth_indicator=ml.net_worth_indicator,
            credit_risk_indicator=ml.credit_risk_indicator,
        ),
        ml_predicted_label=ml.predicted_label,
        ml_model_name=APIConfig.ML_MODEL_DISPLAY_NAME,
    )

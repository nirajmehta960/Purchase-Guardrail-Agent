"""
Inference Orchestrator — Full prediction pipeline for the /predict endpoint.

The ML model is the authority for GREEN/YELLOW/RED decisions.
Decomposed into discrete pipeline stages, each independently testable:

    _load_user_financial_profile()  → DB lookup
    _resolve_product()              → intent parsing / product resolution
    _compute_financial_features()   → affordability + feature guards
    _load_product_data()            → product row + reviews from DB
    _score_ml_model()               → ML prediction (AUTHORITY)
    _generate_explanation()         → LLM response + guardrails

    run_inference()                 → orchestrates the above stages

Usage:
    from deployment.api.inference import run_inference
    from deployment.api.model_loader import model_manager

    response = run_inference(request, model_manager)
"""

from __future__ import annotations

import logging
import math
import time
from dataclasses import dataclass, field
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
# Pipeline Stage Result Types
# ---------------------------------------------------------------------------

@dataclass
class ProductResolution:
    """Output of the product resolution stage."""
    product_name: str | None = None
    product_price: float | None = None
    product_id: str | None = None
    evaluation_mode: str = "catalog"
    user_context: str | None = None
    parsed_intent: Any = None


@dataclass
class FinancialResult:
    """Output of the financial feature computation stage."""
    financial_dict: dict = field(default_factory=dict)
    features_view: FinancialFeaturesView | None = None
    affordability_unreliable: bool = False


@dataclass
class ProductData:
    """Product row and review data loaded from DB for ML features."""
    product_row: Any = None
    reviews_df: Any = None
    product_feats: Any = None
    review_feats: Any = None


@dataclass
class MLScore:
    """Output of the ML model scoring stage."""
    confidence: float | None = None
    predicted_label: str | None = None
    unavailable_reason: str | None = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _opt_float_financial(val) -> float | None:
    if val is None:
        return None
    try:
        x = float(val)
        if math.isnan(x):
            return None
        return x
    except (TypeError, ValueError):
        return None


def _financial_features_view(
    user_profile: dict, financial_dict: dict,
) -> FinancialFeaturesView:
    """Snapshot of financial features for API / technical UI."""
    return FinancialFeaturesView(
        discretionary_income=float(
            user_profile.get("discretionary_income", 0) or 0,
        ),
        debt_to_income_ratio=float(
            financial_dict.get("debt_to_income_ratio", 0) or 0,
        ),
        saving_to_income_ratio=float(
            user_profile.get("saving_to_income_ratio", 0) or 0,
        ),
        monthly_expense_burden_ratio=float(
            financial_dict.get("monthly_expense_burden_ratio", 0) or 0,
        ),
        emergency_fund_months=float(
            financial_dict.get("emergency_fund_months", 0) or 0,
        ),
        affordability_score=float(
            financial_dict.get("affordability_score", 0) or 0,
        ),
        price_to_income_ratio=_opt_float_financial(
            financial_dict.get("price_to_income_ratio"),
        ),
        residual_utility_score=_opt_float_financial(
            financial_dict.get("residual_utility_score"),
        ),
        savings_to_price_ratio=_opt_float_financial(
            financial_dict.get("savings_to_price_ratio"),
        ),
        net_worth_indicator=_opt_float_financial(
            financial_dict.get("net_worth_indicator"),
        ),
        credit_risk_indicator=_opt_float_financial(
            financial_dict.get("credit_risk_indicator"),
        ),
    )


# ---------------------------------------------------------------------------
# Stage 1: Load User Financial Profile
# ---------------------------------------------------------------------------

def _load_user_financial_profile(
    user_id: str, db_engine,
) -> dict | None:
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
        logger.error(
            "Failed to load financial profile for user_id=%s: %s",
            user_id, e,
        )
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

def _resolve_product(
    request: PredictRequest, manager,
) -> ProductResolution | PredictResponse:
    """Parse intent and resolve the product.

    Returns a ProductResolution on success, or a PredictResponse
    early-exit on failure (out of scope, not found, etc.).
    """
    from llm.product_resolver import resolve_product, resolve_product_by_id
    from llm.intent_parser import parse_user_input
    from llm.prompts.response_templates import (
        OUT_OF_SCOPE_RESPONSE,
        PRODUCT_NOT_FOUND_RESPONSE,
    )

    if request.product_id:
        match = resolve_product_by_id(
            request.product_id, manager.db_engine,
        )
        if match:
            logger.info(
                "Direct product lookup: %s → %s ($%.2f)",
                request.product_id, match.product_name, match.price,
            )
            return ProductResolution(
                product_name=match.product_name,
                product_price=match.price,
                product_id=match.product_id,
            )
        return PredictResponse(
            recommendation="YELLOW", confidence=None,
            explanation=PRODUCT_NOT_FOUND_RESPONSE,
            evaluation_mode="none",
        )

    # Natural language mode
    parsed = parse_user_input(request.user_query, manager.llm_provider)
    logger.info(
        "Intent parsed: %s, product_ref=%s, price_hint=%s",
        parsed.intent, parsed.product_reference,
        getattr(parsed, "price_hint", None),
    )

    if parsed.intent == "out_of_scope":
        return PredictResponse(
            recommendation="YELLOW", confidence=None,
            explanation=OUT_OF_SCOPE_RESPONSE,
            evaluation_mode="none",
        )

    price_hint = getattr(parsed, "price_hint", None)
    user_ctx = getattr(parsed, "user_context", None)

    if parsed.product_reference:
        match = resolve_product(
            parsed.product_reference, manager.db_engine,
            price_hint=price_hint,
        )

    if match:
        logger.info(
            "Product resolved: '%s' → '%s' ($%.2f)",
            parsed.product_reference, match.product_name, match.price,
        )
        return ProductResolution(
            product_name=match.product_name,
            product_price=match.price,
            product_id=match.product_id,
            user_context=user_ctx,
            parsed_intent=parsed,
        )

    if price_hint is not None and parsed.product_reference:
        logger.info(
            "Hypothetical evaluation (stated price): '%s' at $%.2f",
            parsed.product_reference, float(price_hint),
        )
        return ProductResolution(
            product_name=parsed.product_reference.strip(),
            product_price=float(price_hint),
            evaluation_mode="hypothetical",
            user_context=user_ctx,
            parsed_intent=parsed,
        )

    return PredictResponse(
        recommendation="YELLOW", confidence=None,
        explanation=PRODUCT_NOT_FOUND_RESPONSE,
        evaluation_mode="none",
    )


# ---------------------------------------------------------------------------
# Stage 3: Compute Financial Features
# ---------------------------------------------------------------------------

_FEATURE_DEFAULTS = {
    "price_to_income_ratio": 1.0,
    "savings_to_price_ratio": 0.0,
    "net_worth_indicator": 0.0,
    "credit_risk_indicator": 0.0,
}


def _compute_financial_features(
    user_profile: dict, product_price: float,
) -> FinancialResult:
    """Compute affordability features and guard None values."""
    from features.financial_features import compute_affordability

    affordability = compute_affordability(
        user_financial_profile=user_profile,
        product_price=product_price,
    )
    fd = affordability.to_dict()
    unreliable = bool(affordability.affordability_score_unreliable)
    fd.pop("affordability_score_unreliable", None)

    # Merge DB-level ratios
    for key in (
        "debt_to_income_ratio", "monthly_expense_burden_ratio",
        "emergency_fund_months", "saving_to_income_ratio",
    ):
        fd[key] = float(user_profile.get(key, 0) or 0)

    # Guard None values with conservative defaults
    for key, default in _FEATURE_DEFAULTS.items():
        if fd.get(key) is None:
            fd[key] = default
            logger.warning(
                "%s is None — defaulting to %s", key, default,
            )

    logger.info(
        "Affordability computed: score=%.2f, SPR=%.2f, unreliable=%s",
        fd.get("affordability_score", 0),
        fd.get("savings_to_price_ratio", 0),
        unreliable,
    )

    features_view = _financial_features_view(user_profile, fd)
    return FinancialResult(
        financial_dict=fd,
        features_view=features_view,
        affordability_unreliable=unreliable,
    )


# ---------------------------------------------------------------------------
# Stage 4: Load Product Data (for ML features)
# ---------------------------------------------------------------------------

def _load_product_data(
    product: ProductResolution, manager,
) -> ProductData:
    """Load product row + reviews and compute features for ML model input."""
    from features.product_features import compute_product_features
    from features.review_features import compute_review_features

    result = ProductData()

    if not product.product_id:
        return result

    # Load product row
    sql = text("""
        SELECT product_id, product_name, price, average_rating,
               rating_number, rating_variance, category
        FROM products
        WHERE product_id = :product_id
    """)
    try:
        with manager.db_engine.connect() as conn:
            row = conn.execute(sql, {"product_id": product.product_id}).fetchone()
    except Exception as e:
        logger.error("Failed to load product row for %s: %s", product.product_id, e)
        return result

    if row is None:
        return result

    columns = [
        "product_id", "product_name", "price", "average_rating",
        "rating_number", "rating_variance", "category",
    ]
    result.product_row = pd.Series(dict(zip(columns, row)))

    # Load reviews
    review_sql = text("""
        SELECT user_id, product_id, rating, review_title, review_text,
               verified_purchase, helpful_vote
        FROM reviews
        WHERE product_id = :product_id
    """)
    try:
        with manager.db_engine.connect() as conn:
            review_rows = conn.execute(
                review_sql, {"product_id": product.product_id},
            ).fetchall()
    except Exception as e:
        logger.error("Failed to load reviews for %s: %s", product.product_id, e)
        review_rows = []

    if review_rows:
        review_cols = [
            "user_id", "product_id", "rating", "review_title",
            "review_text", "verified_purchase", "helpful_vote",
        ]
        result.reviews_df = pd.DataFrame(review_rows, columns=review_cols)
    else:
        result.reviews_df = pd.DataFrame()

    # Compute product and review features
    result.product_feats = compute_product_features(
        result.product_row, manager.category_stats, manager.max_rating_number,
    )
    result.review_feats = compute_review_features(result.reviews_df)

    logger.info(
        "Product data loaded: product_id=%s, reviews=%d",
        product.product_id, len(result.reviews_df),
    )
    return result


# ---------------------------------------------------------------------------
# Stage 5: ML Model Scoring (AUTHORITY)
# ---------------------------------------------------------------------------

def _build_ml_feature_row(
    user_profile: dict,
    financial_dict: dict,
    product_price: float,
    product_data: ProductData,
) -> dict:
    """Assemble the raw feature row matching the training schema."""
    pr = product_data.product_row
    pf = product_data.product_feats
    rf = product_data.review_feats
    has_product = pr is not None

    return {
        # Raw user fields from DB
        "monthly_income": float(user_profile.get("monthly_income", 0) or 0),
        "monthly_expenses": float(user_profile.get("monthly_expenses", 0) or 0),
        "savings_balance": float(user_profile.get("savings_balance", 0) or 0),
        "has_loan": int(bool(user_profile.get("has_loan", 0))),
        "loan_amount": float(user_profile.get("loan_amount", 0) or 0),
        "monthly_emi": float(user_profile.get("monthly_emi", 0) or 0),
        "loan_interest_rate": float(user_profile.get("loan_interest_rate", 0) or 0),
        "loan_term_months": float(user_profile.get("loan_term_months", 0) or 0),
        "credit_score": float(user_profile.get("credit_score", 0) or 0),
        "employment_status": str(user_profile.get("employment_status", "unknown") or "unknown"),
        "region": str(user_profile.get("region", "unknown") or "unknown"),
        # DB-precomputed financial ratios
        "liquid_savings": float(user_profile.get("liquid_savings", 0) or 0),
        "discretionary_income": float(user_profile.get("discretionary_income", 0) or 0),
        "debt_to_income_ratio": float(financial_dict.get("debt_to_income_ratio", 0)),
        "saving_to_income_ratio": float(user_profile.get("saving_to_income_ratio", 0) or 0),
        "monthly_expense_burden_ratio": float(financial_dict.get("monthly_expense_burden_ratio", 0)),
        "emergency_fund_months": float(financial_dict.get("emergency_fund_months", 0)),
        # Affordability computed features
        "affordability_score": float(financial_dict.get("affordability_score", 0)),
        "price_to_income_ratio": float(financial_dict.get("price_to_income_ratio", 0)),
        "residual_utility_score": float(financial_dict.get("residual_utility_score") or 0),
        "savings_to_price_ratio": float(financial_dict.get("savings_to_price_ratio", 0)),
        "net_worth_indicator": float(financial_dict.get("net_worth_indicator", 0)),
        "credit_risk_indicator": float(financial_dict.get("credit_risk_indicator", 0) or 0),
        # Product raw fields
        "product_price": float(product_price or 0),
        "average_rating": float(pr["average_rating"] if has_product else 0),
        "rating_number": float(pr["rating_number"] if has_product else 0),
        "rating_variance": float(pr["rating_variance"] if has_product else 0),
        "category": str(pr["category"] if has_product else "unknown"),
        # Product computed features
        "value_density": float(pf.value_density if has_product else 0),
        "review_confidence": float(pf.review_confidence if has_product else 0),
        "rating_polarization": float(pf.rating_polarization if has_product else 0),
        "quality_risk_score": float(pf.quality_risk_score if has_product else 0),
        "cold_start_flag": int(pf.cold_start_flag if has_product else 0),
        "price_category_rank": float(pf.price_category_rank if has_product else 0),
        "category_rating_deviation": float(pf.category_rating_deviation if has_product else 0),
        # Review computed features
        "verified_purchase_ratio": float(rf.verified_purchase_ratio if has_product else 0),
        "helpful_concentration": float(rf.helpful_concentration if has_product else 0),
        "sentiment_spread": float(rf.sentiment_spread if has_product else 0),
        "review_depth_score": float(rf.review_depth_score if has_product else 0),
        "reviewer_diversity": float(rf.reviewer_diversity if has_product else 0),
        "extreme_rating_ratio": float(rf.extreme_rating_ratio if has_product else 0),
        # Legacy training feature — always 0 since engines no longer run at inference
        "downgraded": 0,
    }


def _score_ml_model(
    user_profile: dict,
    fin: FinancialResult,
    product: ProductResolution,
    product_data: ProductData,
    manager,
) -> MLScore:
    """Score using the ML model. Returns the authoritative prediction."""
    if manager.model is None:
        return MLScore(unavailable_reason="no_model")
    if manager.feature_pipeline is None:
        return MLScore(unavailable_reason="no_pipeline")

    try:
        import numpy as np

        raw_row = _build_ml_feature_row(
            user_profile, fin.financial_dict, product.product_price, product_data,
        )
        feature_df = pd.DataFrame([raw_row])
        X = manager.feature_pipeline.transform(feature_df)

        # Fix MLflow schema type expectations
        if isinstance(X, pd.DataFrame):
            X = X.copy()
            if "credit_score" in X.columns:
                cs = np.asarray(X["credit_score"].values, dtype=float).ravel()
                if (
                    cs.size
                    and np.all(np.isfinite(cs))
                    and np.allclose(cs, np.round(cs), atol=1e-5)
                ):
                    X["credit_score"] = np.round(cs).astype(np.int64)
                else:
                    X["credit_score"] = np.int64(
                        int(user_profile.get("credit_score") or 0),
                    )
            if "downgraded" in X.columns:
                X["downgraded"] = X["downgraded"].astype(np.int64)

        label_ml, conf = manager.predict(X)

        result = MLScore()
        if label_ml is not None:
            result.predicted_label = str(label_ml).strip().upper()
        if conf is None:
            result.unavailable_reason = "scoring_error"
            logger.warning("ML predict returned no confidence (label=%s)", label_ml)
        else:
            result.confidence = float(conf)
            logger.info("ML prediction: %s (confidence=%.4f)", result.predicted_label, result.confidence)
        return result

    except Exception as e:
        logger.warning("ML model scoring failed: %s", e, exc_info=True)
        return MLScore(unavailable_reason="scoring_error")


# ---------------------------------------------------------------------------
# Stage 6: LLM Response Generation + Guardrails
# ---------------------------------------------------------------------------

def _generate_explanation(
    product: ProductResolution,
    fin: FinancialResult,
    ml: MLScore,
    user_profile: dict,
    manager,
) -> str:
    """Generate LLM explanation and run guardrails."""
    from llm.response_generator import (
        RecommendationContext,
        generate_response,
        _generate_from_template,
    )
    from llm.guardrails import check_response

    fd = fin.financial_dict
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
        affordability_score=float(fd.get("affordability_score", 0.0)),
        affordability_score_unreliable=fin.affordability_unreliable,
        savings_to_price_ratio=float(fd.get("savings_to_price_ratio", 0.0)),
        emergency_fund_months=float(fd.get("emergency_fund_months", 0.0)),
        debt_to_income_ratio=float(fd.get("debt_to_income_ratio", 0.0)),
        monthly_income=float(user_profile.get("monthly_income", 0) or 0),
        monthly_expense_burden_ratio=float(fd.get("monthly_expense_burden_ratio", 0.0)),
        price_to_income_ratio=float(fd.get("price_to_income_ratio", 0.0)),
        credit_score=user_profile.get("credit_score"),
        review_snippets=[],
    )

    response_text = generate_response(context, manager.llm_provider)
    guardrail = check_response(response_text, recommendation_color, context)

    if not guardrail.passed:
        logger.warning(
            "Guardrail violations: %s — falling back to template",
            guardrail.violations,
        )
        response_text = _generate_from_template(context)

    return response_text


# ---------------------------------------------------------------------------
# Response Assembly
# ---------------------------------------------------------------------------

def _build_response(
    product: ProductResolution,
    fin: FinancialResult,
    ml: MLScore,
    explanation: str,
    start_time: float,
) -> PredictResponse:
    """Assemble the final PredictResponse from all pipeline stages."""
    fd = fin.financial_dict
    recommendation = ml.predicted_label or "YELLOW"

    elapsed = time.time() - start_time
    logger.info(
        "Inference complete: recommendation=%s, confidence=%s, latency=%.3fs",
        recommendation,
        f"{ml.confidence:.2f}" if ml.confidence is not None else "n/a",
        elapsed,
    )

    return PredictResponse(
        recommendation=recommendation,
        confidence=ml.confidence,
        ml_unavailable_reason=ml.unavailable_reason,
        explanation=explanation,
        product_name=product.product_name,
        product_price=product.product_price,
        evaluation_mode=product.evaluation_mode,
        affordability_score=float(fd.get("affordability_score", 0.0)),
        affordability_score_unreliable=fin.affordability_unreliable,
        emergency_fund_months=float(fd.get("emergency_fund_months", 0) or 0),
        debt_to_income_ratio=float(fd.get("debt_to_income_ratio", 0) or 0),
        financial_features=fin.features_view,
        ml_predicted_label=ml.predicted_label,
        ml_model_name=APIConfig.ML_MODEL_DISPLAY_NAME,
    )


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------

def run_inference(
    request: PredictRequest, manager,
) -> PredictResponse:
    """Execute the full inference pipeline for a /predict request.

    The ML model is the authority for GREEN/YELLOW/RED decisions.
    """
    from llm.prompts.response_templates import USER_NOT_FOUND_RESPONSE

    start_time = time.time()

    # Stage 1: Load user financial profile
    if manager.db_engine is None:
        return PredictResponse(
            recommendation="YELLOW", confidence=None,
            explanation="Service is temporarily unavailable.",
            evaluation_mode="none",
        )

    user_profile = _load_user_financial_profile(
        request.user_id, manager.db_engine,
    )
    if user_profile is None:
        logger.warning("User not found: %s", request.user_id)
        return PredictResponse(
            recommendation="YELLOW", confidence=None,
            explanation=USER_NOT_FOUND_RESPONSE,
            evaluation_mode="none",
        )
    logger.info("User profile loaded for: %s", request.user_id)

    # Stage 2: Resolve product
    resolution = _resolve_product(request, manager)
    if isinstance(resolution, PredictResponse):
        return resolution

    # Stage 3: Compute financial features
    fin = _compute_financial_features(
        user_profile, resolution.product_price,
    )

    # Stage 4: Load product data (for ML features)
    product_data = _load_product_data(resolution, manager)

    # Stage 5: ML model scoring (AUTHORITY)
    ml = _score_ml_model(user_profile, fin, resolution, product_data, manager)

    # Stage 6: LLM explanation + guardrails
    explanation = _generate_explanation(
        resolution, fin, ml, user_profile, manager,
    )

    # Assemble response
    return _build_response(
        resolution, fin, ml, explanation, start_time,
    )

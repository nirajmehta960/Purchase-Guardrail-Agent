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
import math
import time

import pandas as pd
from sqlalchemy import text

from deployment.api.config import APIConfig
from deployment.api.schemas import FinancialFeaturesView, PredictRequest, PredictResponse

logger = logging.getLogger(__name__)


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


def _financial_features_view(user_profile: dict, financial_dict: dict) -> FinancialFeaturesView:
    """Snapshot of Layer 1 features for API / technical UI."""
    return FinancialFeaturesView(
        discretionary_income=float(user_profile.get("discretionary_income", 0) or 0),
        debt_to_income_ratio=float(financial_dict.get("debt_to_income_ratio", 0) or 0),
        saving_to_income_ratio=float(user_profile.get("saving_to_income_ratio", 0) or 0),
        monthly_expense_burden_ratio=float(financial_dict.get("monthly_expense_burden_ratio", 0) or 0),
        emergency_fund_months=float(financial_dict.get("emergency_fund_months", 0) or 0),
        affordability_score=float(financial_dict.get("affordability_score", 0) or 0),
        price_to_income_ratio=_opt_float_financial(financial_dict.get("price_to_income_ratio")),
        residual_utility_score=_opt_float_financial(financial_dict.get("residual_utility_score")),
        savings_to_price_ratio=_opt_float_financial(financial_dict.get("savings_to_price_ratio")),
        net_worth_indicator=_opt_float_financial(financial_dict.get("net_worth_indicator")),
        credit_risk_indicator=_opt_float_financial(financial_dict.get("credit_risk_indicator")),
    )


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
    from deployment.api.quality_signals import build_quality_signal_views

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
            confidence=None,
            explanation="Service is temporarily unavailable. Please try again later.",
            guardrail_passed=True,
            evaluation_mode="none",
        )

    user_profile = _load_user_financial_profile(request.user_id, manager.db_engine)
    if user_profile is None:
        logger.warning("User not found: %s", request.user_id)
        return PredictResponse(
            recommendation="YELLOW",
            confidence=None,
            explanation=USER_NOT_FOUND_RESPONSE,
            guardrail_passed=True,
            evaluation_mode="none",
        )

    logger.info("User profile loaded for: %s", request.user_id)

    # ------------------------------------------------------------------
    # Step 2: Parse intent / Resolve product
    # ------------------------------------------------------------------
    product_match = None
    product_name = None
    product_price = None
    product_id = None
    evaluation_mode = "catalog"

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
                confidence=None,
                explanation=PRODUCT_NOT_FOUND_RESPONSE,
                guardrail_passed=True,
                evaluation_mode="none",
            )
    else:
        # Natural language mode — parse intent first
        parsed = parse_user_input(request.user_query, manager.llm_provider)
        logger.info(
            "Intent parsed: %s, product_ref=%s, price_hint=%s",
            parsed.intent,
            parsed.product_reference,
            getattr(parsed, "price_hint", None),
        )

        if parsed.intent == "out_of_scope":
            return PredictResponse(
                recommendation="YELLOW",
                confidence=None,
                explanation=OUT_OF_SCOPE_RESPONSE,
                guardrail_passed=True,
                evaluation_mode="none",
            )

        price_hint = getattr(parsed, "price_hint", None)
        if parsed.product_reference:
            product_match = resolve_product(
                parsed.product_reference,
                manager.db_engine,
                price_hint=price_hint,
            )

        if product_match:
            product_name = product_match.product_name
            product_price = product_match.price
            product_id = product_match.product_id
            user_context_val = getattr(parsed, "user_context", None)
            logger.info("Product resolved: '%s' → '%s' ($%.2f)",
                        parsed.product_reference, product_name, product_price)
        elif price_hint is not None and parsed.product_reference:
            # No catalog row (or price/name mismatch) — evaluate at the user-stated price
            evaluation_mode = "hypothetical"
            product_name = parsed.product_reference.strip()
            product_price = float(price_hint)
            product_id = None
            user_context_val = getattr(parsed, "user_context", None)
            logger.info(
                "Hypothetical evaluation (stated price): '%s' at $%.2f",
                product_name,
                product_price,
            )
        else:
            return PredictResponse(
                recommendation="YELLOW",
                confidence=None,
                explanation=PRODUCT_NOT_FOUND_RESPONSE,
                guardrail_passed=True,
                evaluation_mode="none",
            )

    # ------------------------------------------------------------------
    # Step 3: Compute affordability features (6 financial features)
    # ------------------------------------------------------------------
    affordability = compute_affordability(
        user_financial_profile=user_profile,
        product_price=product_price,
    )
    financial_dict = affordability.to_dict()
    affordability_unreliable = bool(affordability.affordability_score_unreliable)
    financial_dict.pop("affordability_score_unreliable", None)

    # Merge DB-level financial features into the dict for the engine
    financial_dict["debt_to_income_ratio"] = float(user_profile.get("debt_to_income_ratio", 0) or 0)
    financial_dict["monthly_expense_burden_ratio"] = float(user_profile.get("monthly_expense_burden_ratio", 0) or 0)
    financial_dict["emergency_fund_months"] = float(user_profile.get("emergency_fund_months", 0) or 0)
    financial_dict["saving_to_income_ratio"] = float(user_profile.get("saving_to_income_ratio", 0) or 0)

    logger.info(
        "Affordability computed: score=%.2f, SPR=%.2f, AFS_unreliable=%s",
        financial_dict.get("affordability_score", 0),
        financial_dict.get("savings_to_price_ratio", 0),
        affordability_unreliable,
    )
    logger.info(
        "Layer1 snapshot (align with dashboard): credit_score=%s → CRI=%.4f, "
        "MEB=%.4f, DTI=%.4f, EFM=%.2f, STIR_db=%.4f, PIR=%s, SPR=%s, NWI=%s",
        user_profile.get("credit_score"),
        float(financial_dict.get("credit_risk_indicator") or 0),
        float(financial_dict.get("monthly_expense_burden_ratio") or 0),
        float(financial_dict.get("debt_to_income_ratio") or 0),
        float(financial_dict.get("emergency_fund_months") or 0),
        float(financial_dict.get("saving_to_income_ratio") or 0),
        financial_dict.get("price_to_income_ratio"),
        financial_dict.get("savings_to_price_ratio"),
        financial_dict.get("net_worth_indicator"),
    )

    financial_features_view = _financial_features_view(user_profile, financial_dict)

    # ------------------------------------------------------------------
    # Step 4: Run Deterministic Engine (Layer 1)
    # ------------------------------------------------------------------
    engine = DecisionEngine()
    decision = engine.decide(financial_dict, {"price": product_price})

    l1_color = decision.decision_category
    triggered_rules = list(decision.triggered_rules)
    if evaluation_mode == "hypothetical":
        triggered_rules.insert(0, "evaluation:hypothetical_stated_price")
    logger.info("Layer 1 decision: %s, rules=%s", l1_color, triggered_rules)

    # ------------------------------------------------------------------
    # Step 5: Run Downgrade Engine (Layer 2)
    # ------------------------------------------------------------------
    was_downgraded = False
    final_color = l1_color
    product_row = None
    product_feats = None
    review_feats = None
    layer2_evaluated = False
    review_count = 0
    layer2_product_triggers: list[str] = []
    layer2_review_triggers: list[str] = []
    product_signals = None
    review_signals = None

    if product_id:
        product_row = _load_product_row(product_id, manager.db_engine)
        reviews_df = _load_product_reviews(product_id, manager.db_engine)
        review_count = len(reviews_df)

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
            layer2_evaluated = True
            layer2_product_triggers = list(downgrade_result.product_triggers)
            layer2_review_triggers = list(downgrade_result.review_triggers)

            if was_downgraded:
                triggered_rules.extend(downgrade_result.product_triggers)
                triggered_rules.extend(downgrade_result.review_triggers)
                logger.info("Layer 2 downgrade: %s → %s", l1_color, final_color)
            else:
                logger.info(
                    "Layer 2 evaluated: L1=%s → final=%s, reviews=%d, PR triggers=%s, RR triggers=%s",
                    l1_color,
                    final_color,
                    review_count,
                    layer2_product_triggers,
                    layer2_review_triggers,
                )

            product_signals, review_signals = build_quality_signal_views(
                product_row,
                product_feats,
                review_feats,
            )

    # ------------------------------------------------------------------
    # Step 6: Run ML Model → confidence score
    # ------------------------------------------------------------------
    # The ML model provides a confidence score but CANNOT override the
    # deterministic engine's color. It's informational only.
    model_confidence: float | None = None
    ml_unavailable_reason: str | None = None
    ml_predicted_label: str | None = None
    if manager.model is None:
        ml_unavailable_reason = "no_model"
    elif manager.feature_pipeline is None:
        ml_unavailable_reason = "no_pipeline"
    else:
        try:
            import numpy as np
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
                # Affordability computed features (must match training schema — includes residual_utility_score)
                "affordability_score":     float(financial_dict.get("affordability_score", 0)),
                "price_to_income_ratio":   float(financial_dict.get("price_to_income_ratio", 0)),
                "residual_utility_score":  float(financial_dict.get("residual_utility_score") or 0),
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
                # Training schema + MLflow signature — see labeling_pipeline / run_pipeline
                "downgraded":              int(was_downgraded),
            }
            feature_df = _pd.DataFrame([raw_row])
            X_preprocessed = manager.feature_pipeline.transform(feature_df)
            # MLflow pyfunc validates inputs against the saved signature. credit_score and
            # downgraded must be int64 (long); StandardScaler / pandas leave them as float64.
            if isinstance(X_preprocessed, _pd.DataFrame):
                X_preprocessed = X_preprocessed.copy()
                if "credit_score" in X_preprocessed.columns:
                    cs = np.asarray(X_preprocessed["credit_score"].values, dtype=float).ravel()
                    if cs.size and np.all(np.isfinite(cs)) and np.allclose(
                        cs, np.round(cs), atol=1e-5
                    ):
                        X_preprocessed["credit_score"] = np.round(cs).astype(np.int64)
                    else:
                        X_preprocessed["credit_score"] = np.int64(
                            int(user_profile.get("credit_score") or 0)
                        )
                if "downgraded" in X_preprocessed.columns:
                    X_preprocessed["downgraded"] = (
                        X_preprocessed["downgraded"].astype(np.int64)
                    )
            label_ml, conf = manager.predict(X_preprocessed)
            if label_ml is not None:
                ml_predicted_label = str(label_ml).strip().upper()
            if conf is None:
                model_confidence = None
                ml_unavailable_reason = "scoring_error"
                logger.warning("ML predict returned no confidence (label=%s)", label_ml)
            else:
                model_confidence = float(conf)
                logger.info("ML confidence: %.4f", model_confidence)
        except Exception as e:
            logger.warning("ML model scoring failed: %s", e, exc_info=True)
            model_confidence = None
            ml_unavailable_reason = "scoring_error"

    # ------------------------------------------------------------------
    # Step 7: Run LLM response generation + guardrails
    # ------------------------------------------------------------------
    _mc = model_confidence if model_confidence is not None else 0.0
    context = RecommendationContext(
        product_name=product_name or "the product",
        product_price=product_price or 0.0,
        recommendation_color=final_color,
        original_color=l1_color if was_downgraded else final_color,
        was_downgraded=was_downgraded,
        triggered_rules=triggered_rules,
        confidence_scores={final_color: _mc},
        ml_confidence=model_confidence,
        ml_unavailable_reason=ml_unavailable_reason,
        hypothetical_purchase=(evaluation_mode == "hypothetical"),
        user_context=user_context_val,
        affordability_score=float(financial_dict.get("affordability_score", 0.0)),
        affordability_score_unreliable=affordability_unreliable,
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
        "Inference complete: color=%s, ml_confidence=%s, downgraded=%s, "
        "guardrail_passed=%s, latency=%.3fs",
        final_color,
        f"{model_confidence:.2f}" if model_confidence is not None else "n/a",
        was_downgraded,
        guardrail_result.passed,
        elapsed,
    )

    return PredictResponse(
        recommendation=final_color,
        confidence=model_confidence,
        ml_unavailable_reason=ml_unavailable_reason,
        explanation=response_text,
        product_name=product_name,
        product_price=product_price,
        triggered_rules=triggered_rules,
        was_downgraded=was_downgraded,
        guardrail_passed=guardrail_result.passed,
        evaluation_mode=evaluation_mode,
        layer2_evaluated=layer2_evaluated,
        review_count=review_count,
        layer2_product_triggers=layer2_product_triggers,
        layer2_review_triggers=layer2_review_triggers,
        product_signals=product_signals,
        review_signals=review_signals,
        affordability_score=float(financial_dict.get("affordability_score", 0.0)),
        affordability_score_unreliable=affordability_unreliable,
        emergency_fund_months=float(financial_dict.get("emergency_fund_months", 0) or 0),
        debt_to_income_ratio=float(financial_dict.get("debt_to_income_ratio", 0) or 0),
        financial_features=financial_features_view,
        layer1_recommendation=l1_color,
        ml_predicted_label=ml_predicted_label,
        ml_model_name=APIConfig.ML_MODEL_DISPLAY_NAME,
    )

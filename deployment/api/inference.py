"""
Inference Orchestrator — Full prediction pipeline for the /predict endpoint.

Decomposed into discrete pipeline stages, each independently testable:

    _load_user_financial_profile()  → DB lookup
    _resolve_product()              → intent parsing / product resolution
    _compute_financial_features()   → affordability + feature guards
    _run_layer1_engine()            → deterministic GREEN/YELLOW/RED
    _run_layer2_engine()            → downgrade via product/review signals
    _score_ml_model()               → XGBoost confidence (informational)
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
import re
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
class L1Result:
    """Output of the Layer 1 deterministic engine."""
    color: str = "YELLOW"
    triggered_rules: list[str] = field(default_factory=list)


@dataclass
class L2Result:
    """Output of the Layer 2 downgrade engine."""
    final_color: str = "YELLOW"
    was_downgraded: bool = False
    layer2_evaluated: bool = False
    review_count: int = 0
    product_triggers: list[str] = field(default_factory=list)
    review_triggers: list[str] = field(default_factory=list)
    product_signals: Any = None
    review_signals: Any = None
    product_row: Any = None
    product_feats: Any = None
    review_feats: Any = None
    review_snippets: list[str] = field(default_factory=list)


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
    """Snapshot of Layer 1 features for API / technical UI."""
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


def _clean_review_text(raw: str) -> str:
    """Strip Amazon-specific noise from review text."""
    raw = re.sub(r"\[\[(?:VIDEO|IMAGE)ID:[^\]]*\]\]", "", raw)
    raw = re.sub(r"<br\s*/?>", " ", raw, flags=re.IGNORECASE)
    raw = re.sub(r"<[^>]+>", "", raw)
    raw = re.sub(r"\s+", " ", raw)
    return raw.strip()


def _select_review_snippets(
    reviews_df: pd.DataFrame,
    n_positive: int = 3,
    n_critical: int = 2,
) -> list:
    """Return formatted review snippets for LLM context."""
    if reviews_df.empty:
        return []

    df = reviews_df.copy()
    df["helpful_vote"] = pd.to_numeric(
        df.get("helpful_vote", 0), errors="coerce",
    ).fillna(0)
    df["rating"] = pd.to_numeric(
        df.get("rating", 3), errors="coerce",
    ).fillna(3)

    positive = df[df["rating"] >= 4].nlargest(n_positive, "helpful_vote")
    critical = df[df["rating"] <= 2].nlargest(n_critical, "helpful_vote")
    selected = pd.concat([positive, critical])

    snippets = []
    for _, row in selected.iterrows():
        rating = int(row.get("rating", 0))
        title = _clean_review_text(str(row.get("review_title", "") or ""))
        body_text = _clean_review_text(
            str(row.get("review_text", "") or ""),
        )

        if title and body_text:
            body = f"{title}: {body_text}"
        elif body_text:
            body = body_text
        elif title:
            body = title
        else:
            continue

        if len(body) > 160:
            body = body[:157] + "..."
        snippets.append(f"[{rating}\u2605] {body}")

    return snippets


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
            guardrail_passed=True, evaluation_mode="none",
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
            guardrail_passed=True, evaluation_mode="none",
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
        guardrail_passed=True, evaluation_mode="none",
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
                "%s is None — defaulting to %s for engine", key, default,
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
# Stage 4: Layer 1 — Deterministic Engine
# ---------------------------------------------------------------------------

def _run_layer1_engine(
    financial_dict: dict, product: ProductResolution,
) -> L1Result:
    """Run the deterministic financial engine."""
    from deterministic_engine.financial_engine import DecisionEngine

    engine = DecisionEngine()
    decision = engine.decide(
        financial_dict, {"price": product.product_price},
    )

    rules = list(decision.triggered_rules)
    if product.evaluation_mode == "hypothetical":
        rules.insert(0, "evaluation:hypothetical_stated_price")

    logger.info("Layer 1 decision: %s, rules=%s", decision.decision_category, rules)
    return L1Result(color=decision.decision_category, triggered_rules=rules)


# ---------------------------------------------------------------------------
# Stage 5: Layer 2 — Downgrade Engine
# ---------------------------------------------------------------------------

def _load_product_reviews(
    product_id: str, db_engine,
) -> pd.DataFrame:
    """Load all reviews for a product."""
    sql = text("""
        SELECT user_id, product_id, rating, review_title, review_text,
               verified_purchase, helpful_vote
        FROM reviews
        WHERE product_id = :product_id
    """)
    try:
        with db_engine.connect() as conn:
            rows = conn.execute(
                sql, {"product_id": product_id},
            ).fetchall()
    except Exception as e:
        logger.error(
            "Failed to load reviews for product_id=%s: %s",
            product_id, e,
        )
        return pd.DataFrame()

    if not rows:
        return pd.DataFrame()

    columns = [
        "user_id", "product_id", "rating", "review_title",
        "review_text", "verified_purchase", "helpful_vote",
    ]
    return pd.DataFrame(rows, columns=columns)


def _load_product_row(
    product_id: str, db_engine,
) -> pd.Series | None:
    """Load a single product row for feature computation."""
    sql = text("""
        SELECT product_id, product_name, price, average_rating,
               rating_number, rating_variance, category
        FROM products
        WHERE product_id = :product_id
    """)
    try:
        with db_engine.connect() as conn:
            row = conn.execute(
                sql, {"product_id": product_id},
            ).fetchone()
    except Exception as e:
        logger.error(
            "Failed to load product row for product_id=%s: %s",
            product_id, e,
        )
        return None

    if row is None:
        return None

    columns = [
        "product_id", "product_name", "price", "average_rating",
        "rating_number", "rating_variance", "category",
    ]
    return pd.Series(dict(zip(columns, row)))


def _run_layer2_engine(
    l1_color: str,
    product: ProductResolution,
    manager,
) -> L2Result:
    """Run Layer 2 downgrade engine with product/review signals."""
    from features.product_features import compute_product_features
    from features.review_features import compute_review_features
    from deterministic_engine.downgrade_engine import DowngradeEngine
    from deployment.api.quality_signals import build_quality_signal_views

    result = L2Result(final_color=l1_color)

    if not product.product_id:
        return result

    product_row = _load_product_row(product.product_id, manager.db_engine)
    reviews_df = _load_product_reviews(
        product.product_id, manager.db_engine,
    )
    result.review_count = len(reviews_df)
    result.review_snippets = _select_review_snippets(reviews_df)

    if product_row is None:
        return result

    product_feats = compute_product_features(
        product_row, manager.category_stats, manager.max_rating_number,
    )
    review_feats = compute_review_features(reviews_df)

    downgrade = DowngradeEngine()
    dr = downgrade.evaluate(
        financial_label=l1_color,
        product_features=product_feats,
        review_features=review_feats,
    )

    result.final_color = dr.final_label
    result.was_downgraded = dr.was_downgraded
    result.layer2_evaluated = True
    result.product_triggers = list(dr.product_triggers)
    result.review_triggers = list(dr.review_triggers)
    result.product_row = product_row
    result.product_feats = product_feats
    result.review_feats = review_feats

    if dr.was_downgraded:
        logger.info("Layer 2 downgrade: %s → %s", l1_color, dr.final_label)
    else:
        logger.info(
            "Layer 2 evaluated: L1=%s → final=%s, reviews=%d",
            l1_color, dr.final_label, result.review_count,
        )

    result.product_signals, result.review_signals = (
        build_quality_signal_views(product_row, product_feats, review_feats)
    )

    return result


# ---------------------------------------------------------------------------
# Stage 6: ML Model Scoring
# ---------------------------------------------------------------------------

def _build_ml_feature_row(
    user_profile: dict,
    financial_dict: dict,
    product_price: float,
    l2: L2Result,
) -> dict:
    """Assemble the raw feature row matching the training schema."""
    pr = l2.product_row
    pf = l2.product_feats
    rf = l2.review_feats
    has_product = pr is not None

    return {
        # Raw user fields from DB
        "monthly_income": float(
            user_profile.get("monthly_income", 0) or 0,
        ),
        "monthly_expenses": float(
            user_profile.get("monthly_expenses", 0) or 0,
        ),
        "savings_balance": float(
            user_profile.get("savings_balance", 0) or 0,
        ),
        "has_loan": int(bool(user_profile.get("has_loan", 0))),
        "loan_amount": float(
            user_profile.get("loan_amount", 0) or 0,
        ),
        "monthly_emi": float(
            user_profile.get("monthly_emi", 0) or 0,
        ),
        "loan_interest_rate": float(
            user_profile.get("loan_interest_rate", 0) or 0,
        ),
        "loan_term_months": float(
            user_profile.get("loan_term_months", 0) or 0,
        ),
        "credit_score": float(
            user_profile.get("credit_score", 0) or 0,
        ),
        "employment_status": str(
            user_profile.get("employment_status", "unknown") or "unknown",
        ),
        "region": str(user_profile.get("region", "unknown") or "unknown"),
        # DB-precomputed financial ratios
        "liquid_savings": float(
            user_profile.get("liquid_savings", 0) or 0,
        ),
        "discretionary_income": float(
            user_profile.get("discretionary_income", 0) or 0,
        ),
        "debt_to_income_ratio": float(
            financial_dict.get("debt_to_income_ratio", 0),
        ),
        "saving_to_income_ratio": float(
            user_profile.get("saving_to_income_ratio", 0) or 0,
        ),
        "monthly_expense_burden_ratio": float(
            financial_dict.get("monthly_expense_burden_ratio", 0),
        ),
        "emergency_fund_months": float(
            financial_dict.get("emergency_fund_months", 0),
        ),
        # Affordability computed features
        "affordability_score": float(
            financial_dict.get("affordability_score", 0),
        ),
        "price_to_income_ratio": float(
            financial_dict.get("price_to_income_ratio", 0),
        ),
        "residual_utility_score": float(
            financial_dict.get("residual_utility_score") or 0,
        ),
        "savings_to_price_ratio": float(
            financial_dict.get("savings_to_price_ratio", 0),
        ),
        "net_worth_indicator": float(
            financial_dict.get("net_worth_indicator", 0),
        ),
        "credit_risk_indicator": float(
            financial_dict.get("credit_risk_indicator", 0) or 0,
        ),
        # Product raw fields
        "product_price": float(product_price or 0),
        "average_rating": float(
            pr["average_rating"] if has_product else 0,
        ),
        "rating_number": float(
            pr["rating_number"] if has_product else 0,
        ),
        "rating_variance": float(
            pr["rating_variance"] if has_product else 0,
        ),
        "category": str(
            pr["category"] if has_product else "unknown",
        ),
        # Product computed features
        "value_density": float(
            pf.value_density if has_product else 0,
        ),
        "review_confidence": float(
            pf.review_confidence if has_product else 0,
        ),
        "rating_polarization": float(
            pf.rating_polarization if has_product else 0,
        ),
        "quality_risk_score": float(
            pf.quality_risk_score if has_product else 0,
        ),
        "cold_start_flag": int(
            pf.cold_start_flag if has_product else 0,
        ),
        "price_category_rank": float(
            pf.price_category_rank if has_product else 0,
        ),
        "category_rating_deviation": float(
            pf.category_rating_deviation if has_product else 0,
        ),
        # Review computed features
        "verified_purchase_ratio": float(
            rf.verified_purchase_ratio if has_product else 0,
        ),
        "helpful_concentration": float(
            rf.helpful_concentration if has_product else 0,
        ),
        "sentiment_spread": float(
            rf.sentiment_spread if has_product else 0,
        ),
        "review_depth_score": float(
            rf.review_depth_score if has_product else 0,
        ),
        "reviewer_diversity": float(
            rf.reviewer_diversity if has_product else 0,
        ),
        "extreme_rating_ratio": float(
            rf.extreme_rating_ratio if has_product else 0,
        ),
        # Training schema
        "downgraded": int(l2.was_downgraded),
    }


def _score_ml_model(
    user_profile: dict,
    fin: FinancialResult,
    product: ProductResolution,
    l2: L2Result,
    manager,
) -> MLScore:
    """Score using the ML model. Returns informational confidence only."""
    if manager.model is None:
        return MLScore(unavailable_reason="no_model")
    if manager.feature_pipeline is None:
        return MLScore(unavailable_reason="no_pipeline")

    try:
        import numpy as np

        raw_row = _build_ml_feature_row(
            user_profile, fin.financial_dict, product.product_price, l2,
        )
        feature_df = pd.DataFrame([raw_row])
        X = manager.feature_pipeline.transform(feature_df)

        # Fix MLflow schema type expectations
        if isinstance(X, pd.DataFrame):
            X = X.copy()
            if "credit_score" in X.columns:
                cs = np.asarray(
                    X["credit_score"].values, dtype=float,
                ).ravel()
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
            logger.warning(
                "ML predict returned no confidence (label=%s)", label_ml,
            )
        else:
            result.confidence = float(conf)
            logger.info("ML confidence: %.4f", result.confidence)
        return result

    except Exception as e:
        logger.warning("ML model scoring failed: %s", e, exc_info=True)
        return MLScore(unavailable_reason="scoring_error")


# ---------------------------------------------------------------------------
# Stage 7: LLM Response Generation + Guardrails
# ---------------------------------------------------------------------------

def _generate_explanation(
    product: ProductResolution,
    fin: FinancialResult,
    l1: L1Result,
    l2: L2Result,
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
    mc = ml.confidence if ml.confidence is not None else 0.0

    context = RecommendationContext(
        product_name=product.product_name or "the product",
        product_price=product.product_price or 0.0,
        recommendation_color=l2.final_color,
        original_color=l1.color if l2.was_downgraded else l2.final_color,
        was_downgraded=l2.was_downgraded,
        triggered_rules=l1.triggered_rules + l2.product_triggers + l2.review_triggers
        if l2.was_downgraded else l1.triggered_rules,
        confidence_scores={l2.final_color: mc},
        ml_confidence=ml.confidence,
        ml_unavailable_reason=ml.unavailable_reason,
        hypothetical_purchase=(product.evaluation_mode == "hypothetical"),
        user_context=product.user_context,
        affordability_score=float(fd.get("affordability_score", 0.0)),
        affordability_score_unreliable=fin.affordability_unreliable,
        savings_to_price_ratio=float(
            fd.get("savings_to_price_ratio", 0.0),
        ),
        emergency_fund_months=float(
            fd.get("emergency_fund_months", 0.0),
        ),
        debt_to_income_ratio=float(
            fd.get("debt_to_income_ratio", 0.0),
        ),
        monthly_income=float(
            user_profile.get("monthly_income", 0) or 0,
        ),
        monthly_expense_burden_ratio=float(
            fd.get("monthly_expense_burden_ratio", 0.0),
        ),
        price_to_income_ratio=float(
            fd.get("price_to_income_ratio", 0.0),
        ),
        credit_score=user_profile.get("credit_score"),
        review_snippets=l2.review_snippets,
    )

    response_text = generate_response(context, manager.llm_provider)
    guardrail = check_response(response_text, l2.final_color, context)

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
    l1: L1Result,
    l2: L2Result,
    ml: MLScore,
    explanation: str,
    start_time: float,
) -> PredictResponse:
    """Assemble the final PredictResponse from all pipeline stages."""
    fd = fin.financial_dict

    # Merge triggered rules for response
    all_rules = list(l1.triggered_rules)
    if l2.was_downgraded:
        all_rules.extend(l2.product_triggers)
        all_rules.extend(l2.review_triggers)

    elapsed = time.time() - start_time
    logger.info(
        "Inference complete: color=%s, ml_confidence=%s, "
        "downgraded=%s, latency=%.3fs",
        l2.final_color,
        f"{ml.confidence:.2f}" if ml.confidence is not None else "n/a",
        l2.was_downgraded,
        elapsed,
    )

    return PredictResponse(
        recommendation=l2.final_color,
        confidence=ml.confidence,
        ml_unavailable_reason=ml.unavailable_reason,
        explanation=explanation,
        product_name=product.product_name,
        product_price=product.product_price,
        triggered_rules=all_rules,
        was_downgraded=l2.was_downgraded,
        guardrail_passed=True,
        evaluation_mode=product.evaluation_mode,
        layer2_evaluated=l2.layer2_evaluated,
        review_count=l2.review_count,
        layer2_product_triggers=l2.product_triggers,
        layer2_review_triggers=l2.review_triggers,
        product_signals=l2.product_signals,
        review_signals=l2.review_signals,
        affordability_score=float(fd.get("affordability_score", 0.0)),
        affordability_score_unreliable=fin.affordability_unreliable,
        emergency_fund_months=float(
            fd.get("emergency_fund_months", 0) or 0,
        ),
        debt_to_income_ratio=float(
            fd.get("debt_to_income_ratio", 0) or 0,
        ),
        financial_features=fin.features_view,
        layer1_recommendation=l1.color,
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

    Orchestrates seven discrete stages, each independently testable.
    """
    from llm.prompts.response_templates import USER_NOT_FOUND_RESPONSE

    start_time = time.time()

    # Stage 1: Load user financial profile
    if manager.db_engine is None:
        return PredictResponse(
            recommendation="YELLOW", confidence=None,
            explanation="Service is temporarily unavailable.",
            guardrail_passed=True, evaluation_mode="none",
        )

    user_profile = _load_user_financial_profile(
        request.user_id, manager.db_engine,
    )
    if user_profile is None:
        logger.warning("User not found: %s", request.user_id)
        return PredictResponse(
            recommendation="YELLOW", confidence=None,
            explanation=USER_NOT_FOUND_RESPONSE,
            guardrail_passed=True, evaluation_mode="none",
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

    # Stage 4: Layer 1 deterministic engine
    l1 = _run_layer1_engine(fin.financial_dict, resolution)

    # Stage 5: Layer 2 downgrade engine
    l2 = _run_layer2_engine(l1.color, resolution, manager)

    # Stage 6: ML model scoring
    ml = _score_ml_model(user_profile, fin, resolution, l2, manager)

    # Stage 7: LLM explanation + guardrails
    explanation = _generate_explanation(
        resolution, fin, l1, l2, ml, user_profile, manager,
    )

    # Assemble response
    return _build_response(
        resolution, fin, l1, l2, ml, explanation, start_time,
    )

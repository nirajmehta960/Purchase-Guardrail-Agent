"""
Pydantic Request / Response Schemas for the SavVio API.

Defines the contract for the /predict and /health endpoints.
"""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Request Schemas
# ---------------------------------------------------------------------------

class PredictRequest(BaseModel):
    """Request body for the /predict endpoint.

    Two modes of operation:
        1. Natural language query — the LLM parses the product from user_query,
           and the API looks up the user's financial profile via user_id.
        2. Direct product_id — skips LLM intent parsing, goes straight to
           product lookup + evaluation.
    """
    user_query: str = Field(
        ...,
        min_length=1,
        description="Natural language purchase query, e.g. 'Can I buy the Sony WH-1000XM5?'",
        examples=["Can I buy the Sony WH-1000XM5?"],
    )
    user_id: str = Field(
        ...,
        min_length=1,
        description="User ID for financial profile lookup in the database.",
        examples=["user_001"],
    )
    product_id: Optional[str] = Field(
        default=None,
        description=(
            "Optional product ID. If provided, skips LLM intent parsing and "
            "goes directly to product evaluation."
        ),
    )

    model_config = {"json_schema_extra": {
        "examples": [
            {
                "user_query": "Can I buy the Sony WH-1000XM5?",
                "user_id": "user_001",
            },
            {
                "user_query": "Evaluate this product for me",
                "user_id": "user_002",
                "product_id": "B0C8XYZ123",
            },
        ]
    }}


# ---------------------------------------------------------------------------
# Layer 2 — product / review signals for UI (matches feature engineering)
# ---------------------------------------------------------------------------


class ProductSignalsView(BaseModel):
    """Listing stats + engineered product features used in Layer 2 / ML."""

    average_rating: Optional[float] = None
    rating_count: Optional[int] = None
    rating_variance: Optional[float] = None
    category: Optional[str] = None
    price: Optional[float] = None
    price_position_in_category: Optional[str] = None
    rating_vs_category: Optional[str] = None
    cold_start: Optional[bool] = None
    value_density: Optional[float] = None
    review_confidence: Optional[float] = None
    rating_polarization: Optional[float] = None
    quality_risk_score: Optional[float] = None
    price_category_rank: Optional[float] = None
    category_rating_deviation: Optional[float] = None


class ReviewSignalsView(BaseModel):
    """Aggregated review features (same signals as training)."""

    verified_purchase_ratio: Optional[float] = None
    helpful_concentration: Optional[float] = None
    sentiment_spread: Optional[float] = None
    review_depth_score: Optional[float] = None
    reviewer_diversity: Optional[float] = None
    extreme_rating_ratio: Optional[float] = None
    sentiment_interpretation: Optional[str] = None


class FinancialFeaturesView(BaseModel):
    """Layer 1 financial inputs: 5 DB profile ratios + 6 affordability features."""

    discretionary_income: Optional[float] = None
    debt_to_income_ratio: Optional[float] = None
    saving_to_income_ratio: Optional[float] = None
    monthly_expense_burden_ratio: Optional[float] = None
    emergency_fund_months: Optional[float] = None
    affordability_score: Optional[float] = None
    price_to_income_ratio: Optional[float] = None
    residual_utility_score: Optional[float] = None
    savings_to_price_ratio: Optional[float] = None
    net_worth_indicator: Optional[float] = None
    credit_risk_indicator: Optional[float] = None


# ---------------------------------------------------------------------------
# Response Schemas
# ---------------------------------------------------------------------------

class PredictResponse(BaseModel):
    """Response body for the /predict endpoint."""
    recommendation: str = Field(
        ...,
        description="Recommendation color: GREEN, YELLOW, or RED.",
    )
    confidence: Optional[float] = Field(
        default=None,
        description=(
            "ML model confidence for the label (0–1) when the classifier is loaded; "
            "null if the model is not available (deterministic engine still applies)."
        ),
    )
    ml_unavailable_reason: Optional[str] = Field(
        default=None,
        description=(
            "When confidence is null: no_model, no_pipeline, or scoring_error — "
            "for clearer UI than a generic 'not loaded' message."
        ),
    )
    explanation: str = Field(
        ...,
        description="Natural language explanation from the LLM wrapper.",
    )
    product_name: Optional[str] = Field(
        default=None,
        description="Resolved product name from the database.",
    )
    product_price: Optional[float] = Field(
        default=None,
        description="Resolved product price.",
    )
    triggered_rules: list[str] = Field(
        default_factory=list,
        description="List of deterministic engine rules that triggered.",
    )
    was_downgraded: bool = Field(
        default=False,
        description="Whether the Layer 2 downgrade engine changed the color.",
    )
    guardrail_passed: bool = Field(
        default=True,
        description="Whether the LLM response passed all 6 safety guardrail checks.",
    )
    evaluation_mode: str = Field(
        default="catalog",
        description="'catalog' if a product row was matched; 'hypothetical' if only price + description from the query.",
    )
    layer2_evaluated: bool = Field(
        default=False,
        description="True when Layer 2 (DowngradeEngine) ran with product + review features.",
    )
    review_count: int = Field(
        default=0,
        description="Number of review rows loaded for this product_id (0 if none).",
    )
    layer2_product_triggers: list[str] = Field(
        default_factory=list,
        description="Product-side downgrade rule ids evaluated (may be empty).",
    )
    layer2_review_triggers: list[str] = Field(
        default_factory=list,
        description="Review-side downgrade rule ids evaluated (may be empty).",
    )
    product_signals: Optional[ProductSignalsView] = Field(
        default=None,
        description="Product listing + engineered product features when Layer 2 ran.",
    )
    review_signals: Optional[ReviewSignalsView] = Field(
        default=None,
        description="Engineered review aggregate features when Layer 2 ran.",
    )
    affordability_score: Optional[float] = Field(
        default=None,
        description="discretionary − price for this evaluation (Layer 1 inputs).",
    )
    affordability_score_unreliable: bool = Field(
        default=False,
        description="True when raw affordability was outside safe bounds (clamped for rules).",
    )
    emergency_fund_months: Optional[float] = Field(
        default=None,
        description="User emergency fund runway (months) from profile — for UI summaries.",
    )
    debt_to_income_ratio: Optional[float] = Field(
        default=None,
        description="User debt-to-income ratio 0–1 from profile — for UI summaries.",
    )
    financial_features: Optional[FinancialFeaturesView] = Field(
        default=None,
        description="Full Layer 1 feature snapshot (user profile + purchase pair).",
    )
    layer1_recommendation: Optional[str] = Field(
        default=None,
        description="Deterministic engine label before Layer 2 downgrade (GREEN/YELLOW/RED).",
    )
    ml_predicted_label: Optional[str] = Field(
        default=None,
        description="ML classifier label when scoring succeeds (informational only).",
    )
    ml_model_name: str = Field(
        default="SavVio classifier",
        description="Display name for ML layer in UI.",
    )


class HealthResponse(BaseModel):
    """Response body for the /health endpoint."""
    status: str = Field(
        default="healthy",
        description="Service health status.",
    )
    model_loaded: bool = Field(
        ...,
        description="Whether the ML model is loaded and ready for inference.",
    )
    db_connected: bool = Field(
        ...,
        description="Whether the database connection is active.",
    )
    llm_provider: str = Field(
        ...,
        description="Active LLM provider name (mock, openai, gemini, claude).",
    )
    version: str = Field(
        ...,
        description="API version string.",
    )


class ErrorResponse(BaseModel):
    """Structured error response."""
    error: str = Field(..., description="Error type.")
    detail: str = Field(..., description="Human-readable error message.")


# ---------------------------------------------------------------------------
# Product catalog (browse)
# ---------------------------------------------------------------------------


class ProductListItem(BaseModel):
    """One row from the products table for picker UI."""

    product_id: str
    product_name: str
    price: Optional[float] = None
    average_rating: Optional[float] = None
    rating_number: Optional[float] = None


class ProductListResponse(BaseModel):
    """Paginated product list for catalog selection."""

    items: list[ProductListItem] = Field(default_factory=list)
    total: int = Field(0, description="Total rows matching filters (before limit/offset).")
    price_min_applied: float = Field(..., description="Lower bound used for this query.")
    price_max_applied: float = Field(..., description="Upper bound used for this query.")
    limit: int = Field(..., description="Page size.")
    offset: int = Field(0, description="Skip rows.")


# ---------------------------------------------------------------------------
# User profile (dashboard)
# ---------------------------------------------------------------------------


class UserProfileResponse(BaseModel):
    """Financial profile row for dashboard display."""

    user_id: str
    monthly_income: Optional[float] = None
    monthly_expenses: Optional[float] = None
    savings_balance: Optional[float] = None
    has_loan: Optional[int] = None
    loan_amount: Optional[float] = None
    monthly_emi: Optional[float] = None
    loan_interest_rate: Optional[float] = None
    loan_term_months: Optional[float] = None
    credit_score: Optional[int] = None
    employment_status: Optional[str] = None
    region: Optional[str] = None
    liquid_savings: Optional[float] = None
    discretionary_income: Optional[float] = None
    debt_to_income_ratio: Optional[float] = None
    saving_to_income_ratio: Optional[float] = None
    monthly_expense_burden_ratio: Optional[float] = None
    emergency_fund_months: Optional[float] = None

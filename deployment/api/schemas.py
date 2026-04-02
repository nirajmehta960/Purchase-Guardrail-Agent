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
# Response Schemas
# ---------------------------------------------------------------------------

class PredictResponse(BaseModel):
    """Response body for the /predict endpoint."""
    recommendation: str = Field(
        ...,
        description="Recommendation color: GREEN, YELLOW, or RED.",
    )
    confidence: float = Field(
        ...,
        ge=0.0,
        le=1.0,
        description="ML model confidence score for the recommendation (0–1).",
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

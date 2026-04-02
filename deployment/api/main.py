"""
SavVio FastAPI Application — Production Inference API.

Exposes the SavVio recommendation engine as a REST API:
    GET  /health   — Liveness check (model, DB, LLM status)
    GET  /products — Browse catalog (price band + search) for product picker
    POST /predict  — Full inference pipeline (prompt → recommendation)
    GET  /user/{user_id}/evaluate?product_id=...  — Direct product evaluation

Startup:
    The app loads model artifacts, initializes the DB connection, and sets up
    the LLM provider on startup via the ModelManager singleton.

Usage:
    uvicorn deployment.api.main:app --host 0.0.0.0 --port 3500 --reload
"""

from __future__ import annotations

import logging
import time
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from deployment.api.config import APIConfig
from deployment.api.model_loader import model_manager
from deployment.api.schemas import (
    ErrorResponse,
    HealthResponse,
    PredictRequest,
    PredictResponse,
    ProductListItem,
    ProductListResponse,
    UserProfileResponse,
)

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=getattr(logging, APIConfig.LOG_LEVEL, logging.INFO),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Lifespan (startup / shutdown)
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Load resources at startup, release at shutdown."""
    logger.info("Starting SavVio API v%s...", APIConfig.API_VERSION)
    try:
        model_manager.load()
        logger.info("All resources loaded successfully.")
    except Exception as e:
        logger.error("Startup resource loading failed: %s", e, exc_info=True)
        logger.warning("API will start but some features may be unavailable.")
    yield
    logger.info("Shutting down SavVio API...")


# ---------------------------------------------------------------------------
# App Factory
# ---------------------------------------------------------------------------

app = FastAPI(
    title=APIConfig.API_TITLE,
    version=APIConfig.API_VERSION,
    description=APIConfig.API_DESCRIPTION,
    lifespan=lifespan,
)

# CORS — allow Streamlit frontend and local dev
app.add_middleware(
    CORSMiddleware,
    allow_origins=APIConfig.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------------------
# Request Logging Middleware
# ---------------------------------------------------------------------------

@app.middleware("http")
async def log_requests(request: Request, call_next):
    """Log every request with timing for Cloud Logging / monitoring."""
    start = time.time()
    response = await call_next(request)
    elapsed = time.time() - start

    logger.info(
        "request_log | method=%s path=%s status=%d latency=%.3fs",
        request.method,
        request.url.path,
        response.status_code,
        elapsed,
    )
    return response


# ---------------------------------------------------------------------------
# Exception Handlers
# ---------------------------------------------------------------------------

@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    return JSONResponse(
        status_code=exc.status_code,
        content=ErrorResponse(
            error=f"http_{exc.status_code}",
            detail=str(exc.detail),
        ).model_dump(),
    )


@app.exception_handler(Exception)
async def general_exception_handler(request: Request, exc: Exception):
    logger.error("Unhandled exception: %s", exc, exc_info=True)
    return JSONResponse(
        status_code=500,
        content=ErrorResponse(
            error="internal_error",
            detail="An unexpected error occurred. Please try again.",
        ).model_dump(),
    )


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@app.get(
    "/user/{user_id}/profile",
    response_model=UserProfileResponse,
    tags=["User"],
)
async def get_user_profile(user_id: str):
    """Return the user's financial profile from PostgreSQL for the dashboard."""
    from deployment.api.inference import _load_user_financial_profile

    if not model_manager.db_engine:
        raise HTTPException(
            status_code=503,
            detail="Database unavailable.",
        )

    row = _load_user_financial_profile(user_id, model_manager.db_engine)
    if row is None:
        raise HTTPException(
            status_code=404,
            detail=f"No financial profile found for user_id={user_id!r}.",
        )

    return UserProfileResponse(**row)


@app.get(
    "/products",
    response_model=ProductListResponse,
    tags=["Catalog"],
)
async def browse_products(
    q: Optional[str] = Query(None, description="Substring filter on product_name (ILIKE)."),
    price_min: Optional[float] = Query(None, description="Minimum price; defaults from PRODUCT_BROWSE_PRICE_MIN."),
    price_max: Optional[float] = Query(None, description="Maximum price; defaults from PRODUCT_BROWSE_PRICE_MAX."),
    limit: int = Query(
        APIConfig.PRODUCT_BROWSE_DEFAULT_LIMIT,
        ge=1,
        le=APIConfig.PRODUCT_BROWSE_MAX_LIMIT,
    ),
    offset: int = Query(0, ge=0),
):
    """List products for catalog selection — enables Layer 2 + review signals when used with /predict."""
    from deployment.api.products_catalog import list_products as fetch_products

    if not model_manager.db_engine:
        raise HTTPException(status_code=503, detail="Database unavailable.")

    lim = min(limit, APIConfig.PRODUCT_BROWSE_MAX_LIMIT)
    rows, total, pmin_applied, pmax_applied = fetch_products(
        model_manager.db_engine,
        q=q,
        price_min=price_min,
        price_max=price_max,
        limit=lim,
        offset=offset,
        default_price_min=APIConfig.PRODUCT_BROWSE_PRICE_MIN,
        default_price_max=APIConfig.PRODUCT_BROWSE_PRICE_MAX,
    )
    return ProductListResponse(
        items=[ProductListItem(**r) for r in rows],
        total=total,
        price_min_applied=pmin_applied,
        price_max_applied=pmax_applied,
        limit=lim,
        offset=offset,
    )


@app.get("/health", response_model=HealthResponse, tags=["Health"])
async def health_check():
    """Liveness check — verifies model loaded, DB connected, LLM provider active.

    Used by Cloud Run for health probes and deployment verification.
    """
    return HealthResponse(
        status="healthy" if model_manager.is_loaded else "degraded",
        model_loaded=model_manager.model is not None,
        db_connected=model_manager.check_db_connection(),
        llm_provider=model_manager.get_llm_provider_name(),
        version=APIConfig.API_VERSION,
    )


@app.post("/predict", response_model=PredictResponse, tags=["Inference"])
async def predict(request: PredictRequest):
    """Full inference pipeline — natural language prompt → recommendation.

    Accepts a user query and user_id, then orchestrates:
    1. Financial profile lookup
    2. Intent parsing (LLM)
    3. Product resolution (pgvector)
    4. Deterministic engine evaluation (Layer 1 + Layer 2)
    5. ML model confidence scoring
    6. LLM response generation with guardrail verification

    The deterministic engine's color is AUTHORITATIVE — the ML model
    and LLM wrapper cannot override it.
    """
    from deployment.api.inference import run_inference

    try:
        response = run_inference(request, model_manager)
        return response
    except Exception as e:
        logger.error("Inference failed for user=%s: %s", request.user_id, e, exc_info=True)
        raise HTTPException(
            status_code=500,
            detail="Inference pipeline failed. Please try again.",
        )


@app.get(
    "/user/{user_id}/evaluate",
    response_model=PredictResponse,
    tags=["Inference"],
)
async def evaluate_product(
    user_id: str,
    product_id: str = Query(..., description="Product ID to evaluate."),
):
    """Direct product evaluation — skips LLM intent parsing.

    Convenience endpoint for when the frontend already knows the product_id.
    Goes directly to financial evaluation + ML scoring + LLM explanation.
    """
    from deployment.api.inference import run_inference

    request = PredictRequest(
        user_query=f"Evaluate product {product_id}",
        user_id=user_id,
        product_id=product_id,
    )

    try:
        response = run_inference(request, model_manager)
        return response
    except Exception as e:
        logger.error(
            "Evaluation failed for user=%s, product=%s: %s",
            user_id, product_id, e, exc_info=True,
        )
        raise HTTPException(
            status_code=500,
            detail="Product evaluation failed. Please try again.",
        )


# ---------------------------------------------------------------------------
# Run with: uvicorn deployment.api.main:app --host 0.0.0.0 --port 3500
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "deployment.api.main:app",
        host=APIConfig.API_HOST,
        port=APIConfig.API_PORT,
        reload=True,
    )

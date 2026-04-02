"""
API Configuration for SavVio Deployment.

Centralized settings for the FastAPI backend — model paths, DB environment,
CORS origins, and logging. Reads from environment variables with sensible
defaults for local development.
"""

import os
from pathlib import Path

# ---------------------------------------------------------------------------
# Path Resolution
# ---------------------------------------------------------------------------

# deployment/api/config.py → deployment/ → SavVio/
_DEPLOYMENT_DIR = Path(__file__).resolve().parent.parent
_PROJECT_ROOT = _DEPLOYMENT_DIR.parent


class APIConfig:
    """Configuration for the SavVio deployment API."""

    # ---------------------------------------------------------------------------
    # Paths
    # ---------------------------------------------------------------------------
    PROJECT_ROOT = _PROJECT_ROOT
    DEPLOYMENT_DIR = _DEPLOYMENT_DIR

    # Model artifacts — saved by run_pipeline.py's save_best_model_local()
    MODEL_ARTIFACT_DIR = os.getenv(
        "MODEL_ARTIFACT_DIR",
        str(_PROJECT_ROOT / "model_pipeline" / "models" / "artifacts"),
    )
    LABEL_ENCODER_PATH = os.getenv(
        "LABEL_ENCODER_PATH",
        str(_PROJECT_ROOT / "model_pipeline" / "models" / "preprocessing" / "label_encoder.pkl"),
    )
    # Fitted sklearn feature pipeline (saved by FeaturePipeline.save() during training)
    FEATURE_PIPELINE_PATH = os.getenv(
        "FEATURE_PIPELINE_PATH",
        str(_PROJECT_ROOT / "model_pipeline" / "models" / "artifacts" / "feature_pipeline.pkl"),
    )

    # ---------------------------------------------------------------------------
    # Database
    # ---------------------------------------------------------------------------
    # "dev" or "prod" — passed to savviocore.database.db_connection.get_engine()
    DB_ENV = os.getenv("DB_ENV", "dev")

    # ---------------------------------------------------------------------------
    # Server
    # ---------------------------------------------------------------------------
    API_HOST = os.getenv("API_HOST", "0.0.0.0")
    API_PORT = int(os.getenv("API_PORT", "8080"))

    # CORS — allow Streamlit frontend and local dev
    CORS_ORIGINS = os.getenv(
        "CORS_ORIGINS",
        "http://localhost:8501,http://localhost:3000,*",
    ).split(",")

    # ---------------------------------------------------------------------------
    # API Metadata
    # ---------------------------------------------------------------------------
    API_TITLE = "SavVio API"
    API_VERSION = "1.0.0"
    API_DESCRIPTION = (
        "AI-driven financial purchase recommendation API. "
        "Provides Green/Yellow/Red signals based on real-time financial health."
    )

    # ---------------------------------------------------------------------------
    # Logging
    # ---------------------------------------------------------------------------
    LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")

    # ---------------------------------------------------------------------------
    # MLflow (for loading models from registry — optional)
    # ---------------------------------------------------------------------------
    MLFLOW_TRACKING_URI = os.getenv("MLFLOW_TRACKING_URI", "")

    # ---------------------------------------------------------------------------
    # Model Pipeline Source Paths (for importing existing modules)
    # ---------------------------------------------------------------------------
    MODEL_PIPELINE_SRC = str(_PROJECT_ROOT / "model_pipeline" / "src")
    SAVVIOCORE_SRC = str(_PROJECT_ROOT / "savviocore" / "src")

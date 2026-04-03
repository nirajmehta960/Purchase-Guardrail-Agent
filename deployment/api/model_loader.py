"""
Model Loader — Resource management for the SavVio inference pipeline.

ModelManager loads all inference resources and exposes a unified interface
for the API layer:
    - ML model (MLflow pyfunc / XGBoost)
    - Label encoder (sklearn LabelEncoder)
    - Feature preprocessing pipeline (sklearn)
    - Database engine (SQLAlchemy via savviocore)
    - LLM provider (Groq / Gemini / mock)
    - Category stats (for product feature computation)

Usage:
    from deployment.api.model_loader import model_manager

    # At startup:
    model_manager.load()

    # During inference:
    label, confidence = model_manager.predict(features_array)
"""

from __future__ import annotations

import logging
import os
import sys

import joblib
import numpy as np

from deployment.api.config import APIConfig

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def _max_class_probability(proba) -> float:
    """Best-effort max softmax probability across classes."""
    try:
        import pandas as pd

        if isinstance(proba, pd.DataFrame):
            arr = proba.to_numpy(dtype=float)
        else:
            arr = np.asarray(proba, dtype=float)
        if arr.size == 0:
            return 0.0
        if arr.ndim == 1:
            return float(np.max(arr))
        return float(np.max(arr[0]))
    except Exception:
        return 0.0


def _ensure_import_paths():
    """Add model_pipeline/src and savviocore/src to sys.path so we can
    import the existing modules (deterministic engine, LLM, features, etc.)."""
    for path in [APIConfig.MODEL_PIPELINE_SRC, APIConfig.SAVVIOCORE_SRC]:
        if path not in sys.path:
            sys.path.insert(0, path)
            logger.info("Added to sys.path: %s", path)


# Ensure import paths are available before any downstream imports.
_ensure_import_paths()


# ---------------------------------------------------------------------------
# ModelManager
# ---------------------------------------------------------------------------

class ModelManager:
    """Loads all inference resources and exposes a unified interface.

    Handles: ML model, label encoder, feature pipeline, DB, LLM, and
    category stats.  Each is loaded via a simple private method.
    """

    _DEFAULT_LABEL_CLASSES = ["GREEN", "RED", "YELLOW"]

    def __init__(self):
        self.model = None
        self.label_encoder = None
        self.feature_pipeline = None
        self.db_engine = None
        self.llm_provider = None
        self.category_stats: dict = {}
        self.max_rating_number: float = 0.0
        self._loaded = False

    # --- Properties ---

    @property
    def is_loaded(self) -> bool:
        return self._loaded

    # --- Loading ---

    def load(self):
        """Load all resources. Call once at API startup."""
        logger.info("Loading model manager resources...")
        self._load_model(APIConfig.MODEL_ARTIFACT_DIR)
        self._load_label_encoder(APIConfig.LABEL_ENCODER_PATH)
        self._load_feature_pipeline(APIConfig.FEATURE_PIPELINE_PATH)
        self._connect_db(APIConfig.DB_ENV)
        self._init_llm_provider()
        self._compute_category_stats()
        self._loaded = True
        logger.info("Model manager fully loaded.")

    def _load_model(self, artifact_dir: str) -> None:
        """Load MLflow pyfunc model from local artifact directory.

        The champion model is saved by run_pipeline.py's save_best_model_local()
        into model_pipeline/models/artifacts/ via mlflow.artifacts.download_artifacts.
        The MLmodel file lives directly in that directory.
        """
        logger.info("Loading model from: %s", artifact_dir)
        if not os.path.exists(artifact_dir):
            logger.error("Model artifact directory not found: %s", artifact_dir)
            return

        import mlflow.pyfunc
        mlmodel_dirs = [
            root for root, _dirs, files in os.walk(artifact_dir) if "MLmodel" in files
        ]
        if not mlmodel_dirs:
            logger.error("No MLmodel file found in %s", artifact_dir)
            return

        model_path = mlmodel_dirs[0]
        self.model = mlflow.pyfunc.load_model(model_path)
        logger.info("Loaded model via mlflow.pyfunc from: %s", model_path)

    def _load_label_encoder(self, encoder_path: str) -> None:
        """Load label encoder or create a default."""
        logger.info("Loading label encoder from: %s", encoder_path)
        if not os.path.exists(encoder_path):
            logger.warning("Label encoder not found at %s — using default.", encoder_path)
            self._create_default_encoder()
            return
        try:
            self.label_encoder = joblib.load(encoder_path)
            logger.info("Label encoder loaded. Classes: %s", list(self.label_encoder.classes_))
        except Exception as e:
            logger.error("Failed to load label encoder: %s", e, exc_info=True)
            self._create_default_encoder()

    def _create_default_encoder(self):
        from sklearn.preprocessing import LabelEncoder
        self.label_encoder = LabelEncoder()
        self.label_encoder.fit(self._DEFAULT_LABEL_CLASSES)

    def _load_feature_pipeline(self, pipeline_path: str) -> None:
        """Load the fitted sklearn feature pipeline."""
        logger.info("Loading feature pipeline from: %s", pipeline_path)
        if not os.path.exists(pipeline_path):
            logger.warning("Feature pipeline not found at %s", pipeline_path)
            return
        try:
            self.feature_pipeline = joblib.load(pipeline_path)
            logger.info("Feature pipeline loaded: %s", type(self.feature_pipeline).__name__)
        except Exception as e:
            logger.error("Failed to load feature pipeline: %s", e, exc_info=True)

    def _connect_db(self, env: str) -> None:
        """Initialize the SQLAlchemy database engine."""
        try:
            from savviocore.database.db_connection import get_engine
            self.db_engine = get_engine(env=env)
            from sqlalchemy import text
            with self.db_engine.connect() as conn:
                conn.execute(text("SELECT 1"))
            logger.info("Database connected (env=%s)", env)
        except Exception as e:
            logger.warning("Database initialization failed: %s", e)
            self.db_engine = None

    def _init_llm_provider(self) -> None:
        """Initialize the LLM provider."""
        try:
            from llm.llm_provider import get_provider
            self.llm_provider = get_provider()
            logger.info("LLM provider initialized: %s", self.llm_provider.provider_name)
        except Exception as e:
            logger.warning("LLM provider initialization failed: %s — using mock.", e)
            from llm.llm_provider import MockProvider
            self.llm_provider = MockProvider()

    def _compute_category_stats(self) -> None:
        """Pre-compute category statistics from the products table."""
        if self.db_engine is None:
            logger.warning("Skipping category stats — no DB connection.")
            return
        try:
            import pandas as pd
            from features.product_features import compute_category_stats

            products_df = pd.read_sql(
                "SELECT product_id, price, average_rating, rating_number, "
                "rating_variance, category FROM products",
                self.db_engine,
            )
            self.category_stats = compute_category_stats(products_df)
            self.max_rating_number = float(products_df["rating_number"].max() or 0.0)
            logger.info(
                "Category stats computed for %d categories, max_rating_number=%.0f",
                len(self.category_stats), self.max_rating_number,
            )
        except Exception as e:
            logger.warning("Category stats computation failed: %s", e)

    # --- Inference ---

    def predict(self, features) -> tuple[str, float | None]:
        """Run the ML model and return (predicted_label, confidence)."""
        if self.model is None:
            logger.warning("No model loaded — returning GREEN with no ML confidence.")
            return "GREEN", None

        try:
            if hasattr(self.model, "predict_proba"):
                pred = self.model.predict(features)
                proba = self.model.predict_proba(features)
            elif hasattr(self.model, "_model_impl"):
                import pandas as pd
                if not isinstance(features, pd.DataFrame):
                    features = pd.DataFrame(features)
                pred_raw = self.model.predict(features)
                pred = pred_raw.values if hasattr(pred_raw, "values") else pred_raw
                proba = self._predict_proba_for_pyfunc(features)
            else:
                logger.warning("Model type not recognized — returning GREEN with no confidence.")
                return "GREEN", None

            pred_int = int(pred[0]) if hasattr(pred[0], "__int__") else pred[0]
            if isinstance(pred_int, (int, np.integer)):
                label = self.label_encoder.inverse_transform([pred_int])[0]
            else:
                label = str(pred_int)

            confidence = _max_class_probability(proba) if proba is not None else None
            return label, confidence

        except Exception as e:
            logger.error("Prediction failed: %s", e, exc_info=True)
            return "GREEN", None

    def _predict_proba_for_pyfunc(self, features_df):
        """Class probabilities for pyfunc-loaded models; None if unavailable."""
        try:
            unwrapped = self.model._model_impl
            if hasattr(unwrapped, "predict_proba"):
                return unwrapped.predict_proba(features_df)
            for attr in ("sklearn_model", "model", "classifier"):
                if hasattr(unwrapped, attr):
                    inner = getattr(unwrapped, attr)
                    if hasattr(inner, "predict_proba"):
                        return inner.predict_proba(features_df)
        except Exception as e:
            logger.warning("Unwrapped MLflow model predict_proba failed: %s", e)
        return None

    # --- Health ---

    def check_db_connection(self) -> bool:
        if self.db_engine is None:
            return False
        try:
            from sqlalchemy import text
            with self.db_engine.connect() as conn:
                conn.execute(text("SELECT 1"))
            return True
        except Exception:
            return False

    def get_llm_provider_name(self) -> str:
        if self.llm_provider is None:
            return "none"
        return self.llm_provider.provider_name


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

model_manager = ModelManager()

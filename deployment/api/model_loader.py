"""
Model Loader — Resource management for the SavVio inference pipeline.

Focused classes handle individual resources; ModelManager composes them.

Each loader class owns a single responsibility:
    - ModelArtifactLoader  → ML model (MLflow pyfunc / XGBoost)
    - LabelEncoderLoader   → sklearn LabelEncoder
    - FeaturePipelineLoader→ sklearn preprocessing pipeline
    - DatabaseManager      → SQLAlchemy engine
    - LLMManager           → LLM provider (Groq / Gemini / mock)

ModelManager is the composition root: it delegates to the focused classes
and exposes a unified interface for the API layer.

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
# Focused Resource Loaders
# ---------------------------------------------------------------------------

class ModelArtifactLoader:
    """Loads and manages the ML model artifact (MLflow pyfunc or XGBoost)."""

    def __init__(self):
        self.model = None
        self._mlflow_model_uri: str | None = None
        self._native_classifier = None

    def load(self, artifact_dir: str) -> None:
        logger.info("Loading model from: %s", artifact_dir)
        if not os.path.exists(artifact_dir):
            logger.warning("Model artifact directory not found: %s", artifact_dir)
            return

        # Try mlflow.pyfunc first, then direct xgboost
        try:
            import mlflow.pyfunc
            mlmodel_dirs = [
                root for root, _dirs, files in os.walk(artifact_dir) if "MLmodel" in files
            ]
            if mlmodel_dirs:
                model_path = mlmodel_dirs[0]
                self.model = mlflow.pyfunc.load_model(model_path)
                self._mlflow_model_uri = model_path
                logger.info("Loaded model via mlflow.pyfunc from: %s", model_path)
                self._load_native_classifier()
                return
        except Exception as e:
            logger.warning("mlflow.pyfunc load failed: %s — trying xgboost direct load", e)

        try:
            from xgboost import XGBClassifier
            model_file = os.path.join(artifact_dir, "model.xgb")
            if os.path.exists(model_file):
                self.model = XGBClassifier()
                self.model.load_model(model_file)
                logger.info("Loaded XGBoost model from: %s", model_file)
            else:
                logger.warning("No model file found in %s", artifact_dir)
        except Exception as e:
            logger.error("Failed to load model: %s", e, exc_info=True)

    def _load_native_classifier(self):
        """Reload via mlflow.xgboost / lightgbm flavor for predict_proba."""
        if self._native_classifier is not None or not self._mlflow_model_uri:
            return
        uri = self._mlflow_model_uri
        for flavor, name in [("mlflow.xgboost", "XGBoost"), ("mlflow.lightgbm", "LightGBM")]:
            try:
                import importlib
                mod = importlib.import_module(flavor)
                m = mod.load_model(uri)
                if hasattr(m, "predict_proba"):
                    self._native_classifier = m
                    logger.info("Native %s model available for predict_proba (%s)", name, uri)
                    return
            except Exception as e:
                logger.debug("%s.load_model(%s): %s", flavor, uri, e)

    def predict_proba_for_pyfunc(self, features_df):
        """Class probabilities for pyfunc-loaded models; None if unavailable."""
        if self._native_classifier is not None:
            try:
                return self._native_classifier.predict_proba(features_df)
            except Exception as e:
                logger.warning("Native classifier predict_proba failed: %s", e)
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


class LabelEncoderLoader:
    """Loads the label encoder for decoding integer predictions."""

    _DEFAULT_CLASSES = ["GREEN", "RED", "YELLOW"]

    def __init__(self):
        self.encoder = None

    def load(self, encoder_path: str) -> None:
        logger.info("Loading label encoder from: %s", encoder_path)
        if not os.path.exists(encoder_path):
            logger.warning("Label encoder not found at %s — using default.", encoder_path)
            self._create_default()
            return
        try:
            self.encoder = joblib.load(encoder_path)
            logger.info("Label encoder loaded. Classes: %s", list(self.encoder.classes_))
        except Exception as e:
            logger.error("Failed to load label encoder: %s", e, exc_info=True)
            self._create_default()

    def _create_default(self):
        from sklearn.preprocessing import LabelEncoder
        self.encoder = LabelEncoder()
        self.encoder.fit(self._DEFAULT_CLASSES)


class FeaturePipelineLoader:
    """Loads the fitted sklearn feature pipeline saved during training."""

    def __init__(self):
        self.pipeline = None

    def load(self, pipeline_path: str) -> None:
        logger.info("Loading feature pipeline from: %s", pipeline_path)
        if not os.path.exists(pipeline_path):
            logger.warning("Feature pipeline not found at %s", pipeline_path)
            return
        try:
            self.pipeline = joblib.load(pipeline_path)
            logger.info("Feature pipeline loaded: %s", type(self.pipeline).__name__)
        except Exception as e:
            logger.error("Failed to load feature pipeline: %s", e, exc_info=True)


class DatabaseManager:
    """Manages the SQLAlchemy database engine."""

    def __init__(self):
        self.engine = None

    def connect(self, env: str) -> None:
        try:
            from savviocore.database.db_connection import get_engine
            self.engine = get_engine(env=env)
            from sqlalchemy import text
            with self.engine.connect() as conn:
                conn.execute(text("SELECT 1"))
            logger.info("Database connected (env=%s)", env)
        except Exception as e:
            logger.warning("Database initialization failed: %s", e)
            self.engine = None

    def check_connection(self) -> bool:
        if self.engine is None:
            return False
        try:
            from sqlalchemy import text
            with self.engine.connect() as conn:
                conn.execute(text("SELECT 1"))
            return True
        except Exception:
            return False


class LLMManager:
    """Manages the LLM provider lifecycle."""

    def __init__(self):
        self.provider = None

    def initialize(self) -> None:
        try:
            from llm.llm_provider import get_provider
            self.provider = get_provider()
            logger.info("LLM provider initialized: %s", self.provider.provider_name)
        except Exception as e:
            logger.warning("LLM provider initialization failed: %s — using mock.", e)
            from llm.llm_provider import MockProvider
            self.provider = MockProvider()

    @property
    def provider_name(self) -> str:
        if self.provider is None:
            return "none"
        return self.provider.provider_name


# ---------------------------------------------------------------------------
# Category Stats (stateless helper)
# ---------------------------------------------------------------------------

def compute_category_stats(db_engine) -> tuple[dict, float]:
    """Pre-compute category statistics from the products table.

    Returns (category_stats_dict, max_rating_number).
    """
    if db_engine is None:
        logger.warning("Skipping category stats — no DB connection.")
        return {}, 0.0
    try:
        import pandas as pd
        from features.product_features import compute_category_stats as _compute

        products_df = pd.read_sql(
            "SELECT product_id, price, average_rating, rating_number, "
            "rating_variance, category FROM products",
            db_engine,
        )
        stats = _compute(products_df)
        max_rn = float(products_df["rating_number"].max() or 0.0)
        logger.info("Category stats computed for %d categories, max_rating_number=%.0f", len(stats), max_rn)
        return stats, max_rn
    except Exception as e:
        logger.warning("Category stats computation failed: %s", e)
        return {}, 0.0


# ---------------------------------------------------------------------------
# ModelManager — Composition Root
# ---------------------------------------------------------------------------

class ModelManager:
    """Facade that composes all inference resources.

    Delegates loading to focused classes; exposes a unified interface
    for the API layer. Property accessors maintain backward compatibility.
    """

    def __init__(self):
        self._model_loader = ModelArtifactLoader()
        self._label_encoder_loader = LabelEncoderLoader()
        self._feature_pipeline_loader = FeaturePipelineLoader()
        self._db = DatabaseManager()
        self._llm = LLMManager()
        self.category_stats: dict = {}
        self.max_rating_number: float = 0.0
        self._loaded = False

    # --- Properties (backward-compatible access) ---

    @property
    def is_loaded(self) -> bool:
        return self._loaded

    @property
    def model(self):
        return self._model_loader.model

    @model.setter
    def model(self, val):
        self._model_loader.model = val

    @property
    def label_encoder(self):
        return self._label_encoder_loader.encoder

    @label_encoder.setter
    def label_encoder(self, val):
        self._label_encoder_loader.encoder = val

    @property
    def feature_pipeline(self):
        return self._feature_pipeline_loader.pipeline

    @feature_pipeline.setter
    def feature_pipeline(self, val):
        self._feature_pipeline_loader.pipeline = val

    @property
    def db_engine(self):
        return self._db.engine

    @db_engine.setter
    def db_engine(self, val):
        self._db.engine = val

    @property
    def llm_provider(self):
        return self._llm.provider

    @llm_provider.setter
    def llm_provider(self, val):
        self._llm.provider = val

    # --- Loading (delegates to focused classes) ---

    def load(self):
        """Load all resources. Call once at API startup."""
        logger.info("Loading model manager resources...")
        self._model_loader.load(APIConfig.MODEL_ARTIFACT_DIR)
        self._label_encoder_loader.load(APIConfig.LABEL_ENCODER_PATH)
        self._feature_pipeline_loader.load(APIConfig.FEATURE_PIPELINE_PATH)
        self._db.connect(APIConfig.DB_ENV)
        self._llm.initialize()
        self.category_stats, self.max_rating_number = compute_category_stats(self._db.engine)
        self._loaded = True
        logger.info("Model manager fully loaded.")

    def _load_label_encoder(self):
        """Delegate to LabelEncoderLoader (kept for test compatibility)."""
        self._label_encoder_loader.load(APIConfig.LABEL_ENCODER_PATH)

    def _init_llm_provider(self):
        """Delegate to LLMManager (kept for test compatibility)."""
        self._llm.initialize()

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
                proba = self._model_loader.predict_proba_for_pyfunc(features)
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

    # --- Health ---

    def check_db_connection(self) -> bool:
        return self._db.check_connection()

    def get_llm_provider_name(self) -> str:
        return self._llm.provider_name


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

model_manager = ModelManager()

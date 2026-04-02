"""
Model Loader — Loads champion XGBoost model, label encoder, and DB engine.

Manages the lifecycle of all resources needed for inference:
    - XGBoost model (loaded from local artifacts)
    - LabelEncoder (for decoding integer predictions to GREEN/YELLOW/RED)
    - DB engine (for financial profile + product lookups)
    - LLM provider (for intent parsing + response generation)
    - Category stats (pre-computed for product feature calculation)

Usage:
    from deployment.api.model_loader import model_manager

    # At startup:
    model_manager.load()

    # During inference:
    label, confidence = model_manager.predict(features_array)  # confidence may be None
"""

from __future__ import annotations

import logging
import os
import sys

import joblib
import numpy as np

from deployment.api.config import APIConfig

logger = logging.getLogger(__name__)


def _max_class_probability(proba) -> float:
    """Best-effort max softmax probability across classes.

    sklearn uses shape (n_samples, n_classes). Some paths return a 1-D (n_classes,)
    vector for a single row — using only ``proba[0]`` would read *one class* and can
    wrongly report 0.0 when that class has zero mass.
    """
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


class ModelManager:
    """Singleton that holds all resources needed for inference.

    Call load() once at startup. After that, the instance provides
    predict(), and exposes model, label_encoder, db_engine, llm_provider,
    category_stats, and max_rating_number for use by the inference orchestrator.
    """

    def __init__(self):
        self.model = None
        self.label_encoder = None
        self.feature_pipeline = None
        self.db_engine = None
        self.llm_provider = None
        self.category_stats: dict = {}
        self.max_rating_number: float = 0.0
        self._loaded = False
        #: Directory passed to ``mlflow.pyfunc.load_model`` (contains MLmodel) — used to reload
        #: the native xgboost/lightgbm flavor for ``predict_proba`` (pyfunc does not expose it).
        self._mlflow_model_uri: str | None = None
        self._native_classifier = None

    @property
    def is_loaded(self) -> bool:
        return self._loaded

    # ------------------------------------------------------------------
    # Loading
    # ------------------------------------------------------------------

    def load(self):
        """Load all resources. Call once at API startup."""
        logger.info("Loading model manager resources...")
        self._load_model()
        self._load_label_encoder()
        self._load_feature_pipeline()
        self._init_db()
        self._init_llm_provider()
        self._precompute_category_stats()
        self._loaded = True
        logger.info("Model manager fully loaded.")

    def _load_model(self):
        """Load the champion XGBoost model from local artifacts."""
        artifact_dir = APIConfig.MODEL_ARTIFACT_DIR
        logger.info("Loading model from: %s", artifact_dir)

        if not os.path.exists(artifact_dir):
            logger.warning(
                "Model artifact directory not found: %s — "
                "predict() will fail until a model is loaded.",
                artifact_dir,
            )
            return

        # The MLflow XGBoost flavor saves as model.xgb or via mlflow.pyfunc.
        # Try mlflow.pyfunc first (most reliable), then direct xgboost load.
        try:
            import mlflow.pyfunc

            # Walk the artifact dir to find the MLmodel file
            mlmodel_dirs = []
            for root, dirs, files in os.walk(artifact_dir):
                if "MLmodel" in files:
                    mlmodel_dirs.append(root)

            if mlmodel_dirs:
                model_path = mlmodel_dirs[0]
                self.model = mlflow.pyfunc.load_model(model_path)
                self._mlflow_model_uri = model_path
                logger.info("Loaded model via mlflow.pyfunc from: %s", model_path)
                # Eager-load native xgboost/lightgbm estimator for predict_proba (pyfunc wrapper has no proba).
                self._load_native_classifier_with_predict_proba()
                return

        except Exception as e:
            logger.warning("mlflow.pyfunc load failed: %s — trying xgboost direct load", e)

        # Fallback: try loading XGBoost model directly
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

    def _load_feature_pipeline(self):
        """Load the fitted sklearn feature pipeline saved during training."""
        pipeline_path = APIConfig.FEATURE_PIPELINE_PATH
        logger.info("Loading feature pipeline from: %s", pipeline_path)

        if not os.path.exists(pipeline_path):
            logger.warning(
                "Feature pipeline not found at %s — ML model scoring will be unavailable.",
                pipeline_path,
            )
            return

        try:
            self.feature_pipeline = joblib.load(pipeline_path)
            logger.info("Feature pipeline loaded: %s", type(self.feature_pipeline).__name__)
        except Exception as e:
            logger.error("Failed to load feature pipeline: %s", e, exc_info=True)

    def _load_label_encoder(self):
        """Load the label encoder for decoding predictions."""
        encoder_path = APIConfig.LABEL_ENCODER_PATH
        logger.info("Loading label encoder from: %s", encoder_path)

        if not os.path.exists(encoder_path):
            logger.warning(
                "Label encoder not found at %s — using default GREEN/YELLOW/RED mapping.",
                encoder_path,
            )
            # Provide a default label encoder that matches training
            from sklearn.preprocessing import LabelEncoder
            self.label_encoder = LabelEncoder()
            self.label_encoder.fit(["GREEN", "RED", "YELLOW"])
            return

        try:
            self.label_encoder = joblib.load(encoder_path)
            logger.info(
                "Label encoder loaded. Classes: %s",
                list(self.label_encoder.classes_),
            )
        except Exception as e:
            logger.error("Failed to load label encoder: %s", e, exc_info=True)
            from sklearn.preprocessing import LabelEncoder
            self.label_encoder = LabelEncoder()
            self.label_encoder.fit(["GREEN", "RED", "YELLOW"])

    def _init_db(self):
        """Initialize the database engine via savviocore."""
        try:
            from savviocore.database.db_connection import get_engine

            self.db_engine = get_engine(env=APIConfig.DB_ENV)
            # Quick connectivity check
            from sqlalchemy import text
            with self.db_engine.connect() as conn:
                conn.execute(text("SELECT 1"))
            logger.info("Database connected (env=%s)", APIConfig.DB_ENV)

        except Exception as e:
            logger.warning("Database initialization failed: %s — DB-dependent features will be unavailable.", e)
            self.db_engine = None

    def _init_llm_provider(self):
        """Initialize the LLM provider."""
        try:
            from llm.llm_provider import get_provider
            self.llm_provider = get_provider()
            logger.info("LLM provider initialized: %s", self.llm_provider.provider_name)
        except Exception as e:
            logger.warning("LLM provider initialization failed: %s — using mock.", e)
            from llm.llm_provider import MockProvider
            self.llm_provider = MockProvider()

    def _precompute_category_stats(self):
        """Pre-compute category statistics needed for product feature calculation."""
        if self.db_engine is None:
            logger.warning("Skipping category stats — no DB connection.")
            return

        try:
            import pandas as pd
            from features.product_features import compute_category_stats

            products_df = pd.read_sql(
                "SELECT product_id, price, average_rating, rating_number, rating_variance, category FROM products",
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

    # ------------------------------------------------------------------
    # Inference
    # ------------------------------------------------------------------

    def _load_native_classifier_with_predict_proba(self):
        """Reload the same artifact via mlflow.xgboost / lightgbm flavor to get predict_proba.

        ``mlflow.pyfunc.load_model`` wraps the estimator for ``predict`` only; training
        logs models with ``mlflow.xgboost.log_model`` / ``mlflow.lightgbm.log_model``,
        which expose ``predict_proba`` when loaded with the matching flavor.
        """
        if self._native_classifier is not None:
            return self._native_classifier
        if not self._mlflow_model_uri:
            return None
        uri = self._mlflow_model_uri
        try:
            import mlflow.xgboost

            m = mlflow.xgboost.load_model(uri)
            if hasattr(m, "predict_proba"):
                self._native_classifier = m
                logger.info("Native XGBoost model available for predict_proba (%s)", uri)
                return m
        except Exception as e:
            logger.debug("mlflow.xgboost.load_model(%s): %s", uri, e)
        try:
            import mlflow.lightgbm

            m = mlflow.lightgbm.load_model(uri)
            if hasattr(m, "predict_proba"):
                self._native_classifier = m
                logger.info("Native LightGBM model available for predict_proba (%s)", uri)
                return m
        except Exception as e:
            logger.debug("mlflow.lightgbm.load_model(%s): %s", uri, e)
        return None

    def _predict_proba_for_pyfunc_model(self, features_df):
        """Class probabilities for pyfunc-loaded models; ``None`` if unavailable."""
        native = self._load_native_classifier_with_predict_proba()
        if native is not None:
            try:
                return native.predict_proba(features_df)
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

    def predict(self, features) -> tuple[str, float | None]:
        """Run the ML model and return (predicted_label, confidence).

        Args:
            features: Feature array or DataFrame row ready for model.predict().

        Returns:
            Tuple of (label_string, confidence_float | None).
            confidence_float is the max class probability when scoring succeeds; None if
            the model cannot produce a probability (caller should treat as ML unavailable).
        """
        if self.model is None:
            logger.warning("No model loaded — returning GREEN with no ML confidence.")
            return "GREEN", None

        try:
            # Handle mlflow.pyfunc models (return DataFrame) vs raw XGBClassifier
            if hasattr(self.model, "predict_proba"):
                pred = self.model.predict(features)
                proba = self.model.predict_proba(features)
            elif hasattr(self.model, "_model_impl"):
                # MLflow pyfunc: predict via wrapper; proba via native xgboost/lightgbm reload
                import pandas as pd

                if not isinstance(features, pd.DataFrame):
                    features = pd.DataFrame(features)
                pred_raw = self.model.predict(features)
                pred = pred_raw.values if hasattr(pred_raw, "values") else pred_raw
                proba = self._predict_proba_for_pyfunc_model(features)
            else:
                logger.warning("Model type not recognized — returning GREEN with no confidence.")
                return "GREEN", None

            # Decode integer prediction to label string
            pred_int = int(pred[0]) if hasattr(pred[0], "__int__") else pred[0]
            if isinstance(pred_int, (int, np.integer)):
                label = self.label_encoder.inverse_transform([pred_int])[0]
            else:
                label = str(pred_int)

            if proba is None:
                confidence = None
            else:
                confidence = _max_class_probability(proba)
            return label, confidence

        except Exception as e:
            logger.error("Prediction failed: %s", e, exc_info=True)
            return "GREEN", None

    # ------------------------------------------------------------------
    # Health Check Helpers
    # ------------------------------------------------------------------

    def check_db_connection(self) -> bool:
        """Return True if the database is reachable."""
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
        """Return the name of the active LLM provider."""
        if self.llm_provider is None:
            return "none"
        return self.llm_provider.provider_name


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

model_manager = ModelManager()

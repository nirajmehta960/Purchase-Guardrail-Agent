"""
SavVio Model Wrapper — Bundles classifier + feature pipeline + label encoder
into a single MLflow pyfunc artifact.

The API loads one artifact and calls model.predict(raw_df) to get
label + confidence. No separate pipeline or encoder loading needed.
"""

import joblib
import pandas as pd
import mlflow.pyfunc


class SavVioModelWrapper(mlflow.pyfunc.PythonModel):
    """MLflow pyfunc wrapper that packages preprocessing + prediction + label decoding."""

    def load_context(self, context):
        """Called once when the artifact is loaded (API startup)."""
        self.pipeline = joblib.load(context.artifacts["feature_pipeline"])
        self.encoder = joblib.load(context.artifacts["label_encoder"])
        self.model = joblib.load(context.artifacts["classifier"])

    def predict(self, context, model_input, params=None):
        """Raw features in, label + confidence out.

        Args:
            model_input: DataFrame with raw feature columns (unscaled, strings included).

        Returns:
            DataFrame with columns: label (GREEN/YELLOW/RED), confidence (0-1).
        """
        X = self.pipeline.transform(model_input)
        pred = self.model.predict(X)
        proba = self.model.predict_proba(X)
        return pd.DataFrame({
            "label": self.encoder.inverse_transform(pred),
            "confidence": proba.max(axis=1),
        })

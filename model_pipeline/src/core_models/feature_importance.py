"""
SHAP-based Feature Importance Analysis for the SavVio Model Pipeline.

Complements ``sensitivity_analysis.py`` (which covers *hyperparameter* sensitivity)
by providing per-feature explainability for the champion model. Satisfies the
"Feature Importance Analysis (SHAP / LIME)" requirement of the model-pipeline spec.

What this module produces (under ``reports/explainability/``):
    - ``{model_name}_shap_global_importance.json`` — ranked mean(|SHAP|) per feature
    - ``{model_name}_shap_global_bar.png``        — global importance bar chart
    - ``{model_name}_shap_summary_beeswarm.png``  — beeswarm summary (binary or multi-class)
    - ``{model_name}_shap_per_class_importance.json`` (multi-class only)

All artifacts are also logged to the active MLflow run so they appear under the
final-evaluation run alongside confusion matrices, ROC curves, etc.

Public entrypoint:
    explain_with_shap(model, X, feature_names, model_name, class_names, output_dir)

Design notes:
    - Uses ``shap.TreeExplainer`` which is exact and very fast for XGBoost,
      LightGBM, and Fairlearn ThresholdOptimizer wrappers around tree models.
    - Falls back gracefully (returns a ``status: skipped`` dict) for unsupported
      estimators or when SHAP is unavailable, so the main pipeline never fails
      because of explainability.
    - Subsamples ``X`` to ``Config.EXPLAINABILITY_MAX_SAMPLES`` rows to bound
      runtime on large held-out sets.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _unwrap_estimator(model: Any) -> Any:
    """Return the underlying tree estimator if ``model`` is a Fairlearn wrapper.

    ``ThresholdOptimizer`` (used by the bias-mitigation step) keeps the wrapped
    classifier on its ``.estimator_`` attribute. SHAP needs the raw tree model
    to use ``TreeExplainer``.
    """
    for attr in ("estimator_", "estimator", "predictor"):
        if hasattr(model, attr):
            inner = getattr(model, attr)
            if inner is not None and hasattr(inner, "predict"):
                return inner
    return model


def _is_tree_model(estimator: Any) -> bool:
    """Cheap duck-type check for tree-based boosters supported by TreeExplainer."""
    module_name = type(estimator).__module__.lower()
    return any(token in module_name for token in ("xgboost", "lightgbm", "sklearn.tree", "sklearn.ensemble"))


def _subsample(X: pd.DataFrame, max_rows: int, seed: int = 42) -> pd.DataFrame:
    if len(X) <= max_rows:
        return X
    return X.sample(n=max_rows, random_state=seed)


def _to_dataframe(X: Any, feature_names: Optional[List[str]]) -> pd.DataFrame:
    if isinstance(X, pd.DataFrame):
        return X
    if feature_names is None:
        feature_names = [f"f{i}" for i in range(np.asarray(X).shape[1])]
    return pd.DataFrame(X, columns=feature_names)


def _mean_abs_per_feature(shap_values: np.ndarray) -> np.ndarray:
    """Return mean |SHAP| per feature, collapsing class axis if present."""
    arr = np.asarray(shap_values)
    if arr.ndim == 3:
        # (n_samples, n_features, n_classes) — average across both samples and classes.
        return np.abs(arr).mean(axis=(0, 2))
    if arr.ndim == 2:
        return np.abs(arr).mean(axis=0)
    raise ValueError(f"Unexpected SHAP value shape: {arr.shape}")


# ---------------------------------------------------------------------------
# Plot helpers
# ---------------------------------------------------------------------------

def _plot_global_bar(
    importances: List[Dict[str, Any]],
    model_name: str,
    output_path: str,
    top_k: int,
) -> None:
    top = importances[:top_k]
    feature_names = [item["feature"] for item in top][::-1]
    values = [item["mean_abs_shap"] for item in top][::-1]

    fig, ax = plt.subplots(figsize=(8, max(4, 0.35 * len(top))))
    ax.barh(feature_names, values, color="#4C72B0")
    ax.set_xlabel("mean(|SHAP value|)")
    ax.set_title(f"Global Feature Importance — {model_name} (top {len(top)})")
    fig.tight_layout()
    fig.savefig(output_path, dpi=150, bbox_inches="tight")
    plt.close(fig)


def _plot_beeswarm(
    shap, shap_values_obj, X_sample: pd.DataFrame, model_name: str, output_path: str, top_k: int,
) -> None:
    """Beeswarm summary plot. Falls back silently if SHAP version differs."""
    try:
        plt.figure(figsize=(8, max(4, 0.35 * top_k)))
        shap.summary_plot(
            shap_values_obj,
            X_sample,
            max_display=top_k,
            show=False,
        )
        plt.title(f"SHAP Summary — {model_name}")
        plt.tight_layout()
        plt.savefig(output_path, dpi=150, bbox_inches="tight")
        plt.close()
    except Exception as exc:  # pragma: no cover - plot fallback
        logger.warning("SHAP beeswarm plot failed (non-fatal): %s", exc)


# ---------------------------------------------------------------------------
# Public entrypoint
# ---------------------------------------------------------------------------

def explain_with_shap(
    model: Any,
    X: Any,
    feature_names: Optional[List[str]],
    model_name: str,
    class_names: Optional[List[str]],
    output_dir: str,
    max_samples: int = 1000,
    top_k_features: int = 15,
    log_to_mlflow: bool = True,
) -> Dict[str, Any]:
    """
    Compute SHAP feature importance for ``model`` on ``X`` and persist artifacts.

    Returns a summary dict with keys:
        status            — "ok" | "skipped"
        reason            — present when skipped
        sample_size       — rows actually fed to SHAP
        top_features      — list of {"feature", "mean_abs_shap"} (ranked desc)
        artifacts         — list of artifact paths written under ``output_dir``
    """
    # Lazy import so the pipeline still runs in environments where SHAP is missing.
    try:
        import shap  # type: ignore
    except ImportError:
        logger.warning("shap is not installed — skipping feature-importance analysis.")
        return {"status": "skipped", "reason": "shap_not_installed", "artifacts": []}

    estimator = _unwrap_estimator(model)
    if not _is_tree_model(estimator):
        logger.warning(
            "Estimator %s is not a supported tree model — skipping SHAP.",
            type(estimator).__name__,
        )
        return {
            "status": "skipped",
            "reason": "non_tree_estimator",
            "estimator": type(estimator).__name__,
            "artifacts": [],
        }

    os.makedirs(output_dir, exist_ok=True)

    X_df = _to_dataframe(X, feature_names)
    X_sample = _subsample(X_df, max_samples)
    feature_list = list(X_df.columns)

    # Compute SHAP values via the new Explainer API (works for tree models).
    try:
        explainer = shap.TreeExplainer(estimator)
        shap_values_obj = explainer(X_sample)
    except Exception as exc:
        logger.warning("SHAP TreeExplainer failed (%s) — skipping feature importance.", exc)
        return {"status": "skipped", "reason": "explainer_error", "artifacts": []}

    raw_values = getattr(shap_values_obj, "values", shap_values_obj)
    mean_abs = _mean_abs_per_feature(raw_values)

    ranked = sorted(
        [{"feature": name, "mean_abs_shap": float(val)} for name, val in zip(feature_list, mean_abs)],
        key=lambda item: item["mean_abs_shap"],
        reverse=True,
    )

    artifacts: List[str] = []

    # 1) Global importance JSON.
    importance_json_path = os.path.join(output_dir, f"{model_name}_shap_global_importance.json")
    with open(importance_json_path, "w", encoding="utf-8") as f:
        json.dump(
            {
                "model_name": model_name,
                "sample_size": int(len(X_sample)),
                "feature_count": len(feature_list),
                "ranked_features": ranked,
            },
            f,
            indent=2,
        )
    artifacts.append(importance_json_path)

    # 2) Global bar plot.
    bar_path = os.path.join(output_dir, f"{model_name}_shap_global_bar.png")
    _plot_global_bar(ranked, model_name, bar_path, top_k_features)
    artifacts.append(bar_path)

    # 3) Beeswarm summary.
    beeswarm_path = os.path.join(output_dir, f"{model_name}_shap_summary_beeswarm.png")
    _plot_beeswarm(shap, shap_values_obj, X_sample, model_name, beeswarm_path, top_k_features)
    if os.path.exists(beeswarm_path):
        artifacts.append(beeswarm_path)

    # 4) Per-class importance for multi-class problems.
    arr = np.asarray(raw_values)
    if arr.ndim == 3 and class_names:
        per_class: Dict[str, List[Dict[str, Any]]] = {}
        # arr shape: (n_samples, n_features, n_classes)
        for class_idx, class_name in enumerate(class_names):
            class_mean = np.abs(arr[:, :, class_idx]).mean(axis=0)
            per_class[class_name] = sorted(
                [{"feature": name, "mean_abs_shap": float(val)} for name, val in zip(feature_list, class_mean)],
                key=lambda item: item["mean_abs_shap"],
                reverse=True,
            )[:top_k_features]
        per_class_path = os.path.join(output_dir, f"{model_name}_shap_per_class_importance.json")
        with open(per_class_path, "w", encoding="utf-8") as f:
            json.dump(per_class, f, indent=2)
        artifacts.append(per_class_path)

    # 5) Log everything to MLflow under an "explainability" subfolder.
    if log_to_mlflow:
        try:
            import mlflow

            if mlflow.active_run() is not None:
                for path in artifacts:
                    mlflow.log_artifact(path, artifact_path="explainability")
                mlflow.log_metric("shap_top_feature_importance", ranked[0]["mean_abs_shap"] if ranked else 0.0)
                mlflow.log_param("shap_sample_size", int(len(X_sample)))
        except Exception as exc:  # pragma: no cover - MLflow is best-effort
            logger.warning("Failed to log SHAP artifacts to MLflow: %s", exc)

    logger.info(
        "SHAP feature importance complete for %s — top feature: %s (mean|SHAP|=%.6f)",
        model_name,
        ranked[0]["feature"] if ranked else "n/a",
        ranked[0]["mean_abs_shap"] if ranked else 0.0,
    )

    return {
        "status": "ok",
        "model_name": model_name,
        "sample_size": int(len(X_sample)),
        "feature_count": len(feature_list),
        "top_features": ranked[:top_k_features],
        "artifacts": artifacts,
    }

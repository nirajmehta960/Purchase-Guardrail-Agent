"""
Model Pipeline Prometheus Metrics — push-based via Pushgateway.

Called once at the end of run_pipeline.py main() (inside a try/finally so
metrics are pushed even on failure). Mirrors the pattern from
deployment/api/metrics.py but uses push_to_gateway for batch push.
"""

from __future__ import annotations

import logging
import os
from typing import Any

logger = logging.getLogger(__name__)

_PREFIX = "savvio_mp"
PUSHGATEWAY_URL = os.getenv("PUSHGATEWAY_URL", "")
METRICS_ENABLED = (
    os.getenv("MP_METRICS_ENABLED", "true").lower() == "true"
    and bool(PUSHGATEWAY_URL)
)

_JOB = "savvio-model-pipeline"


def push_pipeline_metrics(
    pipeline_duration_seconds: float,
    pipeline_success: int,
    data_split: dict[str, int],
    candidates: list[dict[str, Any]],
    tuning_result: dict[str, Any] | None,
    final_metrics: dict[str, float] | None,
    champion_name: str | None,
    champion_bias_passed: bool,
    registry_push_success: int,
    run_id: str,
) -> None:
    """Push all model pipeline metrics to the Pushgateway in a single batch.

    Args:
        pipeline_duration_seconds:  Total wall-clock time for main().
        pipeline_success:           1 if main() completed without exception.
        data_split:                 {"train": N, "val": N, "test": N} row counts.
        candidates:                 List of candidate dicts from train_candidates().
                                    Each must have: name, metrics (dict with f1_score,
                                    accuracy), bias_passed.
        tuning_result:              None or dict with model_type, best_f1, trial_count.
        final_metrics:              None or dict from evaluate_model() with f1_score,
                                    accuracy, roc_auc, pr_auc.
        champion_name:              Name of the selected champion model (or None).
        champion_bias_passed:       Whether the champion passed its bias gate.
        registry_push_success:      1 if MLflow registry push succeeded.
        run_id:                     GitHub Actions GITHUB_RUN_ID or "local".
    """
    if not METRICS_ENABLED:
        logger.info(
            "MP metrics disabled (PUSHGATEWAY_URL not set or MP_METRICS_ENABLED=false)"
        )
        return

    try:
        from prometheus_client import CollectorRegistry, Gauge, push_to_gateway
    except ImportError:
        logger.warning("prometheus_client not installed — skipping model pipeline metrics")
        return

    registry = CollectorRegistry(auto_describe=True)
    grouping_key = {"run_id": run_id}

    def _g(suffix: str, help_str: str, labelnames: list[str] | None = None) -> Gauge:
        return Gauge(
            f"{_PREFIX}_{suffix}",
            help_str,
            labelnames=labelnames or [],
            registry=registry,
        )

    # Pipeline-level
    _g("pipeline_duration_seconds", "Total model pipeline wall-clock time in seconds").set(
        pipeline_duration_seconds
    )
    _g("pipeline_success", "1 if model pipeline main() completed without exception").set(
        pipeline_success
    )

    # Data split row counts
    if data_split:
        split_g = _g("data_split_rows", "Row count per data split", ["split"])
        for split_name, count in data_split.items():
            split_g.labels(split=split_name).set(count)

    # Per-candidate baseline metrics (excludes tuned candidate — that is covered by
    # tuning_result)
    if candidates:
        f1_g = _g("baseline_val_f1", "Validation F1 per baseline candidate", ["model"])
        acc_g = _g("baseline_val_accuracy", "Validation accuracy per baseline candidate", ["model"])
        bias_g = _g("baseline_bias_passed", "Bias gate result per baseline candidate (1=passed)", ["model"])
        for c in candidates:
            model_name = c.get("name", "unknown")
            metrics = c.get("metrics") or {}
            f1_g.labels(model=model_name).set(metrics.get("f1_score") or 0)
            acc_g.labels(model=model_name).set(metrics.get("accuracy") or 0)
            bias_g.labels(model=model_name).set(int(bool(c.get("bias_passed", True))))

    # Hyperparameter tuning result
    if tuning_result:
        model_type = tuning_result.get("model_type", "unknown")
        _g("tuning_best_f1", "Optuna best validation F1", ["model"]).labels(
            model=model_type
        ).set(tuning_result.get("best_f1") or 0)
        _g("tuning_trials_completed", "Number of completed Optuna trials", ["model"]).labels(
            model=model_type
        ).set(tuning_result.get("trial_count") or 0)

    # Champion test-set metrics
    if final_metrics and champion_name:
        champ_g = {
            key: _g(f"champion_test_{key}", f"Champion held-out test {key}", ["model"])
            for key in ("f1_score", "accuracy", "roc_auc", "pr_auc")
        }
        for key, gauge in champ_g.items():
            gauge.labels(model=champion_name).set(final_metrics.get(key) or 0)

    _g(
        "champion_bias_passed",
        "1 if the selected champion model passed the bias gate",
    ).set(int(champion_bias_passed))

    _g(
        "registry_push_success",
        "1 if the champion model was successfully pushed to MLflow registry",
    ).set(registry_push_success)

    try:
        push_to_gateway(
            PUSHGATEWAY_URL, job=_JOB, registry=registry, grouping_key=grouping_key
        )
        logger.info("Model pipeline metrics pushed to Pushgateway (run_id=%s)", run_id)
    except Exception as exc:
        logger.warning("Failed to push model pipeline metrics: %s", exc)

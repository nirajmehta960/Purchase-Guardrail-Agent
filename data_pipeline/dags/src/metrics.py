"""
Data Pipeline Prometheus Metrics — push-based via Pushgateway.

Each Airflow task calls push_stage_metrics() or uses timed_stage() to record
duration and success/failure. Metrics are pushed to a Prometheus Pushgateway
rather than scraped, since Airflow tasks are short-lived batch processes.

Mirrors the pattern in deployment/api/metrics.py but uses push_to_gateway
instead of a scrape endpoint.
"""

from __future__ import annotations

import logging
import os
import time
from contextlib import contextmanager
from typing import Any

logger = logging.getLogger(__name__)

_PREFIX = "savvio_dp"
PUSHGATEWAY_URL = os.getenv("PUSHGATEWAY_URL", "")
METRICS_ENABLED = os.getenv("DP_METRICS_ENABLED", "true").lower() == "true" and bool(PUSHGATEWAY_URL)

_JOB = "savvio-data-pipeline"


def _get_run_id(context: dict) -> str:
    dag_run = context.get("dag_run")
    if dag_run and hasattr(dag_run, "run_id"):
        return dag_run.run_id
    return os.getenv("AIRFLOW_CTX_DAG_RUN_ID", "unknown")


def push_stage_metrics(
    grouping_key: dict[str, str],
    metrics_dict: dict[str, tuple[float, dict[str, str], str]],
) -> None:
    """Push a batch of Gauge metrics to the Pushgateway.

    Args:
        grouping_key:  Extra labels keyed by the Pushgateway grouping key
                       (e.g. {"dag_run_id": run_id}). Each distinct key
                       creates an isolated metric group that overwrites on
                       the next push.
        metrics_dict:  Mapping of metric_suffix → (value, label_dict, help_str).
                       The full metric name will be f"{_PREFIX}_{suffix}".
    """
    if not METRICS_ENABLED:
        return

    try:
        from prometheus_client import CollectorRegistry, Gauge, push_to_gateway
    except ImportError:
        logger.debug("prometheus_client not installed — skipping metric push")
        return

    # Isolated registry per call so parallel Airflow tasks never collide with
    # each other or with Airflow's own Prometheus instrumentation.
    registry = CollectorRegistry(auto_describe=True)

    for suffix, (value, labels, help_str) in metrics_dict.items():
        full_name = f"{_PREFIX}_{suffix}"
        label_names = list(labels.keys())
        g = Gauge(full_name, help_str, labelnames=label_names, registry=registry)
        if labels:
            g.labels(**labels).set(value)
        else:
            g.set(value)

    try:
        push_to_gateway(PUSHGATEWAY_URL, job=_JOB, registry=registry, grouping_key=grouping_key)
    except Exception as exc:
        logger.warning("Failed to push metrics to Pushgateway (%s): %s", PUSHGATEWAY_URL, exc)


@contextmanager
def timed_stage(
    metric_name: str,
    grouping_key: dict[str, str],
    extra_labels: dict[str, str] | None = None,
):
    """Context manager that times a pipeline stage and pushes duration + success.

    On success: sets <metric_name>_success = 1
    On exception: sets <metric_name>_success = 0, then re-raises.

    Usage:
        with timed_stage("ingestion", {"dag_run_id": run_id}, {"dataset": "financial"}):
            df = load_financial_data(...)
    """
    extra_labels = extra_labels or {}
    start = time.monotonic()
    success = 0
    try:
        yield
        success = 1
    finally:
        duration = time.monotonic() - start
        push_stage_metrics(
            grouping_key=grouping_key,
            metrics_dict={
                f"{metric_name}_duration_seconds": (
                    duration,
                    extra_labels,
                    f"Wall-clock duration of {metric_name} in seconds",
                ),
                f"{metric_name}_success": (
                    success,
                    extra_labels,
                    f"1 if {metric_name} completed without exception",
                ),
            },
        )


def push_validation_metrics(
    report_summary: dict[str, Any],
    grouping_key: dict[str, str],
    duration_seconds: float,
) -> None:
    """Push ValidationReport.summary dict as Prometheus metrics.

    Args:
        report_summary:    The dict returned by ValidationReport.summary.
                           Expected keys: stage, total_checks, passed,
                           failed_critical, failed_warning, failed_info,
                           pipeline_action.
        grouping_key:      Pushgateway grouping key (dag_run_id).
        duration_seconds:  How long the validation stage took.
    """
    _ACTION_MAP = {"CONTINUE": 0, "ALERT": 1, "HALT": 2}
    stage = report_summary.get("stage", "unknown")
    action_val = _ACTION_MAP.get(str(report_summary.get("pipeline_action", "CONTINUE")), 0)

    push_stage_metrics(
        grouping_key=grouping_key,
        metrics_dict={
            "validation_checks_total": (
                report_summary.get("total_checks", 0),
                {"stage": stage},
                "Total validation checks run",
            ),
            "validation_checks_passed": (
                report_summary.get("passed", 0),
                {"stage": stage},
                "Number of validation checks that passed",
            ),
            "validation_checks_failed_critical": (
                report_summary.get("failed_critical", 0),
                {"stage": stage},
                "Number of CRITICAL validation failures",
            ),
            "validation_checks_failed_warning": (
                report_summary.get("failed_warning", 0),
                {"stage": stage},
                "Number of WARNING validation failures",
            ),
            "validation_action": (
                action_val,
                {"stage": stage},
                "Pipeline action after validation: 0=CONTINUE 1=ALERT 2=HALT",
            ),
            "validation_duration_seconds": (
                duration_seconds,
                {"stage": stage},
                "Wall-clock time for validation stage in seconds",
            ),
        },
    )

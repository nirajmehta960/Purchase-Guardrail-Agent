"""
Drift Detection for SavVio Deployed Model.

Compares current production data distributions against training baseline
using Evidently. Raises alerts when drift thresholds are exceeded.

Run on a schedule (daily or weekly) after collecting production predictions.
"""

import logging
import logging.handlers
import os
import json
from datetime import datetime
from pathlib import Path
from typing import Dict, Optional
from dotenv import load_dotenv

import pandas as pd
import requests
import yaml
from evidently.report import Report
from evidently.metric_preset import DataDriftPreset
from evidently.metrics import DatasetDriftMetric

# Attach a NullHandler so this module is silent when imported without
# a logging config in place (the caller decides where logs go).
logging.getLogger(__name__).addHandler(logging.NullHandler())

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Load environment variables and config
# ---------------------------------------------------------------------------

# Load .env from the same directory as this file
_ENV_PATH = Path(__file__).parent / ".env"
load_dotenv(_ENV_PATH)

# Load alert_config.yaml from the same directory as this file
_CONFIG_PATH = Path(__file__).parent / "alert_config.yaml"
with open(_CONFIG_PATH) as f:
    _CONFIG = yaml.safe_load(f)

# ---------------------------------------------------------------------------
# Configuration — loaded from alert_config.yaml
# ---------------------------------------------------------------------------

MONITOR_COLS = (
    _CONFIG["monitored_features"]["financial"]
    + _CONFIG["monitored_features"]["product"]
)

COLUMN_THRESHOLDS: Dict[str, float] = _CONFIG["column_thresholds"]["overrides"]
DEFAULT_STATTEST_THRESHOLD: float = _CONFIG["column_thresholds"]["default"]

DRIFT_THRESHOLDS = _CONFIG["drift_thresholds"]
OUTPUT_SHIFT_THRESHOLD: float = _CONFIG["output_shift_threshold"]

# Notification config
_SLACK_CFG = _CONFIG["notification"]["slack"]
_EMAIL_CFG = _CONFIG["notification"]["email"]


# ---------------------------------------------------------------------------
# Alerting
# ---------------------------------------------------------------------------

def send_slack_alert(summary: Dict) -> None:
    """Send a Slack alert if severity is in the notify_on list."""
    if not _SLACK_CFG.get("enabled"):
        return

    severity = summary["severity"]
    if severity not in _SLACK_CFG.get("notify_on", []):
        return

    webhook_url = os.environ.get(_SLACK_CFG["webhook_env_var"])
    if not webhook_url:
        logger.warning("Slack webhook URL not set — skipping Slack alert.")
        return

    # Build emoji and color based on severity
    emoji = {"GREEN": "✅", "YELLOW": "⚠️", "RED": "🚨"}.get(severity, "ℹ️")

    drifted = summary.get("drifted_columns", {})
    drifted_list = (
        "\n".join(
            f"• `{col}` — score: {info.get('drift_score', 'N/A'):.4f}"
            for col, info in drifted.items()
            if info.get("drift_detected")
        )
        or "None"
    )

    message = {
        "text": (
            f"{emoji} *SavVio Drift Alert — {severity}*\n"
            f"*Action:* {summary['action']}\n"
            f"*Columns drifted:* {summary['n_drifted_cols']}/{summary['n_total_cols']} "
            f"({summary['share_drifted']*100:.1f}%)\n"
            f"*Drifted columns:*\n{drifted_list}\n"
            f"*Time:* {summary['timestamp']}"
        )
    }

    try:
        resp = requests.post(webhook_url, json=message, timeout=10)
        if resp.status_code == 200:
            logger.info("Slack alert sent — severity: %s", severity)
        else:
            logger.warning("Slack alert failed — status %d: %s", resp.status_code, resp.text)
    except Exception as e:
        logger.warning("Slack alert error: %s", e)


# ---------------------------------------------------------------------------
# Core detection function
# ---------------------------------------------------------------------------

def run_drift_detection(
    baseline_path: str,
    current_data_path: str,
    output_dir: str = "deployment/monitoring/reports",
) -> Dict:
    """
    Compare current production data against training baseline.

    Args:
        baseline_path:     Path to baseline_data.csv (training distribution).
        current_data_path: Path to current production data CSV.
        output_dir:        Where to save the drift report HTML and JSON.

    Returns:
        Dict with drift status, severity, and per-column summary.
    """
    logger.info("Loading baseline and current data...")
    baseline = pd.read_csv(baseline_path)
    current  = pd.read_csv(current_data_path)

    # Only monitor columns present in both datasets; warn about any missing ones.
    cols = [c for c in MONITOR_COLS if c in baseline.columns and c in current.columns]
    missing = set(MONITOR_COLS) - set(cols)
    if missing:
        logger.warning(
            "Skipping columns not found in both datasets: %s. "
            "Update MONITOR_COLS or regenerate the baseline to include them.",
            sorted(missing),
        )

    baseline = baseline[cols]
    current  = current[cols]

    logger.info("Running Evidently drift report on %d columns...", len(cols))

    # In Evidently 0.4.x, per_column_stattest takes {col: "stattest_name"}
    # and per_column_stattest_threshold takes {col: float} — separate dicts.
    per_column_stattest = {col: "wasserstein" for col in cols}
    per_column_threshold = {
        col: COLUMN_THRESHOLDS.get(col, DEFAULT_STATTEST_THRESHOLD)
        for col in cols
    }

    report = Report(metrics=[
        DataDriftPreset(
            per_column_stattest=per_column_stattest,
            per_column_stattest_threshold=per_column_threshold,
        ),
        DatasetDriftMetric(),
    ])
    report.run(reference_data=baseline, current_data=current)

    result        = report.as_dict()
    dataset_drift = result["metrics"][1]["result"]

    share_drifted = dataset_drift.get("share_of_drifted_columns", 0.0)
    n_drifted     = dataset_drift.get("number_of_drifted_columns", 0)
    n_total       = dataset_drift.get("number_of_columns", len(cols))

    # Determine severity.
    if share_drifted < DRIFT_THRESHOLDS["green"]:
        severity = "GREEN"
        action   = "No action needed — distributions stable."
    elif share_drifted < DRIFT_THRESHOLDS["yellow"]:
        severity = "YELLOW"
        action   = "Monitor closely — minor drift detected."
    else:
        severity = "RED"
        action   = "Alert — significant drift detected. Consider retraining."

    summary = {
        "timestamp":       datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "severity":        severity,
        "action":          action,
        "share_drifted":   round(share_drifted, 4),
        "n_drifted_cols":  n_drifted,
        "n_total_cols":    n_total,
        "drifted_columns": dataset_drift.get("drift_by_columns", {}),
    }

    # Persist outputs.
    os.makedirs(output_dir, exist_ok=True)
    timestamp    = datetime.now().strftime("%Y%m%d_%H%M%S")
    report_path  = os.path.join(output_dir, f"drift_report_{timestamp}.html")
    summary_path = os.path.join(output_dir, f"drift_summary_{timestamp}.json")

    report.save_html(report_path)
    with open(summary_path, "w") as f:
        json.dump(summary, f, indent=2)

    logger.info("Drift severity: %s — %s", severity, action)
    logger.info("Drift report saved to %s", report_path)
    logger.info("Drift summary saved to %s", summary_path)

    if severity == "RED":
        logger.warning(
            "DRIFT ALERT — %d/%d columns drifted (%.1f%%). Action: %s",
            n_drifted, n_total, share_drifted * 100, action,
        )

    # Send Slack alert
    send_slack_alert(summary)

    return summary


# ---------------------------------------------------------------------------
# Output distribution drift
# ---------------------------------------------------------------------------

def check_output_drift(
    baseline_output_dist: Dict[str, float],
    current_output_dist: Dict[str, float],
) -> Dict:
    """
    Compare GREEN/YELLOW/RED prediction ratios against baseline.

    Args:
        baseline_output_dist: e.g. {"GREEN": 0.55, "YELLOW": 0.25, "RED": 0.20}
        current_output_dist:  e.g. {"GREEN": 0.70, "YELLOW": 0.15, "RED": 0.15}

    Returns:
        Dict with per-label drift status, shift magnitude, and drifted flag.
    """
    results = {}
    for label in ["GREEN", "YELLOW", "RED"]:
        baseline_share = baseline_output_dist.get(label, 0.0)
        current_share  = current_output_dist.get(label, 0.0)
        shift          = abs(current_share - baseline_share)
        drifted        = shift > OUTPUT_SHIFT_THRESHOLD

        results[label] = {
            "baseline_share": round(baseline_share, 4),
            "current_share":  round(current_share, 4),
            "shift":          round(shift, 4),
            "drifted":        drifted,
        }

        if drifted:
            logger.warning(
                "OUTPUT DRIFT: %s label shifted by %.1f%% "
                "(baseline=%.1f%%, current=%.1f%%)",
                label, shift * 100,
                baseline_share * 100,
                current_share * 100,
            )

    return results


# ---------------------------------------------------------------------------
# Entry point for scheduled runs
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    summary = run_drift_detection(
        baseline_path="deployment/monitoring/baseline_data.csv",
        current_data_path="deployment/monitoring/current_production_data.csv",
        output_dir="deployment/monitoring/reports",
    )

    print(f"\nDrift Detection Complete")
    print(f"Severity:        {summary['severity']}")
    print(f"Action:          {summary['action']}")
    print(f"Columns drifted: {summary['n_drifted_cols']}/{summary['n_total_cols']}")

    # Example: wire in output drift once production labels are available.
    # baseline_dist = {"GREEN": 0.55, "YELLOW": 0.25, "RED": 0.20}
    # current_dist  = {"GREEN": 0.70, "YELLOW": 0.15, "RED": 0.15}
    # output_results = check_output_drift(baseline_dist, current_dist)
    # print("\nOutput Distribution Drift:")
    # for label, info in output_results.items():
    #     print(f"  {label}: shift={info['shift']:.3f}, drifted={info['drifted']}")
"""
Drift Detection — Baseline comparison using Evidently AI.
Monitors product and financial feature distributions for statistical shifts.
"""

import logging
import logging.handlers
import os
import json
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from datetime import datetime
from pathlib import Path
from typing import Dict, Optional
from dotenv import load_dotenv

import pandas as pd
import requests
import yaml
from evidently import DataDefinition, Dataset, Report
from evidently.presets import DataDriftPreset

# Suppress logging on library import
logging.getLogger(__name__).addHandler(logging.NullHandler())

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Load environment variables and config
# ---------------------------------------------------------------------------

# Shared with the rest of the monitoring stack.
_ENV_PATH = Path(__file__).parent.parent / ".env"
load_dotenv(_ENV_PATH)

_CONFIG_PATH = Path(__file__).parent / "alert_config.yaml"
with open(_CONFIG_PATH) as f:
    _CONFIG = yaml.safe_load(f)

# ---------------------------------------------------------------------------
# Configuration — loaded from alert_config.yaml
# ---------------------------------------------------------------------------

NUMERIC_MONITOR_COLS = (
    _CONFIG["monitored_features"]["financial"]
    + _CONFIG["monitored_features"]["product"]
)
CATEGORICAL_MONITOR_COLS = _CONFIG["monitored_features"].get("categorical", [])
MONITOR_COLS = NUMERIC_MONITOR_COLS + CATEGORICAL_MONITOR_COLS

COLUMN_THRESHOLDS: Dict[str, float] = _CONFIG["column_thresholds"]["overrides"]
DEFAULT_STATTEST_THRESHOLD: float = _CONFIG["column_thresholds"]["default"]

DRIFT_THRESHOLDS = _CONFIG["drift_thresholds"]
OUTPUT_SHIFT_THRESHOLD: float = _CONFIG["output_shift_threshold"]

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

    emoji = {"GREEN": "[OK]", "YELLOW": "[WARNING]", "RED": "[CRITICAL]"}.get(severity, "[INFO]")

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


def send_email_alert(summary: Dict) -> None:
    """Send an email alert via SMTP if severity is in the notify_on list.

    Required env vars when email is enabled:
      SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASSWORD, ALERT_EMAIL_FROM,
      and the recipients list under the env var named in alert_config.yaml
      (default: ALERT_EMAIL_LIST, comma-separated).
    """
    if not _EMAIL_CFG.get("enabled"):
        return

    severity = summary["severity"]
    if severity not in _EMAIL_CFG.get("notify_on", []):
        return

    recipients_env = _EMAIL_CFG.get("recipients_env_var", "ALERT_EMAIL_LIST")
    recipients_raw = os.environ.get(recipients_env, "")
    recipients = [r.strip() for r in recipients_raw.split(",") if r.strip()]
    if not recipients:
        logger.warning("Email recipients (%s) not set — skipping email alert.", recipients_env)
        return

    smtp_host = os.environ.get("SMTP_HOST")
    smtp_port = int(os.environ.get("SMTP_PORT", "587"))
    smtp_user = os.environ.get("SMTP_USER")
    smtp_pass = os.environ.get("SMTP_PASSWORD")
    sender    = os.environ.get("ALERT_EMAIL_FROM", smtp_user or "")

    if not (smtp_host and smtp_user and smtp_pass and sender):
        logger.warning("SMTP credentials incomplete — skipping email alert.")
        return

    drifted = summary.get("drifted_columns", {})
    drifted_lines = "\n".join(
        f"  - {col}: drift_score={info.get('drift_score', 'N/A')}"
        for col, info in drifted.items()
        if info.get("drift_detected")
    ) or "  (none)"

    body = (
        f"SavVio Drift Alert — {severity}\n"
        f"Action:           {summary['action']}\n"
        f"Columns drifted:  {summary['n_drifted_cols']}/{summary['n_total_cols']} "
        f"({summary['share_drifted']*100:.1f}%)\n"
        f"Time:             {summary['timestamp']}\n\n"
        f"Drifted columns:\n{drifted_lines}\n\n"
        f"This is an automated notification from the SavVio monitoring pipeline.\n"
        f"On RED severity the modelpipeline.yml workflow is auto-dispatched "
        f"to retrain and redeploy the model."
    )

    msg = MIMEMultipart()
    msg["From"]    = sender
    msg["To"]      = ", ".join(recipients)
    msg["Subject"] = f"[SavVio] Drift Alert — {severity} ({summary['n_drifted_cols']}/{summary['n_total_cols']} columns)"
    msg.attach(MIMEText(body, "plain"))

    try:
        with smtplib.SMTP(smtp_host, smtp_port, timeout=15) as server:
            server.starttls()
            server.login(smtp_user, smtp_pass)
            server.sendmail(sender, recipients, msg.as_string())
        logger.info("Email alert sent to %d recipients — severity: %s", len(recipients), severity)
    except Exception as e:
        logger.warning("Email alert error: %s", e)


# ---------------------------------------------------------------------------
# Core detection function
# ---------------------------------------------------------------------------

def run_drift_detection(
    baseline_path: str,
    current_data,
    output_dir: str = str(Path(__file__).parent.parent / "reports"),
) -> Dict:
    """Computes Wasserstein distance for monitored columns vs baseline."""
    logger.info("Loading baseline and current data...")
    baseline = pd.read_csv(baseline_path)
    current  = current_data if isinstance(current_data, pd.DataFrame) else pd.read_csv(current_data)

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

    numeric_cols     = [c for c in cols if c in NUMERIC_MONITOR_COLS]
    categorical_cols = [c for c in cols if c in CATEGORICAL_MONITOR_COLS]

    # Evidently treats integer-typed categorical columns (e.g. has_loan = 0/1)
    # as numeric by default, which would push them through Wasserstein. Cast to
    # string so chi-squared is applied to the actual category frequencies.
    for c in categorical_cols:
        baseline[c] = baseline[c].astype(str)
        current[c]  = current[c].astype(str)

    logger.info(
        "Running Evidently drift report on %d columns (numeric=%d, categorical=%d)...",
        len(cols), len(numeric_cols), len(categorical_cols),
    )

    # Numeric thresholds only apply to wasserstein. chi-squared on categoricals
    # uses Evidently's default p-value cutoff (0.05).
    per_column_threshold = {
        col: COLUMN_THRESHOLDS.get(col, DEFAULT_STATTEST_THRESHOLD)
        for col in numeric_cols
    }

    data_definition = DataDefinition(
        numerical_columns=numeric_cols,
        categorical_columns=categorical_cols,
    )
    baseline_ds = Dataset.from_pandas(baseline, data_definition=data_definition)
    current_ds  = Dataset.from_pandas(current,  data_definition=data_definition)

    report = Report(metrics=[DataDriftPreset(
        num_method="wasserstein",
        cat_method="chisquare",
        per_column_threshold=per_column_threshold,
    )])
    snapshot = report.run(reference_data=baseline_ds, current_data=current_ds)

    # Extract summary counts from metric_results (Evidently 0.7.x API).
    share_drifted = 0.0
    n_drifted     = 0
    drifted_cols  = {}
    for val in snapshot.metric_results.values():
        vtype = type(val).__name__
        if vtype == "CountValue":                   # DriftedColumnsCount
            n_drifted     = int(val.count.value)
            share_drifted = float(val.share.value)
        elif vtype == "SingleValue" and "Value drift for" in val.display_name:
            col_name = val.display_name.replace("Value drift for ", "")
            params   = val.metric_value_location.metric.params
            method   = params.get("method", "wasserstein")
            score    = float(val.value)
            threshold = params.get("threshold", DEFAULT_STATTEST_THRESHOLD)
            # For distance metrics (wasserstein, ks, psi, jensenshannon) higher
            # score = more drift, so we drift if score >= threshold.
            # For chi-squared the score is a p-value, so we drift if p <= threshold.
            if method in {"chisquare", "g_test"}:
                drift_detected = score <= threshold
            else:
                drift_detected = score >= threshold
            drifted_cols[col_name] = {
                "drift_score":    round(score, 6),
                "drift_detected": drift_detected,
                "method":         method,
                "threshold":      threshold,
            }

    n_total = len(cols)

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
        "drifted_columns": drifted_cols,
    }

    os.makedirs(output_dir, exist_ok=True)
    timestamp    = datetime.now().strftime("%Y%m%d_%H%M%S")
    report_path  = os.path.join(output_dir, f"drift_report_{timestamp}.html")
    summary_path = os.path.join(output_dir, f"drift_summary_{timestamp}.json")

    snapshot.save_html(report_path)
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

    # Email-only; flip notification.slack.enabled in alert_config.yaml to use Slack.
    send_email_alert(summary)

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
    import subprocess as _sp
    import sys as _sys
    import tempfile as _tempfile

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    _sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent / "savviocore" / "src"))

    _GCS_BASELINE = os.getenv("DRIFT_BASELINE_GCS_URI")
    if not _GCS_BASELINE:
        print("DRIFT_BASELINE_GCS_URI is not set — skipping drift detection.")
        print("(Set it to the gs:// URI of baseline_data.csv produced by generate_baseline.py.)")
        raise SystemExit(0)

    logger.info("Downloading baseline from GCS: %s", _GCS_BASELINE)
    _tmp_baseline = _tempfile.NamedTemporaryFile(suffix=".csv", delete=False)
    _tmp_baseline.close()
    _dl = _sp.run(
        ["gcloud", "storage", "cp", _GCS_BASELINE, _tmp_baseline.name],
        capture_output=True, text=True,
    )
    if _dl.returncode != 0:
        print(f"Baseline not found in GCS — skipping drift detection.")
        print(f"(Run generate_baseline.py after model training to upload the baseline.)")
        print(f"Details: {_dl.stderr.strip()}")
        raise SystemExit(0)

    logger.info("Collecting current production data from DB...")
    try:
        from collect_production_data import collect_df
        current_df = collect_df()
    except Exception as _e:
        print(f"Could not collect production data from DB: {_e}")
        print("Skipping drift detection — DB unavailable or no data yet.")
        raise SystemExit(0)

    summary = run_drift_detection(
        baseline_path=_tmp_baseline.name,
        current_data=current_df,
    )
    Path(_tmp_baseline.name).unlink(missing_ok=True)

    print(f"\nDrift Detection Complete")
    print(f"Severity:        {summary['severity']}")
    print(f"Action:          {summary['action']}")
    print(f"Columns drifted: {summary['n_drifted_cols']}/{summary['n_total_cols']}")
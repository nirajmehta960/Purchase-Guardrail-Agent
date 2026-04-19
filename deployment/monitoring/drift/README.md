# Drift Detection — SavVio

## What this does
Monitors 8 production features weekly against the training baseline.
Detects when real-world data starts looking different from what the model was trained on.
Sends email alerts on YELLOW (10–50% columns drifted) or RED (50%+),
and auto-triggers retraining on RED.

## Files
| File | Purpose |
|------|---------|
| `drift_detector.py` | Main script — runs drift detection |
| `collect_production_data.py` | Queries production DB, returns DataFrame |
| `generate_baseline.py` | Saves training distribution snapshot to GCS |
| `alert_config.yaml` | All thresholds and email config (Slack disabled) |
| `../reports/` | HTML + JSON drift reports saved here |

## How to run locally
```bash
cd /path/to/SavVio-1
python deployment/monitoring/drift/drift_detector.py
```

## View the HTML report
```bash
open $(ls -t deployment/monitoring/reports/*.html | head -1)
```

## View the JSON summary
```bash
cat $(ls -t deployment/monitoring/reports/*.json | head -1)
```

## Monitored features (8 total)
**Financial (threshold: 0.10)**
- `discretionary_income`
- `debt_to_income_ratio`
- `monthly_expense_burden_ratio`
- `emergency_fund_months`
- `saving_to_income_ratio`

**Product ratings (threshold: 0.05 — tighter because they directly affect recommendations)**
- `average_rating`
- `rating_number`
- `rating_variance`

## Severity levels
| Severity | Condition | Action |
|----------|-----------|--------|
| GREEN | <10% columns drifted | No action needed |
| YELLOW | 10–50% columns drifted | Monitor closely, investigate cause |
| RED | 50%+ columns drifted | Collect new data, retrain model |

## Alerts
- YELLOW → email alert (via SMTP) to `ALERT_EMAIL_LIST`
- RED → email alert **+ auto-dispatch of `modelpipeline_ci.yml` retraining workflow**

## Automated schedule
Runs every Monday 08:00 UTC via GitHub Actions (`ops-monitoring.yml`).
Can also be triggered manually via `workflow_dispatch` on the same workflow.
No manual intervention needed for the regular cadence.

## Drift → retrain → redeploy loop
1. `ops-monitoring.yml :: drift-detection` runs `drift_detector.py` and writes
   `deployment/monitoring/reports/drift_summary_*.json`.
2. The job exposes `severity` as an output.
3. `ops-monitoring.yml :: trigger-retraining` runs only when severity is `RED`
   and dispatches `modelpipeline_ci.yml` via the GitHub API.
4. `modelpipeline_ci.yml` retrains, runs the validation/bias/rollback gates,
   persists the new metrics baseline, and dispatches `deployment.yml` to push
   the new image to Cloud Run.

## Required secrets for alerting (email-only)
Slack alerting is disabled in `alert_config.yaml` — the project uses email
notifications only. To re-enable Slack later, flip `notification.slack.enabled`
to `true` and add `SLACK_WEBHOOK_URL` back to `ops-monitoring.yml`.

| Secret | Purpose |
|--------|---------|
| `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASSWORD` | SMTP relay for email |
| `ALERT_EMAIL_FROM` | From address used in email alerts |
| `ALERT_EMAIL_LIST` | Comma-separated recipients (YELLOW + RED) |

## Last run result
- Severity: YELLOW
- Columns drifted: 3/8
- Report: `../reports/drift_report_20260413_172131.html`
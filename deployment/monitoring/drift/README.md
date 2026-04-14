# Drift Detection — SavVio

## What this does
Monitors 8 production features weekly against the training baseline.
Detects when real-world data starts looking different from what the model was trained on.
Sends Slack alerts on YELLOW (10–50% columns drifted) or RED (50%+).

## Files
| File | Purpose |
|------|---------|
| `drift_detector.py` | Main script — runs drift detection |
| `collect_production_data.py` | Queries production DB, returns DataFrame |
| `generate_baseline.py` | Saves training distribution snapshot to GCS |
| `alert_config.yaml` | All thresholds and Slack/email config |
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
- YELLOW → Slack alert to #mlops channel
- RED → Slack alert + email alert

## Automated schedule
Runs every Monday 8am UTC via GitHub Actions (`deployment_ci.yml`).
No manual intervention needed.

## Last run result
- Severity: YELLOW
- Columns drifted: 3/8
- Report: `../reports/drift_report_20260413_172131.html`
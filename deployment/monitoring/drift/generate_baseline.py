"""
Generate baseline_data.csv for drift detection.

Reads the training dataset and extracts the monitored feature columns,
saving a sample to deployment/monitoring/baseline_data.csv.

Run once at setup, or whenever the model is retrained on new data:

    python deployment/monitoring/drift/generate_baseline.py

Output: deployment/monitoring/baseline_data.csv
"""

import sys
from pathlib import Path

import pandas as pd
import yaml

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
_REPO_ROOT     = Path(__file__).parent.parent.parent.parent   # SavVio/
_MONITORING    = Path(__file__).parent.parent                  # deployment/monitoring/
_CONFIG_PATH   = Path(__file__).parent / "alert_config.yaml"
_TRAINING_DATA = _REPO_ROOT / "model_pipeline" / "data" / "training_scenarios.csv"
_OUTPUT        = _MONITORING / "data" / "baseline_data.csv"

# ---------------------------------------------------------------------------
# Load monitored feature list from shared config
# ---------------------------------------------------------------------------
with open(_CONFIG_PATH) as f:
    _cfg = yaml.safe_load(f)

MONITOR_COLS = (
    _cfg["monitored_features"]["financial"]
    + _cfg["monitored_features"]["product"]
)

# ---------------------------------------------------------------------------
# Generate baseline
# ---------------------------------------------------------------------------
def generate(sample_size: int = 10_000) -> None:
    if not _TRAINING_DATA.exists():
        print(f"ERROR: Training data not found at {_TRAINING_DATA}")
        print("Check that model_pipeline/data/training_scenarios.csv exists.")
        sys.exit(1)

    df = pd.read_csv(_TRAINING_DATA)

    missing = [c for c in MONITOR_COLS if c not in df.columns]
    if missing:
        print(f"WARNING: These monitored columns are missing from training data: {missing}")
        print("They will be skipped in the baseline.")

    cols = [c for c in MONITOR_COLS if c in df.columns]
    baseline = df[cols].dropna()

    if len(baseline) > sample_size:
        baseline = baseline.sample(n=sample_size, random_state=42)

    _OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    baseline.to_csv(_OUTPUT, index=False)
    print(f"Baseline saved: {_OUTPUT}  ({len(baseline):,} rows, {len(cols)} columns)")
    print(f"Columns: {cols}")


if __name__ == "__main__":
    generate()

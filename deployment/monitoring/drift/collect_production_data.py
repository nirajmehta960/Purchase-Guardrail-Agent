"""
Collect current production data for drift detection.

Queries the production database (financial_profiles + products tables) and
saves a sample to deployment/monitoring/current_production_data.csv.

Run periodically (e.g. weekly) before running the drift detector:

    python deployment/monitoring/drift/collect_production_data.py

Then run drift detection:

    python deployment/monitoring/drift/drift_detector.py

Output: deployment/monitoring/current_production_data.csv
"""

import sys
from pathlib import Path

import pandas as pd
import yaml

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
_MONITORING  = Path(__file__).parent.parent     # deployment/monitoring/
_CONFIG_PATH = Path(__file__).parent / "alert_config.yaml"
_OUTPUT      = _MONITORING / "data" / "current_production_data.csv"

# Add savviocore to path so get_engine is importable
_REPO_ROOT = Path(__file__).parent.parent.parent.parent
sys.path.insert(0, str(_REPO_ROOT / "savviocore" / "src"))

# ---------------------------------------------------------------------------
# Load monitored feature list from shared config
# ---------------------------------------------------------------------------
with open(_CONFIG_PATH) as f:
    _cfg = yaml.safe_load(f)

FINANCIAL_COLS = _cfg["monitored_features"]["financial"]
PRODUCT_COLS   = _cfg["monitored_features"]["product"]
MONITOR_COLS   = FINANCIAL_COLS + PRODUCT_COLS

# ---------------------------------------------------------------------------
# Collect
# ---------------------------------------------------------------------------
def collect(sample_size: int = 10_000) -> None:
    try:
        from savviocore.database.db_connection import get_engine
    except ImportError as e:
        print(f"ERROR: Cannot import database connection: {e}")
        print("Make sure you have activated the correct venv and savviocore is installed.")
        sys.exit(1)

    engine = get_engine()

    # Financial features — one row per user (already computed columns in DB)
    fin_cols = ", ".join(FINANCIAL_COLS)
    financial_df = pd.read_sql(
        f"SELECT {fin_cols} FROM financial_profiles",
        engine,
    )

    # Product features — one row per product
    prod_cols = ", ".join(PRODUCT_COLS)
    products_df = pd.read_sql(
        f"SELECT {prod_cols} FROM products",
        engine,
    )

    # Build a cross-sampled dataset that mirrors the training data distribution:
    # each row pairs a sampled user's financial features with a sampled product's features.
    n = min(sample_size, len(financial_df), len(products_df))
    fin_sample  = financial_df.sample(n=n, random_state=None).reset_index(drop=True)
    prod_sample = products_df.sample(n=n, random_state=None).reset_index(drop=True)

    current = pd.concat([fin_sample, prod_sample], axis=1)[MONITOR_COLS].dropna()

    _OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    current.to_csv(_OUTPUT, index=False)
    print(f"Production data saved: {_OUTPUT}  ({len(current):,} rows, {len(MONITOR_COLS)} columns)")
    print(f"Columns: {MONITOR_COLS}")


if __name__ == "__main__":
    collect()

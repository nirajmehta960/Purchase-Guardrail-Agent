"""
Feature Engineering Orchestrator.

Runs the complete feature engineering pipeline:
1. Financial Feature Engineering
2. Review Feature Engineering (produces product_featured.jsonl + review_featured.jsonl)

Usage:
    python3 src/features/run_features.py
"""

import os
import logging
import argparse

from src.features.financial_features import run_financial_features
from src.features.product_review_features import run_review_features
from src.utils import setup_logging

# Configure module logging.
setup_logging()
logger = logging.getLogger(__name__)


def _data_dir() -> str:
    """Return the central data directory (env-driven via ingestion config).

    Lazy import so this module is safe to load when src.ingestion is stubbed
    (e.g. by tests). Falls back to the DATA_DIR env var (or "data") when the
    full ingestion config is unavailable.
    """
    try:
        from src.ingestion.config import DATA_DIR as _CONFIG_DATA_DIR
        return str(_CONFIG_DATA_DIR)
    except (ImportError, AttributeError):
        return os.environ.get("DATA_DIR", "data")


def main():
    parser = argparse.ArgumentParser(description="Run Feature Engineering Pipeline")
    parser.add_argument("--skip-financial", action="store_true", help="Skip financial features")
    parser.add_argument("--skip-reviews", action="store_true", help="Skip review features")
    args = parser.parse_args()

    DATA_DIR = _data_dir()
    
    # Financial input/output files.
    FIN_INPUT = os.path.join(DATA_DIR, "processed/financial_preprocessed.csv")
    FIN_OUTPUT = os.path.join(DATA_DIR, "features/financial_featured.csv")
    
    # Review & product input/output files.
    REV_INPUT = os.path.join(DATA_DIR, "processed/review_preprocessed.jsonl")
    PROD_INPUT = os.path.join(DATA_DIR, "processed/product_preprocessed.jsonl")
    PROD_OUTPUT = os.path.join(DATA_DIR, "features/product_featured.jsonl")
    REV_OUTPUT = os.path.join(DATA_DIR, "features/review_featured.jsonl")

    # Step 1: Financial features.
    if not args.skip_financial:
        logger.info("--- Starting Financial Feature Engineering ---")
        try:
            run_financial_features(FIN_INPUT, FIN_OUTPUT)
            logger.info("Financial features complete.")
        except Exception as e:
            logger.error(f"Financial feature engineering failed: {e}")

    # Step 2: Review features (produces product_featured.jsonl + review_featured.jsonl).
    if not args.skip_reviews:
        logger.info("--- Starting Review Feature Engineering ---")
        try:
            run_review_features(
                reviews_path=REV_INPUT,
                products_path=PROD_INPUT,
                product_output_path=PROD_OUTPUT,
                review_output_path=REV_OUTPUT,
            )
            logger.info("Review features complete.")
        except Exception as e:
            logger.error(f"Review feature engineering failed: {e}")

    logger.info("Feature engineering pipeline finished.")


# ---------- Airflow task wrappers ----------

def _features_grouping_key(context: dict) -> dict:
    from src.metrics import _get_run_id
    return {"dag_run_id": _get_run_id(context)}


def feature_financial_task(**context):
    """Airflow task: run financial feature engineering."""
    from src.metrics import timed_stage
    logger.info(">>> Running Financial Feature Engineering...")
    DATA_DIR = _data_dir()
    with timed_stage("feature_engineering", _features_grouping_key(context), {"dataset": "financial"}):
        run_financial_features(
            input_path=os.path.join(DATA_DIR, "processed/financial_preprocessed.csv"),
            output_path=os.path.join(DATA_DIR, "features/financial_featured.csv"),
        )
    logger.info(">>> Financial Feature Engineering: SUCCESS")


def feature_review_task(**context):
    """Alias for feature_product_review_task — kept for test compatibility."""
    return feature_product_review_task(**context)


def feature_product_review_task(**context):
    """Airflow task: run product & review feature engineering (produces product + review featured files)."""
    from src.metrics import timed_stage
    logger.info(">>> Running Product & Review Feature Engineering...")
    DATA_DIR = _data_dir()
    with timed_stage("feature_engineering", _features_grouping_key(context), {"dataset": "product_review"}):
        run_review_features(
            reviews_path=os.path.join(DATA_DIR, "processed/review_preprocessed.jsonl"),
            products_path=os.path.join(DATA_DIR, "processed/product_preprocessed.jsonl"),
            product_output_path=os.path.join(DATA_DIR, "features/product_featured.jsonl"),
            review_output_path=os.path.join(DATA_DIR, "features/review_featured.jsonl"),
        )
    logger.info(">>> Product & Review Feature Engineering: SUCCESS")


if __name__ == "__main__":
    main()
"""
Bias Detection Orchestrator for SavVio Pipeline.

Runs the complete bias detection pipeline:
1. Financial Bias Detection
2. Product Bias Detection
3. Review Bias Detection

Usage:
    python3 src/bias/run_bias.py
"""

import sys
import os
import logging
import argparse

from src.bias.financial_bias import run_financial_bias
from src.bias.product_bias import run_product_bias
from src.bias.review_bias import run_review_bias
from src.utils import setup_logging
from src.bias.utils import get_processed_path, get_features_path

# Configure module logging.
setup_logging()
logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────────────────────
# Shared setup
# ──────────────────────────────────────────────────────────────

def _setup():
    """Return resolved data paths: (processed_dir, features_dir).

    Uses the central DATA_DIR from ingestion config (env-driven via DATA_DIR)
    so all stages agree on the data location. Imported lazily and with a fall
    back to the DATA_DIR env var so the module is import-safe when the DAG is
    loaded with stubbed src.ingestion (e.g. inside tests).
    """
    try:
        from src.ingestion.config import DATA_DIR as _CONFIG_DATA_DIR
        data_dir = str(_CONFIG_DATA_DIR)
    except (ImportError, AttributeError):
        data_dir = os.environ.get("DATA_DIR", "data")
    return os.path.join(data_dir, "processed"), os.path.join(data_dir, "features")


def main():
    parser = argparse.ArgumentParser(description="Run all bias detectors.")
    parser.add_argument("--skip-financial", action="store_true", help="Skip financial bias detection.")
    parser.add_argument("--skip-product", action="store_true", help="Skip product bias detection.")
    parser.add_argument("--skip-review", action="store_true", help="Skip review bias detection.")
    args = parser.parse_args()

    failed: list[str] = []
    processed_dir, features_dir = _setup()

    logger.info("=" * 60)
    logger.info("STARTING BIAS DETECTION PIPELINE")
    logger.info("=" * 60)

    # Step 1: Financial bias.
    if not args.skip_financial:
        logger.info("--- Starting Financial Bias Detection ---")
        try:
            run_financial_bias(
                processed_path=os.path.join(processed_dir, "financial_preprocessed.csv"),
                featured_path=os.path.join(features_dir, "financial_featured.csv"),
            )
            logger.info("Financial bias detection complete.")
        except Exception as e:
            logger.error(f"Financial bias detection failed: {e}")
            failed.append("financial")

    # Step 2: Product bias.
    if not args.skip_product:
        logger.info("--- Starting Product Bias Detection ---")
        try:
            run_product_bias(
                preprocessed_path=os.path.join(processed_dir, "product_preprocessed.jsonl"),
                featured_path=os.path.join(features_dir, "product_featured.jsonl"),
            )
            logger.info("Product bias detection complete.")
        except Exception as e:
            logger.error(f"Product bias detection failed: {e}")
            failed.append("product")

    # Step 3: Review bias.
    if not args.skip_review:
        logger.info("--- Starting Review Bias Detection ---")
        try:
            run_review_bias(
                preprocessed_path=os.path.join(processed_dir, "review_preprocessed.jsonl"),
                featured_path=os.path.join(features_dir, "review_featured.jsonl"),
            )
            logger.info("Review bias detection complete.")
        except Exception as e:
            logger.error(f"Review bias detection failed: {e}")
            failed.append("review")

    logger.info("Bias detection pipeline finished.")

    if failed:
        sys.exit(1)


# ──────────────────────────────────────────────────────────────
# Individual Airflow task functions (for parallel bias detection)
# ──────────────────────────────────────────────────────────────

def _bias_grouping_key(context: dict) -> dict:
    from src.metrics import _get_run_id
    return {"dag_run_id": _get_run_id(context)}


def bias_financial_task(**context):
    """Airflow task: run financial bias detection."""
    from src.metrics import timed_stage
    logger.info(">>> Running Financial Bias Detection...")
    processed_dir, features_dir = _setup()
    with timed_stage("bias_detection", _bias_grouping_key(context), {"dataset": "financial"}):
        run_financial_bias(
            processed_path=os.path.join(processed_dir, "financial_preprocessed.csv"),
            featured_path=os.path.join(features_dir, "financial_featured.csv"),
        )
    logger.info(">>> Financial Bias Detection: SUCCESS")


def bias_product_task(**context):
    """Airflow task: run product bias detection."""
    from src.metrics import timed_stage
    logger.info(">>> Running Product Bias Detection...")
    processed_dir, features_dir = _setup()
    with timed_stage("bias_detection", _bias_grouping_key(context), {"dataset": "product"}):
        run_product_bias(
            preprocessed_path=os.path.join(processed_dir, "product_preprocessed.jsonl"),
            featured_path=os.path.join(features_dir, "product_featured.jsonl"),
        )
    logger.info(">>> Product Bias Detection: SUCCESS")


def bias_review_task(**context):
    """Airflow task: run review bias detection."""
    from src.metrics import timed_stage
    logger.info(">>> Running Review Bias Detection...")
    processed_dir, features_dir = _setup()
    with timed_stage("bias_detection", _bias_grouping_key(context), {"dataset": "review"}):
        run_review_bias(
            preprocessed_path=os.path.join(processed_dir, "review_preprocessed.jsonl"),
            featured_path=os.path.join(features_dir, "review_featured.jsonl"),
        )
    logger.info(">>> Review Bias Detection: SUCCESS")


if __name__ == "__main__":
    main()

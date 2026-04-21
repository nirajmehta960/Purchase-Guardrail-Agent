"""
Upload finalized (feature-engineered) data to PostgreSQL.

Loads three datasets into their respective tables:
    - financial_featured.csv   (CSV)   → financial_profiles
    - products_featured.jsonl  (JSONL) → products
    - reviews_featured.jsonl   (JSONL) → reviews

Products must be loaded before reviews (FK dependency).
"""

import json
import os
import logging
import pandas as pd
from sqlalchemy import text

from savviocore.database.db_connection import get_engine
from savviocore.database.db_schema import create_tables

logger = logging.getLogger(__name__)

JSONL_STREAM_CHUNKSIZE = 100_000
_JSONL_LARGE_FILE_MB = 300

# ---------------------------------------------------------------------------
# Column mapping: source field → DB column
# ---------------------------------------------------------------------------

FINANCIAL_COLS = {
    "user_id": "user_id",
    "monthly_income": "monthly_income",
    "monthly_expenses": "monthly_expenses",
    "savings_balance": "savings_balance",
    "has_loan": "has_loan",
    "loan_amount": "loan_amount",
    "monthly_emi": "monthly_emi",
    "loan_interest_rate": "loan_interest_rate",
    "loan_term_months": "loan_term_months",
    "credit_score": "credit_score",
    "employment_status": "employment_status",
    "region": "region",
    # Feature-engineered (included if present)
    "liquid_savings": "liquid_savings",
    "discretionary_income": "discretionary_income",
    "debt_to_income_ratio": "debt_to_income_ratio",
    "saving_to_income_ratio": "saving_to_income_ratio",
    "monthly_expense_burden_ratio": "monthly_expense_burden_ratio",
    "emergency_fund_months": "emergency_fund_months",
}

PRODUCT_COLS = {
    "product_id": "product_id",
    "product_name": "product_name",
    "price": "price",
    "average_rating": "average_rating",
    "rating_number": "rating_number",
    "rating_variance": "rating_variance",
    "description": "description",
    "features": "features",
    "details": "details",
    "category": "category",
}

REVIEW_COLS = {
    "user_id": "user_id",
    "asin": "asin",
    "product_id": "product_id",
    "rating": "rating",
    "review_title": "review_title",
    "review_text": "review_text",
    "verified_purchase": "verified_purchase",
    "helpful_vote": "helpful_vote",
}


# ---------------------------------------------------------------------------
# File readers
# ---------------------------------------------------------------------------

def _read_csv(path: str) -> pd.DataFrame:
    df = pd.read_csv(path)
    logger.info("Read %d rows from CSV: %s", len(df), path)
    return df


def _read_jsonl(path: str) -> pd.DataFrame:
    file_size_mb = os.path.getsize(path) / (1024 * 1024)
    if file_size_mb > _JSONL_LARGE_FILE_MB:
        chunks = pd.read_json(path, lines=True, chunksize=JSONL_STREAM_CHUNKSIZE)
        df = pd.concat(chunks, ignore_index=True)
    else:
        df = pd.read_json(path, lines=True)
    logger.info("Read %d rows from JSONL: %s", len(df), path)
    return df


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _select_and_rename(df: pd.DataFrame, col_map: dict) -> pd.DataFrame:
    available = {k: v for k, v in col_map.items() if k in df.columns}
    missing = [k for k in col_map if k not in df.columns]
    if missing:
        logger.warning("Columns not found in source (skipped): %s", missing)
    return df[list(available.keys())].rename(columns=available)



def _ensure_jsonb(df: pd.DataFrame, col: str) -> pd.DataFrame:
    if col not in df.columns:
        return df

    def to_json_str(val):
        if val is None or (isinstance(val, float) and pd.isna(val)):
            return None
        if isinstance(val, (dict, list)):
            return json.dumps(val)
        if isinstance(val, str):
            return val
        return json.dumps(val)

    df[col] = df[col].apply(to_json_str)
    return df


# ---------------------------------------------------------------------------
# Per-table loaders
# ---------------------------------------------------------------------------

def load_financial(engine, csv_path: str) -> int:
    """Load financial profiles from CSV — TRUNCATE + bulk INSERT.
    GCS always contains the full dataset so each run is a fresh mirror.
    """
    from psycopg2.extras import execute_values

    df = _read_csv(csv_path)
    df = _select_and_rename(df, FINANCIAL_COLS)
    df = df.drop_duplicates(subset=["user_id"], keep="last")

    all_cols = list(df.columns)
    col_list = ", ".join(all_cols)
    sql = f"INSERT INTO financial_profiles ({col_list}) VALUES %s"
    tuples = [tuple(row) for row in df.itertuples(index=False, name=None)]

    with engine.begin() as conn:
        conn.execute(text("TRUNCATE TABLE financial_profiles RESTART IDENTITY"))
        raw = conn.connection
        cursor = raw.cursor()
        execute_values(cursor, sql, tuples)

    logger.info("Inserted %d rows into financial_profiles", len(tuples))
    return len(tuples)


def load_products(engine, jsonl_path: str) -> int:
    """Load products from JSONL — TRUNCATE + bulk INSERT.
    CASCADE on TRUNCATE clears reviews first (FK: reviews.product_id → products.product_id).
    GCS always contains the full dataset so each run is a fresh mirror.
    """
    from psycopg2.extras import execute_values

    df = _read_jsonl(jsonl_path)
    df = _select_and_rename(df, PRODUCT_COLS)
    for col in ["description", "features"]:
        if col in df.columns:
            df[col] = df[col].fillna("")
    df = _ensure_jsonb(df, "details")
    df = df.drop_duplicates(subset=["product_id"], keep="last")

    all_cols = list(df.columns)
    col_list = ", ".join(all_cols)
    sql = f"INSERT INTO products ({col_list}) VALUES %s"
    tuples = [tuple(row) for row in df.itertuples(index=False, name=None)]

    with engine.begin() as conn:
        conn.execute(text("TRUNCATE TABLE products RESTART IDENTITY CASCADE"))
        raw = conn.connection
        cursor = raw.cursor()
        execute_values(cursor, sql, tuples)

    logger.info("Inserted %d rows into products", len(tuples))
    return len(tuples)


def load_reviews(engine, jsonl_path: str) -> int:
    """Load reviews from JSONL — TRUNCATE + bulk INSERT.

    Uses TRUNCATE + bulk INSERT instead of UPSERT because each pipeline run
    loads the full dataset. TRUNCATE avoids per-row conflict checks against
    millions of existing rows, which made runs take ~1 hour vs 10-15 minutes.
    review_embeddings has no FK to reviews so TRUNCATE is safe.
    """
    with engine.connect() as conn:
        existing_ids_set = set(
            pd.read_sql(text("SELECT product_id FROM products"), conn)["product_id"].tolist()
        )

    file_size_mb = os.path.getsize(jsonl_path) / (1024 * 1024)
    logger.info(
        "Reviews JSONL size: %.1f MB. Streaming in chunks of %d rows.",
        file_size_mb, JSONL_STREAM_CHUNKSIZE,
    )

    from psycopg2.extras import execute_values

    all_cols = list(REVIEW_COLS.values())
    col_list = ", ".join(all_cols)
    sql = f"INSERT INTO reviews ({col_list}) VALUES %s"

    total_rows = 0
    total_dropped = 0
    with engine.begin() as conn:
        conn.execute(text("TRUNCATE TABLE reviews RESTART IDENTITY"))

        raw = conn.connection
        cursor = raw.cursor()
        for i, chunk in enumerate(pd.read_json(jsonl_path, lines=True, chunksize=JSONL_STREAM_CHUNKSIZE), start=1):
            df = _select_and_rename(chunk, REVIEW_COLS)
            if "verified_purchase" in df.columns:
                df["verified_purchase"] = df["verified_purchase"].astype(bool)
            if "helpful_vote" in df.columns:
                df["helpful_vote"] = df["helpful_vote"].fillna(0).astype(int)
            before_count = len(df)
            df = df[df["product_id"].isin(existing_ids_set)]
            total_dropped += before_count - len(df)
            df = df.drop_duplicates(subset=["user_id", "product_id"], keep="last")
            if df.empty:
                continue
            tuples = [tuple(row) for row in df.itertuples(index=False, name=None)]
            execute_values(cursor, sql, tuples, page_size=25_000)
            total_rows += len(tuples)
            logger.info("Reviews chunk %d inserted: %d rows (running total: %d)", i, len(tuples), total_rows)

    if total_dropped:
        logger.warning("Dropped %d orphaned reviews (product_id not in products table)", total_dropped)
    return total_rows


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------

def load_all(
    financial_path: str,
    products_path: str,
    reviews_path: str,
    env: str = "dev",
):
    """Load all three datasets into PostgreSQL — TRUNCATE + bulk INSERT."""
    engine = get_engine(env)
    create_tables(engine)

    n_fin  = load_financial(engine, financial_path)
    n_prod = load_products(engine, products_path)
    n_rev  = load_reviews(engine, reviews_path)

    summary = {
        "financial_profiles": n_fin,
        "products": n_prod,
        "reviews": n_rev,
    }
    logger.info("Upload complete: %s", summary)
    return summary


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import argparse
    from src.utils import setup_logging
    setup_logging()

    parser = argparse.ArgumentParser(description="Upload featured data to PostgreSQL")
    parser.add_argument("--financial", required=True)
    parser.add_argument("--products", required=True)
    parser.add_argument("--reviews", required=True)
    parser.add_argument("--env", default="dev", choices=["dev", "prod"])
    args = parser.parse_args()

    result = load_all(args.financial, args.products, args.reviews, env=args.env)
    print("Upload summary:", result)

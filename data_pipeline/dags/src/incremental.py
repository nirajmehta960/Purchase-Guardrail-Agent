"""
Incremental merge utilities for the SavVio data pipeline.

Provides reusable functions for merging new data with existing data
at every stage (preprocessing, features). When a record with the same
key exists in both old and new data, the new version wins. New records
are appended, and old records not present in the new data are kept.
"""

import hashlib
import json
import logging
import os
import shutil
from typing import Dict, List, Optional, Tuple

import duckdb
import pandas as pd

logger = logging.getLogger(__name__)

# DuckDB tunables — env-driven so they can be scaled with the worker container's
# mem_limit (currently 5G in docker-compose.yaml). Preprocessing runs 3 DuckDB
# tasks in parallel, so 3 × DUCKDB_MEMORY_LIMIT must stay well below the
# container limit. Defaults preserve the original safe values.
DUCKDB_MEMORY_LIMIT = os.getenv("DUCKDB_MEMORY_LIMIT", "1000MB")
DUCKDB_THREADS = int(os.getenv("DUCKDB_THREADS", "2"))
DUCKDB_TEMP_DIRECTORY = os.getenv("DUCKDB_TEMP_DIRECTORY", "/tmp")
JSONL_MAX_OBJECT_SIZE = int(os.getenv("JSONL_MAX_OBJECT_SIZE", str(33_554_432)))


def _configure_duckdb(con: "duckdb.DuckDBPyConnection") -> None:
    """Apply shared DuckDB pragmas (memory limit, threads, temp dir)."""
    con.execute(f"PRAGMA temp_directory='{DUCKDB_TEMP_DIRECTORY}';")
    con.execute(f"PRAGMA memory_limit='{DUCKDB_MEMORY_LIMIT}';")
    con.execute("PRAGMA preserve_insertion_order=false;")
    con.execute(f"PRAGMA threads={DUCKDB_THREADS};")


# ---------------------------------------------------------------------------
# File checksum
# ---------------------------------------------------------------------------

def file_checksum(path: str) -> str:
    """
    Compute MD5 checksum of a file.

    Uses MD5 for consistency with GCS blob checksums.

    Args:
        path: Path to the file.

    Returns:
        Hex-encoded MD5 digest string.
    """
    return hashlib.md5(open(path, "rb").read()).hexdigest()


# ---------------------------------------------------------------------------
# CSV merge
# ---------------------------------------------------------------------------

def merge_csv(
    new_path: str,
    existing_path: str,
    key_cols: List[str],
) -> Dict[str, int]:
    """
    Merge a newly-produced CSV with an existing CSV file out-of-core using DuckDB.
    
    Logic:
        - Records whose key exists in both → replaced with the new version.
        - Records only in the new file → appended.
        - Records only in the existing file → kept unchanged.
    """
    logger.info("Starting out-of-core CSV merge using DuckDB...")

    if not os.path.exists(existing_path) or os.path.getsize(existing_path) == 0:
        # First run — nothing to merge with.
        shutil.copy(new_path, existing_path)
        with duckdb.connect() as con:
            total_res = con.execute(f"SELECT COUNT(*) FROM read_csv_auto('{new_path}')").fetchone()
            total = total_res[0] if total_res else 0
        stats = {"updated": 0, "appended": total, "unchanged": 0, "total": total}
        logger.info("First run — wrote %d records: %s", total, existing_path)
        return stats

    partition_clause = ", ".join(key_cols)
    temp_out = existing_path + ".duckdb.tmp"

    # DuckDB pragmas (memory limit, threads, temp dir, ordering) are applied via
    # _configure_duckdb() so the same env-driven defaults are used everywhere.
    # See the DUCKDB_* env vars at the top of this module for tuning guidance —
    # 3 × DUCKDB_MEMORY_LIMIT must stay below the worker container's mem_limit
    # because preprocessing runs 3 DuckDB tasks in parallel.
    with duckdb.connect() as con:
        _configure_duckdb(con)

        query = f"""
        COPY (
            WITH new_data AS (
                SELECT *, 1 AS __source_priority FROM read_csv_auto('{new_path}')
            ),
            existing_data AS (
                SELECT *, 2 AS __source_priority FROM read_csv_auto('{existing_path}')
            ),
            combined AS (
                SELECT * FROM new_data
                UNION ALL BY NAME
                SELECT * FROM existing_data
            ),
            deduped AS (
                SELECT * EXCLUDE (__source_priority, __rn)
                FROM (
                    SELECT *, ROW_NUMBER() OVER (PARTITION BY {partition_clause} ORDER BY __source_priority ASC) AS __rn
                    FROM combined
                )
                WHERE __rn = 1
            )
            SELECT * FROM deduped
        ) TO '{temp_out}' (FORMAT CSV, HEADER);
        """
        con.execute(query)

    # Count rows after closing DuckDB — avoids re-reading the merged file inside the
    # same connection (which doubled peak memory usage and caused OOM on large files).
    total_count = sum(1 for _ in open(temp_out)) - 1  # subtract header row

    stats = {
        "total": total_count,
    }

    os.replace(temp_out, existing_path)
    logger.info("Merged CSV → %s | %s", existing_path, stats)
    return stats


# ---------------------------------------------------------------------------
# JSONL merge
# ---------------------------------------------------------------------------

def merge_jsonl(
    new_path: str,
    existing_path: str,
    key_cols: List[str],
) -> Dict[str, int]:
    """
    Merge a newly-produced JSONL with an existing JSONL file out-of-core using DuckDB.
    
    Same logic as merge_csv but for JSONL files (one JSON object per line).
    """
    logger.info("Starting out-of-core JSONL merge using DuckDB...")

    if not os.path.exists(existing_path) or os.path.getsize(existing_path) == 0:
        # First run — nothing to merge with.
        shutil.copy(new_path, existing_path)
        with duckdb.connect() as con:
            total_res = con.execute(f"SELECT COUNT(*) FROM read_json_auto('{new_path}')").fetchone()
            total = total_res[0] if total_res else 0
        stats = {"updated": 0, "appended": total, "unchanged": 0, "total": total}
        logger.info("First run — wrote %d records: %s", total, existing_path)
        return stats

    partition_clause = ", ".join(key_cols)
    temp_out = existing_path + ".duckdb.tmp"

    with duckdb.connect() as con:
        _configure_duckdb(con)

        query = f"""
        COPY (
            WITH new_data AS (
                SELECT *, 1 AS __source_priority FROM read_json_auto('{new_path}', maximum_object_size={JSONL_MAX_OBJECT_SIZE})
            ),
            existing_data AS (
                SELECT *, 2 AS __source_priority FROM read_json_auto('{existing_path}', maximum_object_size={JSONL_MAX_OBJECT_SIZE})
            ),
            combined AS (
                SELECT * FROM new_data
                UNION ALL BY NAME
                SELECT * FROM existing_data
            ),
            deduped AS (
                SELECT * EXCLUDE (__source_priority, __rn)
                FROM (
                    SELECT *, ROW_NUMBER() OVER (PARTITION BY {partition_clause} ORDER BY __source_priority ASC) AS __rn
                    FROM combined
                )
                WHERE __rn = 1
            )
            SELECT * FROM deduped
        ) TO '{temp_out}' (FORMAT JSON);
        """
        con.execute(query)

    # Count rows after closing DuckDB — avoids re-reading the merged file inside the
    # same connection (which doubled peak memory usage and caused OOM on large files).
    total_count = sum(1 for _ in open(temp_out))

    stats = {
        "total": total_count,
    }

    os.replace(temp_out, existing_path)
    logger.info("Merged JSONL → %s | %s", existing_path, stats)
    return stats

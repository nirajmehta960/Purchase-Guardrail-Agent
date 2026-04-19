"""Shared utilities for bias detection."""
import logging
import os

LOG_FORMAT = "[%(asctime)s] [%(levelname)s] [%(name)s] %(message)s"


from src.utils import setup_logging


def _default_data_dir() -> str:
    """Return the central data directory (env-driven via ingestion config).

    Falls back to the DATA_DIR env var when the full ingestion config can't
    be imported (e.g. in tests that stub out src.ingestion).
    """
    try:
        from src.ingestion.config import DATA_DIR
        return str(DATA_DIR)
    except (ImportError, AttributeError):
        return os.environ.get("DATA_DIR", "data")


def get_processed_path(filename: str, base_dir: str = None) -> str:
    """Return path to a processed data file. Uses central DATA_DIR by default."""
    if base_dir is None:
        return os.path.join(_default_data_dir(), "processed", filename)
    return os.path.join(base_dir, "data/processed", filename)


def get_features_path(filename: str, base_dir: str = None) -> str:
    """Return path to a feature-engineered data file. Uses central DATA_DIR by default."""
    if base_dir is None:
        return os.path.join(_default_data_dir(), "features", filename)
    return os.path.join(base_dir, "data/features", filename)

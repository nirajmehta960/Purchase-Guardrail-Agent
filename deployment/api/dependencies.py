"""
FastAPI Dependency Injection for the SavVio API.

Centralizes resource access so endpoints declare what they *need*
rather than reaching into global state. Enables clean test overrides
via ``app.dependency_overrides``.
"""

from __future__ import annotations

from fastapi import Depends, HTTPException

from deployment.api.model_loader import ModelManager, model_manager


def get_model_manager() -> ModelManager:
    """Provide the ModelManager singleton."""
    return model_manager


def require_db(
    manager: ModelManager = Depends(get_model_manager),
):
    """Ensure database is available; raise 503 if not.

    Returns the SQLAlchemy engine for direct use.
    """
    if not manager.db_engine:
        raise HTTPException(
            status_code=503,
            detail="Database unavailable.",
        )
    return manager.db_engine

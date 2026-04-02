"""
conftest.py — SavVio data_pipeline test configuration.

The source files under dags/src/ use absolute imports like:
    from src.ingestion.config import ...
    from src.validation.anomaly.detectors import ...

For these to work, Python must find 'src' as a proper regular package
(i.e. a directory with __init__.py), NOT as a namespace package.

Problem: if both dags/ AND dags/src/ appear in sys.path, Python's
PathFinder can produce a namespace package for 'src' instead of the
regular package, resulting in:
    ModuleNotFoundError: No module named 'src.X'; 'src' is not a package

Solution: ensure dags/ is in sys.path (not dags/src/), and
pre-register 'src' in sys.modules as a proper package via importlib
so it is fully resolved before any test module loads.
"""

import sys
import os
import importlib.util
import types

# ── 1. Ensure dags/ is in sys.path ──────────────────────────────────────────
DAGS_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "dags"))
SRC_DIR = os.path.join(DAGS_DIR, "src")

if DAGS_DIR not in sys.path:
    sys.path.insert(0, DAGS_DIR)

# ── 2. Pre-register 'src' as a proper package ────────────────────────────────
# This prevents any later sys.path manipulation from turning 'src' into a
# namespace package.  We do this even if DAGS_DIR is already in sys.path
# because other conftest files or pytest plugins may have added dags/src/
# to sys.path before this file runs.
if "src" not in sys.modules:
    _src_init = os.path.join(SRC_DIR, "__init__.py")
    _spec = importlib.util.spec_from_file_location(
        "src",
        _src_init,
        submodule_search_locations=[SRC_DIR],
    )
    if _spec is not None and _spec.loader is not None:
        _src_mod = importlib.util.module_from_spec(_spec)
        _src_mod.__path__ = [SRC_DIR]  # type: ignore[assignment]
        _src_mod.__package__ = "src"
        sys.modules["src"] = _src_mod
        _spec.loader.exec_module(_src_mod)  # type: ignore[union-attr]

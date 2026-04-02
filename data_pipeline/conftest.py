"""
Root-level conftest.py for data_pipeline.

Loaded by pytest BEFORE any other conftest.py or test module, since this
lives at the rootdir (data_pipeline/).  This is the earliest possible hook.

We pre-register the 'src' package into sys.modules here so that subsequent
imports of 'from src.X import ...' (including those inside files loaded via
importlib.util.spec_from_file_location in test_*.py files) resolve correctly.
"""

from __future__ import annotations

import importlib.util
import os
import sys

# ---------------------------------------------------------------------------
# 1. Resolve absolute paths
# ---------------------------------------------------------------------------
_HERE = os.path.dirname(os.path.abspath(__file__))
DAGS_DIR = os.path.join(_HERE, "dags")
SRC_DIR = os.path.join(DAGS_DIR, "src")

# ---------------------------------------------------------------------------
# 2. Insert dags/ at the front of sys.path (idempotent)
# ---------------------------------------------------------------------------
if DAGS_DIR not in sys.path:
    sys.path.insert(0, DAGS_DIR)

# ---------------------------------------------------------------------------
# 3. Force-register 'src' as a proper regular package in sys.modules.
#
#    Without this, if any other path entry (e.g. savviocore's editable-install
#    .pth file adds savviocore/ to sys.path, which contains a src/ directory
#    without __init__.py) is searched before dags/, Python's PathFinder may
#    produce a namespace package for 'src' instead of the regular package at
#    dags/src/__init__.py, causing:
#        ModuleNotFoundError: No module named 'src.X'; 'src' is not a package
# ---------------------------------------------------------------------------
_src_init = os.path.join(SRC_DIR, "__init__.py")

if "src" not in sys.modules and os.path.isfile(_src_init):
    _spec = importlib.util.spec_from_file_location(
        "src",
        _src_init,
        submodule_search_locations=[SRC_DIR],
    )
    if _spec is not None and _spec.loader is not None:
        _src_mod = importlib.util.module_from_spec(_spec)
        _src_mod.__path__ = [SRC_DIR]      # mark as regular package
        _src_mod.__package__ = "src"
        sys.modules["src"] = _src_mod
        _spec.loader.exec_module(_src_mod)

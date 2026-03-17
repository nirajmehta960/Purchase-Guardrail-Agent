# model_pipeline/tests/test_bias_detection.py
import os
import sys
import types
from unittest.mock import MagicMock, patch

import numpy as np
import pandas as pd
import pytest

# ---------------------------------------------------------------------------
# Path setup
# ---------------------------------------------------------------------------
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
SRC_DIR = os.path.join(PROJECT_ROOT, "src")
sys.path.insert(0, PROJECT_ROOT)
sys.path.insert(0, SRC_DIR)

# ---------------------------------------------------------------------------
# Stub external dependencies
# ---------------------------------------------------------------------------
def _stub_modules():
    # fairlearn
    fairlearn = types.ModuleType("fairlearn")
    fairlearn.metrics = types.ModuleType("fairlearn.metrics")
    fairlearn.metrics.MetricFrame = MagicMock()
    fairlearn.metrics.demographic_parity_difference = MagicMock(return_value=0.05)
    fairlearn.metrics.equalized_odds_difference = MagicMock(return_value=0.03)
    sys.modules.setdefault("fairlearn", fairlearn)
    sys.modules.setdefault("fairlearn.metrics", fairlearn.metrics)

    # mlflow
    mlflow = types.ModuleType("mlflow")
    mlflow.log_metrics = MagicMock()
    sys.modules.setdefault("mlflow", mlflow)

    # sklearn.metrics
    sklearn = types.ModuleType("sklearn")
    sklearn.metrics = types.ModuleType("sklearn.metrics")
    sklearn.metrics.accuracy_score = MagicMock(return_value=0.9)
    sys.modules.setdefault("sklearn", sklearn)
    sys.modules.setdefault("sklearn.metrics", sklearn.metrics)

_stub_modules()

from guards.bias_detection import evaluate_bias

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _make_inputs(n=20):
    y_test = pd.Series([0, 1, 2] * (n // 3) + [0] * (n % 3))
    y_pred = pd.Series([0, 1, 2] * (n // 3) + [0] * (n % 3))
    sensitive = pd.DataFrame({
        "region":            ["North", "South"] * (n // 2),
        "employment_status": ["Employed", "Unemployed"] * (n // 2),
    })
    return y_test, y_pred, sensitive


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------
class TestEvaluateBias:

    def test_returns_dict(self):
        y_test, y_pred, sf = _make_inputs()
        result = evaluate_bias(y_test, y_pred, sf)
        assert isinstance(result, dict)

    def test_keys_contain_feature_names(self):
        y_test, y_pred, sf = _make_inputs()
        result = evaluate_bias(y_test, y_pred, sf)
        for col in sf.columns:
            assert f"bias_dpd_{col}" in result
            assert f"bias_eod_{col}" in result

    def test_dpd_and_eod_values_are_floats(self):
        y_test, y_pred, sf = _make_inputs()
        result = evaluate_bias(y_test, y_pred, sf)
        for v in result.values():
            assert isinstance(v, float)

    def test_mlflow_log_metrics_called(self):
        import mlflow
        y_test, y_pred, sf = _make_inputs()
        evaluate_bias(y_test, y_pred, sf)
        assert mlflow.log_metrics.called

    def test_single_sensitive_feature(self):
        y_test, y_pred, _ = _make_inputs()
        sf = pd.DataFrame({"region": ["North", "South"] * 10})
        result = evaluate_bias(y_test, y_pred, sf)
        assert "bias_dpd_region" in result
        assert "bias_eod_region" in result

    def test_returns_correct_number_of_metrics(self):
        y_test, y_pred, sf = _make_inputs()
        result = evaluate_bias(y_test, y_pred, sf)
        # 2 metrics (dpd + eod) per feature
        assert len(result) == len(sf.columns) * 2

    def test_demographic_parity_called_per_feature(self):
        import fairlearn.metrics as fm
        fm.demographic_parity_difference.reset_mock()
        y_test, y_pred, sf = _make_inputs()
        evaluate_bias(y_test, y_pred, sf)
        assert fm.demographic_parity_difference.call_count == len(sf.columns)

    def test_equalized_odds_called_per_feature(self):
        import fairlearn.metrics as fm
        fm.equalized_odds_difference.reset_mock()
        y_test, y_pred, sf = _make_inputs()
        evaluate_bias(y_test, y_pred, sf)
        assert fm.equalized_odds_difference.call_count == len(sf.columns)
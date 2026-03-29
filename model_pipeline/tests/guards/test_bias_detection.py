import sys
import os
from unittest.mock import MagicMock

# Fix sqlalchemy __spec__ issue in CI environment
sys.modules['sqlalchemy'] = MagicMock()

# Add src/ to path so 'guards' package is findable
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../../src")))

import pytest
import pandas as pd
from unittest.mock import patch
from guards.bias_detection import evaluate_bias


# ── Helpers ──────────────────────────────────────────────────────────────────

def make_data(y_test, y_pred, groups):
    sensitive = pd.DataFrame({"gender": groups})
    return pd.Series(y_test), pd.Series(y_pred), sensitive


# ── Tests ─────────────────────────────────────────────────────────────────────

@patch("guards.bias_detection.mlflow.log_metrics")
def test_returns_dict(mock_log):
    y_test, y_pred, sensitive = make_data(
        [0, 1, 0, 1], [0, 1, 0, 1], ["M", "F", "M", "F"]
    )
    result = evaluate_bias(y_test, y_pred, sensitive)
    assert isinstance(result, dict)


@patch("guards.bias_detection.mlflow.log_metrics")
def test_keys_present(mock_log):
    y_test, y_pred, sensitive = make_data(
        [0, 1, 0, 1], [0, 1, 0, 1], ["M", "F", "M", "F"]
    )
    result = evaluate_bias(y_test, y_pred, sensitive)
    assert "bias_dpd_gender" in result
    assert "bias_eod_gender" in result


@patch("guards.bias_detection.mlflow.log_metrics")
def test_values_are_floats(mock_log):
    y_test, y_pred, sensitive = make_data(
        [0, 1, 0, 1], [0, 1, 0, 1], ["M", "F", "M", "F"]
    )
    result = evaluate_bias(y_test, y_pred, sensitive)
    for v in result.values():
        assert isinstance(v, float)


@patch("guards.bias_detection.mlflow.log_metrics")
def test_perfect_preds_low_disparity(mock_log):
    # Same label distribution across groups → low disparity
    y_test, y_pred, sensitive = make_data(
        [0, 0, 1, 1, 0, 0, 1, 1],
        [0, 0, 1, 1, 0, 0, 1, 1],
        ["M", "M", "M", "M", "F", "F", "F", "F"],
    )
    result = evaluate_bias(y_test, y_pred, sensitive)
    assert abs(result["bias_dpd_gender"]) < 0.1


@patch("guards.bias_detection.mlflow.log_metrics")
def test_biased_preds_nonzero_disparity(mock_log):
    y_test, y_pred, sensitive = make_data(
        [0, 0, 0, 0, 1, 1, 1, 1],
        [1, 1, 0, 0, 1, 1, 0, 0],
        ["M", "M", "F", "F", "M", "M", "F", "F"],
    )
    result = evaluate_bias(y_test, y_pred, sensitive)
    assert abs(result["bias_dpd_gender"]) > 0.0


@patch("guards.bias_detection.mlflow.log_metrics")
def test_mlflow_called_once(mock_log):
    y_test, y_pred, sensitive = make_data(
        [0, 1, 0, 1], [0, 1, 0, 1], ["M", "F", "M", "F"]
    )
    evaluate_bias(y_test, y_pred, sensitive)
    mock_log.assert_called_once()


@patch("guards.bias_detection.mlflow.log_metrics")
def test_multi_feature_keys(mock_log):
    y_test = pd.Series([0, 1, 0, 1, 0, 1, 0, 1])
    y_pred = pd.Series([0, 1, 0, 1, 1, 0, 1, 0])
    sensitive = pd.DataFrame({
        "gender": ["M", "F", "M", "F", "M", "F", "M", "F"],
        "region": ["N", "N", "S", "S", "N", "N", "S", "S"],
    })
    result = evaluate_bias(y_test, y_pred, sensitive)
    assert "bias_dpd_gender" in result
    assert "bias_dpd_region" in result


@patch("guards.bias_detection.mlflow.log_metrics")
def test_all_same_prediction(mock_log):
    y_test, y_pred, sensitive = make_data(
        [0, 1, 0, 1], [0, 0, 0, 0], ["M", "F", "M", "F"]
    )
    result = evaluate_bias(y_test, y_pred, sensitive)
    assert isinstance(result, dict)
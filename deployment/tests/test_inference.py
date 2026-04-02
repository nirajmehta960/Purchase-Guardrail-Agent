"""
Inference & Model Loading Tests for SavVio Deployment.

Tests the ModelManager and prediction logic with mocked resources.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

# Ensure model_pipeline/src is on the path
_project_root = Path(__file__).resolve().parent.parent.parent
_model_src = _project_root / "model_pipeline" / "src"
_savviocore_src = _project_root / "savviocore" / "src"
for p in [str(_project_root), str(_model_src), str(_savviocore_src)]:
    if p not in sys.path:
        sys.path.insert(0, p)


class TestModelManager:
    """Tests for the ModelManager class."""

    def test_model_manager_initializes_unloaded(self):
        from deployment.api.model_loader import ModelManager
        manager = ModelManager()
        assert not manager.is_loaded
        assert manager.model is None
        assert manager.label_encoder is None

    def test_default_label_encoder_fallback(self):
        """When no label encoder file exists, a default should be created."""
        from deployment.api.model_loader import ModelManager
        manager = ModelManager()
        manager._load_label_encoder()
        assert manager.label_encoder is not None
        assert set(manager.label_encoder.classes_) == {"GREEN", "YELLOW", "RED"}

    def test_label_encoder_roundtrip(self):
        """Label encoder should correctly round-trip GREEN/YELLOW/RED."""
        from sklearn.preprocessing import LabelEncoder
        le = LabelEncoder()
        le.fit(["GREEN", "RED", "YELLOW"])

        for label in ["GREEN", "YELLOW", "RED"]:
            encoded = le.transform([label])[0]
            decoded = le.inverse_transform([encoded])[0]
            assert decoded == label

    def test_predict_returns_label_and_confidence(self):
        """predict() should return a (label, confidence) tuple."""
        from unittest.mock import MagicMock
        from deployment.api.model_loader import ModelManager
        from sklearn.preprocessing import LabelEncoder

        manager = ModelManager()

        # Mock a model that returns integer predictions and probabilities
        mock_model = MagicMock()
        mock_model.predict.return_value = np.array([0])  # GREEN
        mock_model.predict_proba.return_value = np.array([[0.85, 0.10, 0.05]])

        manager.model = mock_model
        le = LabelEncoder()
        le.fit(["GREEN", "RED", "YELLOW"])
        manager.label_encoder = le

        label, confidence = manager.predict(np.array([[1, 2, 3]]))
        assert label == "GREEN"
        assert confidence == 0.85

    def test_predict_no_model_returns_default(self):
        """With no model loaded, predict() should return GREEN with 0.0 confidence."""
        from deployment.api.model_loader import ModelManager
        manager = ModelManager()
        label, confidence = manager.predict(np.array([[1, 2, 3]]))
        assert label == "GREEN"
        assert confidence == 0.0

    def test_predict_confidence_range(self):
        """Confidence should always be in [0, 1]."""
        from unittest.mock import MagicMock
        from deployment.api.model_loader import ModelManager
        from sklearn.preprocessing import LabelEncoder

        manager = ModelManager()
        mock_model = MagicMock()
        mock_model.predict.return_value = np.array([1])
        mock_model.predict_proba.return_value = np.array([[0.1, 0.7, 0.2]])
        manager.model = mock_model

        le = LabelEncoder()
        le.fit(["GREEN", "RED", "YELLOW"])
        manager.label_encoder = le

        _, confidence = manager.predict(np.array([[1, 2, 3]]))
        assert 0.0 <= confidence <= 1.0

    def test_llm_provider_defaults_to_mock(self):
        """When LLM provider init fails, should fall back to mock."""
        from deployment.api.model_loader import ModelManager
        manager = ModelManager()
        manager._init_llm_provider()
        assert manager.llm_provider is not None
        assert manager.llm_provider.provider_name == "mock"

    def test_health_check_helpers(self):
        """check_db_connection and get_llm_provider_name should work."""
        from deployment.api.model_loader import ModelManager
        manager = ModelManager()

        # No DB engine → False
        assert manager.check_db_connection() is False

        # No provider → "none"
        assert manager.get_llm_provider_name() == "none"

        # After init → "mock"
        manager._init_llm_provider()
        assert manager.get_llm_provider_name() == "mock"

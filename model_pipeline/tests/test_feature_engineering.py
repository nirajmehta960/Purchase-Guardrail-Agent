# model_pipeline/tests/test_feature_engineering.py
import os
import sys
import types
from unittest.mock import MagicMock

import pytest
import numpy as np
import pandas as pd
import sklearn
import sklearn.base
import sklearn.pipeline
import sklearn.preprocessing

# ---------------------------------------------------------------------------
# Stub external dependencies before any imports
# ---------------------------------------------------------------------------
def _stub():
    # savviocore
    savviocore = types.ModuleType("savviocore")
    savviocore.database = types.ModuleType("savviocore.database")
    savviocore.database.db_connection = types.ModuleType("savviocore.database.db_connection")
    savviocore.database.db_connection.get_engine = MagicMock(return_value=MagicMock())
    sys.modules.setdefault("savviocore", savviocore)
    sys.modules.setdefault("savviocore.database", savviocore.database)
    sys.modules.setdefault("savviocore.database.db_connection", savviocore.database.db_connection)

    # sqlalchemy
    sqlalchemy = types.ModuleType("sqlalchemy")
    sqlalchemy.text = lambda q: q
    sys.modules.setdefault("sqlalchemy", sqlalchemy)

    # sklearn — use real sklearn (imported at top-level)
    sys.modules.setdefault("sklearn", sklearn)
    sys.modules.setdefault("sklearn.base", sklearn.base)
    sys.modules.setdefault("sklearn.pipeline", sklearn.pipeline)
    sys.modules.setdefault("sklearn.preprocessing", sklearn.preprocessing)
    sys.modules.setdefault("sklearn.pipeline", sklearn.pipeline)

    # path
    PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    SRC_DIR = os.path.join(PROJECT_ROOT, "src")
    sys.path.insert(0, PROJECT_ROOT)
    sys.path.insert(0, SRC_DIR)

_stub()

# ---------------------------------------------------------------------------
# Synthetic data helpers
# ---------------------------------------------------------------------------
def _make_financial_df(n=50):
    rng = np.random.default_rng(42)
    savings_balance = rng.uniform(500, 50000, n)
    return pd.DataFrame({
        "user_id":                      [f"U{i}" for i in range(n)],
        "monthly_income":               rng.uniform(2000, 10000, n),
        "monthly_expenses":             rng.uniform(1000, 5000, n),
        "savings_balance":              savings_balance,
        "liquid_savings":               savings_balance * rng.uniform(0.10, 0.60, n),
        "has_loan":                     rng.choice([True, False], n),
        "loan_amount":                  rng.uniform(0, 200000, n),
        "monthly_emi":                  rng.uniform(0, 2000, n),
        "loan_interest_rate":           rng.uniform(0, 20, n),
        "loan_term_months":             rng.choice([0, 60, 120, 240], n),
        "credit_score":                 rng.integers(300, 850, n),
        "employment_status":            rng.choice(["employed", "self-employed", "unemployed"], n),
        "region":                       rng.choice(["east", "west", "south", "north"], n),
        "discretionary_income":         rng.uniform(-2000, 5000, n),
        "debt_to_income_ratio":         rng.uniform(0, 0.6, n),
        "saving_to_income_ratio":       rng.uniform(0.1, 5.0, n),
        "monthly_expense_burden_ratio": rng.uniform(0.3, 1.2, n),
        "emergency_fund_months":        rng.uniform(0.5, 12, n),
    })

def _make_reviews_df(n=100):
    rng = np.random.default_rng(77)
    return pd.DataFrame({
        "review_id":         [f"R{i}" for i in range(n)],
        "product_id":        [f"P{rng.integers(0, 30)}" for i in range(n)],
        "user_id":           [f"U{rng.integers(0, 50)}" for i in range(n)],
        "rating":            rng.integers(1, 6, n),
        "review_text":       ["This is a test review"] * n,
        "verified_purchase": rng.choice([True, False], n),
        "helpful_vote":      rng.integers(0, 100, n),
    })

def _make_products_df(n=32):
    rng = np.random.default_rng(99)
    per_tier = max(n // 3, 2)
    remainder = n - 2 * per_tier
    prices = np.concatenate([
        rng.uniform(110, 490, per_tier),
        rng.uniform(550, 1400, per_tier),
        rng.uniform(1600, 5000, remainder),
    ])
    rng.shuffle(prices)
    total = len(prices)
    return pd.DataFrame({
        "product_id":     [f"P{i}" for i in range(total)],
        "product_name":   [f"Product {i}" for i in range(total)],
        "price":          prices,
        "average_rating": rng.uniform(1, 5, total),
        "rating_number":  rng.integers(1, 5000, total),
        "rating_variance":rng.uniform(0, 2, total),
        "description":    ["desc"] * total,
        "features":       ["feat"] * total,
        "details":        ["det"] * total,
        "category":       ["cat"] * total,
    })


# ---------------------------------------------------------------------------
# Tests: Missing-value handling
# ---------------------------------------------------------------------------
class TestMissingValues:

    def test_financial_nulls_filled(self):
        from features.feature_engineering import MissingValueImputer
        df = _make_financial_df(20)
        df.loc[0, "discretionary_income"] = np.nan
        df.loc[1, "debt_to_income_ratio"] = np.nan
        result = MissingValueImputer().transform(df)
        assert result["discretionary_income"].isnull().sum() == 0
        assert result["debt_to_income_ratio"].isnull().sum() == 0

    def test_product_variance_null_filled_with_zero(self):
        from features.feature_engineering import MissingValueImputer
        df = pd.DataFrame({
            "rating_variance": [1.0, np.nan, 0.5],
            "price":           [10, 20, 30],
        })
        result = MissingValueImputer().transform(df)
        assert result["rating_variance"].iloc[1] == 0.0

    def test_categorical_nulls_filled_with_unknown(self):
        from features.feature_engineering import MissingValueImputer
        df = pd.DataFrame({
            "employment_status": ["employed", None, "self-employed"],
            "has_loan":          [True, None, False],
            "region":            ["east", "west", None],
        })
        result = MissingValueImputer().transform(df)
        assert result["employment_status"].iloc[1] == "Unknown"
        assert result["region"].iloc[2] == "Unknown"

    def test_computed_features_nulls_filled_with_median(self):
        from features.feature_engineering import MissingValueImputer
        computed_cols = [
            "affordability_score", "price_to_income_ratio", "residual_utility_score",
            "savings_to_price_ratio", "net_worth_indicator", "credit_risk_indicator",
        ]
        df = pd.DataFrame({col: [np.nan, 1.0, 3.0] for col in computed_cols})
        result = MissingValueImputer().transform(df)
        for col in computed_cols:
            assert result[col].isnull().sum() == 0, f"NaN not filled in {col}"
            assert result[col].iloc[0] == pytest.approx(2.0), f"Expected median 2.0 for {col}"


# ---------------------------------------------------------------------------
# Tests: Encoding
# ---------------------------------------------------------------------------
class TestEncoding:

    def test_ordinal_encoder_produces_numeric(self, tmp_path):
        from features.feature_engineering import CategoricalEncoder
        df = pd.DataFrame({
            "employment_status": ["employed", "self-employed", "unemployed"],
            "has_loan":          [True, False, True],
            "region":            ["east", "west", "south"],
        })
        encoder = CategoricalEncoder()
        result = encoder.fit_transform(df)
        assert result["employment_status"].dtype in (np.float64, np.int64)
        assert result["region"].dtype in (np.float64, np.int64)
        result2 = encoder.transform(df)
        assert (result["employment_status"] == result2["employment_status"]).all()

    def test_unknown_category_at_inference(self):
        from features.feature_engineering import CategoricalEncoder
        train_df = pd.DataFrame({
            "employment_status": ["employed", "self-employed"],
            "has_loan":          [True, False],
            "region":            ["east", "west"],
        })
        encoder = CategoricalEncoder()
        encoder.fit(train_df)
        test_df = pd.DataFrame({
            "employment_status": ["NEVER_SEEN_BEFORE"],
            "has_loan":          [True],
            "region":            ["UNKNOWN_REGION"],
        })
        result = encoder.transform(test_df)
        assert result["employment_status"].iloc[0] == -1
        assert result["region"].iloc[0] == -1


# ---------------------------------------------------------------------------
# Tests: Scaling
# ---------------------------------------------------------------------------
class TestScaling:

    def test_scaled_features_near_zero_mean(self, tmp_path):
        from features.feature_engineering import NumericScaler
        rng = np.random.default_rng(42)
        df = pd.DataFrame({
            "discretionary_income":  rng.uniform(-1000, 5000, 100),
            "price":                 rng.uniform(10, 1000, 100),
            "savings_to_price_ratio":rng.uniform(0.1, 50, 100),
            "credit_risk_indicator": rng.uniform(0, 1, 100),
        })
        scaler = NumericScaler()
        result = scaler.fit_transform(df)
        for col in ["discretionary_income", "price", "savings_to_price_ratio"]:
            if col in result.columns:
                assert abs(result[col].mean()) < 0.1


# ---------------------------------------------------------------------------
# Tests: build_feature_matrix integration
# ---------------------------------------------------------------------------
class TestBuildFeatureMatrix:

    def test_returns_correct_shapes(self, tmp_path):
        from features.feature_engineering import build_feature_matrix
        import config
        config.Config.MODEL_SAVE_DIR = str(tmp_path)
        config.Config.SCENARIO_OUTPUT_PATH = str(tmp_path / "scenarios.csv")
        X, y, raw = build_feature_matrix(
            financial_df=_make_financial_df(50),
            products_df=_make_products_df(30),
            reviews_df=_make_reviews_df(100),
            n_scenarios=200,
        )
        assert len(X) <= 200
        assert len(X) >= 150
        assert len(y) == len(X)
        assert len(raw) == len(X)
        assert not X.isnull().any().any()
        assert set(y.unique()).issubset({"GREEN", "YELLOW", "RED"})

    def test_label_column_not_in_X(self, tmp_path):
        from features.feature_engineering import build_feature_matrix
        import config
        config.Config.MODEL_SAVE_DIR = str(tmp_path)
        config.Config.SCENARIO_OUTPUT_PATH = str(tmp_path / "scenarios.csv")
        X, y, _ = build_feature_matrix(
            financial_df=_make_financial_df(50),
            products_df=_make_products_df(30),
            reviews_df=_make_reviews_df(100),
            n_scenarios=100,
        )
        assert "financial_label" not in X.columns

    def test_no_id_columns_in_X(self, tmp_path):
        from features.feature_engineering import build_feature_matrix
        import config
        config.Config.MODEL_SAVE_DIR = str(tmp_path)
        config.Config.SCENARIO_OUTPUT_PATH = str(tmp_path / "scenarios.csv")
        X, _, _ = build_feature_matrix(
            financial_df=_make_financial_df(50),
            products_df=_make_products_df(30),
            reviews_df=_make_reviews_df(100),
            n_scenarios=100,
        )
        assert "user_id" not in X.columns
        assert "product_id" not in X.columns

    def test_computed_features_present_in_X(self, tmp_path):
        from features.feature_engineering import build_feature_matrix
        import config
        config.Config.MODEL_SAVE_DIR = str(tmp_path)
        config.Config.SCENARIO_OUTPUT_PATH = str(tmp_path / "scenarios.csv")
        X, _, _ = build_feature_matrix(
            financial_df=_make_financial_df(50),
            products_df=_make_products_df(30),
            reviews_df=_make_reviews_df(100),
            n_scenarios=100,
        )
        for col in [
            "affordability_score", "price_to_income_ratio",
            "residual_utility_score", "savings_to_price_ratio",
            "net_worth_indicator", "credit_risk_indicator",
        ]:
            assert col in X.columns, f"Missing computed feature in X: {col}"
        assert "review_confidence_score" not in X.columns
        assert "review_polarization_index" not in X.columns
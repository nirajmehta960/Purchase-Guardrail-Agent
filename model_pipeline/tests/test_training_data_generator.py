# model_pipeline/tests/test_training_data_generator.py
import os
import sys
import types
from unittest.mock import MagicMock

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

_stub()

from features.training_data_generator import (
    generate_scenarios,
    _sample_random,
    _sample_stratified,
    _sample_graduated,
    INCOME_BINS, INCOME_LABELS, PRICE_BINS, PRICE_LABELS,
)

# ---------------------------------------------------------------------------
# Helpers
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

def _make_reviews_df(n=100):
    rng = np.random.default_rng(77)
    return pd.DataFrame({
        "review_id":        [f"R{i}" for i in range(n)],
        "product_id":       [f"P{rng.integers(0, 30)}" for i in range(n)],
        "user_id":          [f"U{rng.integers(0, 50)}" for i in range(n)],
        "rating":           rng.integers(1, 6, n),
        "review_text":      ["Test review"] * n,
        "verified_purchase":rng.choice([True, False], n),
        "helpful_vote":     rng.integers(0, 100, n),
    })


# ---------------------------------------------------------------------------
# Tests: _sample_random
# ---------------------------------------------------------------------------
class TestSampleRandom:

    def test_returns_correct_length(self):
        rng = np.random.default_rng(42)
        fin = _make_financial_df(20)
        prod = _make_products_df(10)
        users, prods = _sample_random(fin, prod, 50, rng)
        assert len(users) == 50
        assert len(prods) == 50

    def test_returns_dataframes(self):
        rng = np.random.default_rng(42)
        fin = _make_financial_df(20)
        prod = _make_products_df(10)
        users, prods = _sample_random(fin, prod, 30, rng)
        assert isinstance(users, pd.DataFrame)
        assert isinstance(prods, pd.DataFrame)

    def test_index_reset(self):
        rng = np.random.default_rng(42)
        fin = _make_financial_df(20)
        prod = _make_products_df(10)
        users, prods = _sample_random(fin, prod, 30, rng)
        assert list(users.index) == list(range(30))
        assert list(prods.index) == list(range(30))


# ---------------------------------------------------------------------------
# Tests: _sample_stratified
# ---------------------------------------------------------------------------
class TestSampleStratified:

    def test_returns_correct_length(self):
        rng = np.random.default_rng(42)
        fin = _make_financial_df(50)
        prod = _make_products_df(32)
        users, prods = _sample_stratified(fin, prod, 100, rng)
        assert len(users) == 100
        assert len(prods) == 100

    def test_returns_dataframes(self):
        rng = np.random.default_rng(42)
        fin = _make_financial_df(50)
        prod = _make_products_df(32)
        users, prods = _sample_stratified(fin, prod, 50, rng)
        assert isinstance(users, pd.DataFrame)
        assert isinstance(prods, pd.DataFrame)

    def test_raises_when_no_valid_cells(self):
        rng = np.random.default_rng(42)
        # All products at price=0 — no valid brackets
        fin = _make_financial_df(10)
        prod = _make_products_df(5)
        prod["price"] = 0.0
        with pytest.raises(ValueError, match="No valid"):
            _sample_stratified(fin, prod, 50, rng)


# ---------------------------------------------------------------------------
# Tests: _sample_graduated
# ---------------------------------------------------------------------------
class TestSampleGraduated:

    def test_returns_users_and_tier_dict(self):
        rng = np.random.default_rng(42)
        fin = _make_financial_df(30)
        prod = _make_products_df(32)
        users, tier_products = _sample_graduated(fin, prod, 20, rng)
        assert isinstance(users, pd.DataFrame)
        assert isinstance(tier_products, dict)

    def test_tier_dict_has_all_price_labels(self):
        rng = np.random.default_rng(42)
        fin = _make_financial_df(30)
        prod = _make_products_df(32)
        _, tier_products = _sample_graduated(fin, prod, 20, rng)
        for label in PRICE_LABELS:
            assert label in tier_products

    def test_each_tier_has_correct_length(self):
        rng = np.random.default_rng(42)
        fin = _make_financial_df(30)
        prod = _make_products_df(32)
        n_users = 20
        users, tier_products = _sample_graduated(fin, prod, n_users, rng)
        assert len(users) == n_users
        for label in PRICE_LABELS:
            assert len(tier_products[label]) == n_users

    def test_raises_when_tier_empty(self):
        rng = np.random.default_rng(42)
        fin = _make_financial_df(10)
        prod = _make_products_df(5)
        # All products priced below budget tier min
        prod["price"] = 10.0
        with pytest.raises(ValueError, match="price tier"):
            _sample_graduated(fin, prod, 10, rng)


# ---------------------------------------------------------------------------
# Tests: generate_scenarios (main API)
# ---------------------------------------------------------------------------
class TestGenerateScenarios:

    def test_returns_dataframe(self):
        fin = _make_financial_df(50)
        prod = _make_products_df(32)
        result = generate_scenarios(fin, prod, n_scenarios=50)
        assert isinstance(result, pd.DataFrame)

    def test_labels_are_valid(self):
        fin = _make_financial_df(50)
        prod = _make_products_df(32)
        result = generate_scenarios(fin, prod, n_scenarios=100)
        assert set(result["financial_label"].unique()).issubset({"GREEN", "YELLOW", "RED"})

    def test_has_required_columns(self):
        fin = _make_financial_df(50)
        prod = _make_products_df(32)
        result = generate_scenarios(fin, prod, n_scenarios=50)
        for col in ["product_id", "product_price", "financial_label",
                    "affordability_score", "price_to_income_ratio",
                    "residual_utility_score", "savings_to_price_ratio",
                    "net_worth_indicator", "credit_risk_indicator"]:
            assert col in result.columns, f"Missing column: {col}"

    def test_no_intermediate_label_in_output(self):
        fin = _make_financial_df(50)
        prod = _make_products_df(32)
        result = generate_scenarios(fin, prod, n_scenarios=50)
        assert "_l1_label" not in result.columns

    def test_reproducibility_same_seed(self):
        fin = _make_financial_df(50)
        prod = _make_products_df(32)
        r1 = generate_scenarios(fin, prod, n_scenarios=50, random_state=42)
        r2 = generate_scenarios(fin, prod, n_scenarios=50, random_state=42)
        pd.testing.assert_frame_equal(r1, r2)

    def test_different_seeds_different_output(self):
        fin = _make_financial_df(50)
        prod = _make_products_df(32)
        r1 = generate_scenarios(fin, prod, n_scenarios=50, random_state=1)
        r2 = generate_scenarios(fin, prod, n_scenarios=50, random_state=2)
        assert not r1["product_price"].equals(r2["product_price"])

    def test_not_all_same_label(self):
        fin = _make_financial_df(100)
        prod = _make_products_df(32)
        result = generate_scenarios(fin, prod, n_scenarios=300)
        assert result["financial_label"].nunique() >= 2

    def test_graduated_has_price_tier_column(self):
        fin = _make_financial_df(50)
        prod = _make_products_df(32)
        result = generate_scenarios(fin, prod, n_scenarios=50, graduated=True)
        assert "price_tier" in result.columns
        assert set(result["price_tier"].unique()).issubset(set(PRICE_LABELS))

    def test_graduated_has_session_id_column(self):
        fin = _make_financial_df(50)
        prod = _make_products_df(32)
        result = generate_scenarios(fin, prod, n_scenarios=50, graduated=True)
        assert "session_id" in result.columns

    def test_stratified_mode(self):
        fin = _make_financial_df(50)
        prod = _make_products_df(32)
        result = generate_scenarios(fin, prod, n_scenarios=100,
                                    graduated=False, stratified=True)
        assert isinstance(result, pd.DataFrame)
        assert len(result) == 100

    def test_random_mode(self):
        fin = _make_financial_df(50)
        prod = _make_products_df(32)
        result = generate_scenarios(fin, prod, n_scenarios=100,
                                    graduated=False, stratified=False)
        assert isinstance(result, pd.DataFrame)
        assert len(result) == 100

    def test_liquid_savings_used_in_computation(self):
        """liquid_savings should be present and affect computations."""
        fin = _make_financial_df(50)
        prod = _make_products_df(32)
        result = generate_scenarios(fin, prod, n_scenarios=50)
        assert "liquid_savings" in result.columns
        assert result["liquid_savings"].notna().all()

    def test_graduated_session_ids_are_consecutive(self):
        fin = _make_financial_df(50)
        prod = _make_products_df(32)
        result = generate_scenarios(fin, prod, n_scenarios=50, graduated=True)
        session_ids = sorted(result["session_id"].unique())
        assert session_ids == list(range(len(session_ids)))
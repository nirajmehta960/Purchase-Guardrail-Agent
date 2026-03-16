"""
Feature Engineering — Model Pipeline (Phase 2).

Transforms raw scenario pairs into a model-ready feature matrix with
deterministic GREEN/YELLOW/RED labels.

Pipeline:
    1. generate_training_data() — Load data, sample pairs, compute features, label
    2. transform_features()     — Impute, encode, scale, drop non-features
    3. build_feature_matrix()   — Orchestrator calls 1 then 2 and returns (X, y, scenarios_raw)

Input:  PostgreSQL tables — financial_profiles, products, reviews
Output: Feature matrix (X), label vector (y), raw scenarios CSV
"""

import os
import logging

import joblib
import numpy as np
import pandas as pd
from sklearn.base import BaseEstimator, TransformerMixin
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OrdinalEncoder, StandardScaler

from config import Config
from features.training_data_generator import generate_scenarios
from features.product_features import compute_product_features_batch
from features.review_features import compute_review_features_batch
from deterministic_engine.financial_engine import DecisionEngine
from deterministic_engine.downgrade_engine import DowngradeEngine
from data.db_loader import load_financial_profiles, load_products, load_reviews


logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Scikit-Learn Compatible Transformers
# ---------------------------------------------------------------------------

class MissingValueImputer(BaseEstimator, TransformerMixin):
    """
    Impute missing values following the project conventions.

    Strategy:
        - Financial numeric fields: fill with column median.
        - Product numeric fields: fill 0 for rating_variance, median for others.
        - Computed financial features: fill with column median to avoid
          injecting false signals (0.0 has real semantic meaning for ratios).
        - Categorical fields: fill with 'Unknown'.
    """
    def fit(self, X: pd.DataFrame, y=None):
        return self

    def transform(self, X: pd.DataFrame) -> pd.DataFrame:
        df = X.copy()

        for col in Config.FINANCIAL_FEATURES:
            if col in df.columns and df[col].isnull().any():
                df[col] = df[col].fillna(df[col].median())

        for col in Config.PRODUCT_FEATURES:
            if col in df.columns and df[col].isnull().any():
                if col == "rating_variance":
                    df[col] = df[col].fillna(0.0)
                else:
                    df[col] = df[col].fillna(df[col].median())

        computed_features = [
            "affordability_score", "price_to_income_ratio", "residual_utility_score",
            "savings_to_price_ratio", "net_worth_indicator", "credit_risk_indicator",
        ] + Config.PRODUCT_COMPUTED_FEATURES + Config.REVIEW_COMPUTED_FEATURES
        for col in computed_features:
            if col in df.columns and df[col].isnull().any():
                df[col] = df[col].fillna(df[col].median())

        for col in Config.CATEGORICAL_FEATURES:
            if col in df.columns:
                df[col] = df[col].fillna("Unknown")

        return df


class CategoricalEncoder(BaseEstimator, TransformerMixin):
    """Ordinal-encode categorical features. Unknown categories mapped to -1."""

    def __init__(self):
        self.encoder = OrdinalEncoder(handle_unknown="use_encoded_value", unknown_value=-1)
        self.existing_cat_cols = []

    def fit(self, X: pd.DataFrame, y=None):
        self.existing_cat_cols = [c for c in Config.CATEGORICAL_FEATURES if c in X.columns]
        if self.existing_cat_cols:
            self.encoder.fit(X[self.existing_cat_cols])
        return self

    def transform(self, X: pd.DataFrame) -> pd.DataFrame:
        df = X.copy()
        cols = [c for c in self.existing_cat_cols if c in df.columns]
        if cols:
            df[cols] = self.encoder.transform(df[cols])
        return df


class NumericScaler(BaseEstimator, TransformerMixin):
    """Scale numeric features to zero mean and unit variance."""

    def __init__(self):
        self.scaler = StandardScaler()
        self.numeric_cols = []

    def fit(self, X: pd.DataFrame, y=None):
        self.numeric_cols = [c for c in Config.NUMERIC_FEATURES if c in X.columns]
        if self.numeric_cols:
            self.scaler.fit(X[self.numeric_cols])
        return self

    def transform(self, X: pd.DataFrame) -> pd.DataFrame:
        df = X.copy()
        cols = [c for c in self.numeric_cols if c in df.columns]
        if cols:
            df[cols] = self.scaler.transform(df[cols])
        return df


class FeatureDropper(BaseEstimator, TransformerMixin):
    """Drop non-feature columns (IDs, text blobs, labels)."""

    def fit(self, X: pd.DataFrame, y=None):
        return self

    def transform(self, X: pd.DataFrame) -> pd.DataFrame:
        df = X.copy()
        cols_to_drop = [c for c in Config.COLUMNS_TO_DROP + [Config.LABEL_COL]
                        if c in df.columns]
        if "product_price" in df.columns:
            cols_to_drop.append("product_price")
        return df.drop(columns=cols_to_drop, errors="ignore")


class FeaturePipeline:
    """
    Main transformation pipeline wrapping strict scikit-learn transformers.
    Saves/loads pipeline state to disk to ensure training and inference match perfectly.
    """
    def __init__(self):
        self.pipeline = Pipeline([
            ('imputer', MissingValueImputer()),
            ('encoder', CategoricalEncoder()),
            ('scaler', NumericScaler()),
            ('dropper', FeatureDropper()),
        ])

    def save(self, path=None):
        path = path or os.path.join(Config.MODEL_SAVE_DIR, "feature_pipeline.pkl")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        joblib.dump(self.pipeline, path)
        logger.info("Feature pipeline saved to %s", path)

    def load(self, path=None):
        path = path or os.path.join(Config.MODEL_SAVE_DIR, "feature_pipeline.pkl")
        self.pipeline = joblib.load(path)
        logger.info("Feature pipeline loaded from %s", path)

    def fit_transform(self, X: pd.DataFrame, save_pipeline: bool = True) -> pd.DataFrame:
        transformed = self.pipeline.fit_transform(X)
        if save_pipeline:
            self.save()
        return transformed

    def transform(self, X: pd.DataFrame) -> pd.DataFrame:
        self.load()
        return self.pipeline.transform(X)


# ---------------------------------------------------------------------------
# Feature Computation & Labeling
# ---------------------------------------------------------------------------

def compute_scenario_features(scenarios: pd.DataFrame) -> pd.DataFrame:
    """
    Compute the 6 financial features for each user-product scenario pair.

    Takes a raw scenario DataFrame (user columns + product columns) and
    adds: affordability_score, price_to_income_ratio, residual_utility_score,
    savings_to_price_ratio, net_worth_indicator, credit_risk_indicator.
    """
    df = scenarios.copy()

    price = df["product_price"]
    income = df["monthly_income"].replace(0, np.nan)
    expenses = df["monthly_expenses"]
    emi = df["monthly_emi"]
    loan_amount = df["loan_amount"].fillna(0)
    credit_score = df["credit_score"].fillna(0)
    total_obligations = (expenses + emi).replace(0, np.nan)
    safe_price = price.replace(0, np.nan)

    discretionary = df["discretionary_income"]
    savings = df["liquid_savings"]

    df["affordability_score"] = discretionary - price
    df["price_to_income_ratio"] = price / income
    df["residual_utility_score"] = (savings - price) / total_obligations
    df["savings_to_price_ratio"] = savings / safe_price
    df["net_worth_indicator"] = (savings - loan_amount) / income
    df["credit_risk_indicator"] = (credit_score - 299) / 550.0

    logger.info("Computed 6 financial features for %d scenarios.", len(df))
    return df


def _apply_layer2(
    scenarios: pd.DataFrame,
    products_df: pd.DataFrame,
    reviews_df: pd.DataFrame,
) -> pd.DataFrame:
    """Apply Layer 2 product/review downgrade logic to labeled scenarios."""
    downgrade_engine = DowngradeEngine()

    unique_prods = products_df.drop_duplicates(subset=["product_id"]).copy()
    product_feats_df = compute_product_features_batch(unique_prods)
    product_feats_df = product_feats_df.set_index("product_id")

    review_feats_df = compute_review_features_batch(reviews_df)

    scenarios = scenarios.merge(
        product_feats_df[
            [
                "value_density",
                "review_confidence",
                "rating_polarization",
                "quality_risk_score",
                "cold_start_flag",
                "price_category_rank",
                "category_rating_deviation",
            ]
        ],
        left_on="product_id",
        right_index=True,
        how="left",
    )

    scenarios = scenarios.merge(
        review_feats_df[
            [
                "verified_purchase_ratio",
                "helpful_concentration",
                "sentiment_spread",
                "review_depth_score",
                "reviewer_diversity",
                "extreme_rating_ratio",
            ]
        ],
        left_on="product_id",
        right_index=True,
        how="left",
    )

    logger.info("Applying DowngradeEngine (Layer 2)...")

    def _apply_downgrade(row: pd.Series) -> pd.Series:
        class PF:
            pass

        class RF:
            pass

        pf = PF()
        pf.value_density = row["value_density"]
        pf.review_confidence = row["review_confidence"]
        pf.rating_polarization = row["rating_polarization"]
        pf.quality_risk_score = row["quality_risk_score"]
        pf.cold_start_flag = int(row["cold_start_flag"])
        pf.price_category_rank = row["price_category_rank"]
        pf.category_rating_deviation = row["category_rating_deviation"]

        rf = RF()
        rf.verified_purchase_ratio = row["verified_purchase_ratio"]
        rf.helpful_concentration = row["helpful_concentration"]
        rf.sentiment_spread = row["sentiment_spread"]
        rf.review_depth_score = row["review_depth_score"]
        rf.reviewer_diversity = row["reviewer_diversity"]
        rf.extreme_rating_ratio = row["extreme_rating_ratio"]

        result = downgrade_engine.evaluate(
            financial_label=row["_l1_label"],
            product_features=pf,
            review_features=rf,
        )
        return pd.Series({
            "final_recommendation": result.final_label,
            "downgraded": int(result.final_label != row["_l1_label"]),
        })

    applied = scenarios.apply(_apply_downgrade, axis=1)
    scenarios["final_recommendation"] = applied["final_recommendation"]
    scenarios["downgraded"] = applied["downgraded"]
    return scenarios


def apply_deterministic_labels(
    scenarios: pd.DataFrame,
    products_df: pd.DataFrame,
    reviews_df: pd.DataFrame = None,
) -> pd.DataFrame:
    """
    Apply deterministic engine labels to scenarios with pre-computed features.

    Layer 1: DecisionEngine assigns GREEN/YELLOW/RED based on financial features.
    Layer 2: DowngradeEngine may downgrade labels based on product/review quality.
    """
    engine = DecisionEngine()

    df = scenarios.copy()
    df["_l1_label"] = df.apply(engine.decide_row, axis=1)
    df["final_recommendation"] = df["_l1_label"]

    if reviews_df is not None:
        df = _apply_layer2(df, products_df, reviews_df)
    else:
        df["downgraded"] = 0

    df = df.drop(columns=["_l1_label"], errors="ignore")
    return df


# ---------------------------------------------------------------------------
# Functions for Downstream Use
# ---------------------------------------------------------------------------

def generate_training_data(
    financial_df: pd.DataFrame = None,
    products_df: pd.DataFrame = None,
    reviews_df: pd.DataFrame = None,
    n_scenarios: int = None,
    random_state: int = None,
    output_path: str = None,
):
    """
    End-to-end training data creation: sample pairs → compute features → label.

    Steps:
        1. Load data from PostgreSQL (if DataFrames not provided).
        2. Sample user-product pairs via generate_scenarios().
        3. Compute 6 financial features.
        4. Apply Layer 1 + Layer 2 deterministic labels.
        5. Save labeled scenarios to CSV.
    """
    if n_scenarios is None:
        n_scenarios = Config.N_SCENARIOS
    if random_state is None:
        random_state = Config.RANDOM_STATE

    if financial_df is None or products_df is None or reviews_df is None:
        logger.info("Loading data from PostgreSQL...")
        financial_df = financial_df if financial_df is not None else load_financial_profiles()
        products_df = products_df if products_df is not None else load_products()
        reviews_df = reviews_df if reviews_df is not None else load_reviews()

    logger.info("Generating %d scenarios (stratified sampling)...", n_scenarios)
    scenarios_raw = generate_scenarios(
        financial_df,
        products_df,
        n_scenarios=n_scenarios,
        random_state=random_state,
    )

    scenarios_raw = compute_scenario_features(scenarios_raw)

    scenarios_raw = apply_deterministic_labels(scenarios_raw, products_df, reviews_df)

    if output_path:
        os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
        scenarios_raw.to_csv(output_path, index=False)
        logger.info("Saved raw scenarios to %s", output_path)

    y = scenarios_raw[Config.LABEL_COL].copy()
    logger.info("Training data generated — %d scenarios", len(scenarios_raw))

    return scenarios_raw, y


def transform_features(
    scenarios: pd.DataFrame,
    y: pd.Series,
    is_training: bool = True,
):
    """
    Transform raw scenarios into a model-ready feature matrix using the pipeline.
    """
    pipeline = FeaturePipeline()
    if is_training:
        X = pipeline.fit_transform(scenarios)
    else:
        X = pipeline.transform(scenarios)

    logger.info("Feature matrix built — X shape: %s", X.shape)
    return X, y


def build_feature_matrix(
    financial_df: pd.DataFrame = None,
    products_df: pd.DataFrame = None,
    reviews_df: pd.DataFrame = None,
    n_scenarios: int = None,
    is_training: bool = True,
):
    """
    End-to-end feature engineering pipeline. Orchestrates loading then transformation.
    """
    scenarios_raw, y = generate_training_data(
        financial_df=financial_df,
        products_df=products_df,
        reviews_df=reviews_df,
        n_scenarios=n_scenarios,
    )
    X, y = transform_features(scenarios_raw, y, is_training=is_training)

    logger.info("build_feature_matrix complete — X: %s", X.shape)
    return X, y, scenarios_raw

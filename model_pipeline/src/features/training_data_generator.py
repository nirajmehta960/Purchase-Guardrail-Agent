"""
Training Data Generator — Scenario Creation & Labeling.

Generates synthetic user-product scenarios by pairing real financial
profiles with real products, computes the 6 financial features for each
pair, and labels each scenario GREEN/YELLOW/RED using the DecisionEngine.

Supports three sampling strategies:
    - graduated (default): Each user evaluates one product per price
      tier (budget → mid → premium).  Cumulative spending depletes
      savings after each GREEN/YELLOW purchase.  Evaluation stops on
      the first RED, avoiding redundant rows.
    - stratified: Equal representation across income × price bracket
      combinations (3 income × 3 price = 9 cells).
    - random: Pure uniform random pairing (legacy / quick experiments).

Output becomes the training dataset for downstream ML models.

Usage:
    from features.training_data_generator import generate_scenarios

    scenarios_df = generate_scenarios(financial_df, products_df, n_scenarios=50000)
"""

import logging
from typing import Dict, List, Tuple, Optional

import numpy as np
import pandas as pd

from deterministic_engine.financial_engine import DecisionEngine
from deterministic_engine.downgrade_engine import DowngradeEngine
from features.product_features import compute_product_features_batch
from features.review_features import compute_review_features_batch

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Bracket definitions
# ---------------------------------------------------------------------------

INCOME_BINS: List[float] = [0, 3_000, 7_000, float("inf")]
INCOME_LABELS: List[str] = ["low", "mid", "high"]

PRICE_BINS: List[float] = [100, 500, 1_500, float("inf")]
PRICE_LABELS: List[str] = ["budget", "mid", "premium"]

# ---------------------------------------------------------------------------
# Sampling strategies
# ---------------------------------------------------------------------------

def _sample_random(
    financial_profiles: pd.DataFrame,
    products: pd.DataFrame,
    n_scenarios: int,
    rng: np.random.Generator,
) -> Tuple[pd.DataFrame, pd.DataFrame]:
    """
    Pure uniform random pairing — legacy behaviour.
    
    Args:
        financial_profiles: DataFrame containing user financial profiles.
        products: DataFrame containing product data.
        n_scenarios: Total number of scenarios to generate.
        rng: NumPy random generator instance for reproducibility.
        
    Returns:
        A tuple of (sampled_users_df, sampled_products_df), both of length n_scenarios.
    """
    user_idx = rng.integers(0, len(financial_profiles), size=n_scenarios)
    prod_idx = rng.integers(0, len(products), size=n_scenarios)
    return (
        financial_profiles.iloc[user_idx].reset_index(drop=True),
        products.iloc[prod_idx].reset_index(drop=True),
    )


def _sample_stratified(
    financial_profiles: pd.DataFrame,
    products: pd.DataFrame,
    n_scenarios: int,
    rng: np.random.Generator,
) -> Tuple[pd.DataFrame, pd.DataFrame]:
    """
    Stratified pairing across income × price bracket combinations.

    Divides users into 3 income brackets and products into 3 price
    brackets, then samples equally from each of the 9 (income, price)
    cells so edge-case combinations are well represented.

    Empty cells are skipped and their quota is redistributed evenly
    across remaining cells.
    
    Args:
        financial_profiles: DataFrame containing user financial profiles.
        products: DataFrame containing product data.
        n_scenarios: Total number of scenarios to generate.
        rng: NumPy random generator instance for reproducibility.
        
    Returns:
        A tuple of (sampled_users_df, sampled_products_df), both of length n_scenarios.
    """
    # Assign brackets.
    income_bracket = pd.cut(
        financial_profiles["monthly_income"],
        bins=INCOME_BINS, labels=INCOME_LABELS, include_lowest=True,
    )
    price_bracket = pd.cut(
        products["price"],
        bins=PRICE_BINS, labels=PRICE_LABELS, include_lowest=True,
    )

    income_groups = {
        label: financial_profiles[income_bracket == label]
        for label in INCOME_LABELS
    }
    price_groups = {
        label: products[price_bracket == label]
        for label in PRICE_LABELS
    }

    # Identify non-empty cells.
    valid_cells = [
        (ig, pg)
        for ig in INCOME_LABELS
        for pg in PRICE_LABELS
        if len(income_groups[ig]) > 0 and len(price_groups[pg]) > 0
    ]

    if not valid_cells:
        raise ValueError(
            "No valid (income, price) bracket combinations — check your data."
        )

    # Distribute n_scenarios evenly; spread remainder across first cells.
    base, remainder = divmod(n_scenarios, len(valid_cells))
    cell_counts = [base + (1 if i < remainder else 0) for i in range(len(valid_cells))]

    user_chunks: List[pd.DataFrame] = []
    prod_chunks: List[pd.DataFrame] = []

    for (ig_label, pg_label), count in zip(valid_cells, cell_counts):
        # Draw reproducible integer seeds from the generator so
        # pd.DataFrame.sample() stays deterministic per cell.
        seed = int(rng.integers(0, 2**31))

        user_sample = income_groups[ig_label].sample(
            n=count, replace=True, random_state=seed,
        )
        prod_sample = price_groups[pg_label].sample(
            n=count, replace=True, random_state=seed + 1,
        )
        user_chunks.append(user_sample)
        prod_chunks.append(prod_sample)

        logger.debug(
            "Cell (%s income, %s price): %d scenarios",
            ig_label, pg_label, count,
        )

    users = pd.concat(user_chunks, ignore_index=True)
    prods = pd.concat(prod_chunks, ignore_index=True)

    logger.info(
        "Stratified sampling: %d valid cells out of 9, %d total scenarios",
        len(valid_cells), len(users),
    )
    return users, prods


def _sample_graduated(
    financial_profiles: pd.DataFrame,
    products: pd.DataFrame,
    n_users: int,
    rng: np.random.Generator,
) -> Tuple[pd.DataFrame, Dict[str, pd.DataFrame]]:
    """
    Graduated price-tier sampling with Stratified User Selection.

    Each user session is selected from a specific income bracket (Low, Mid, High)
    to ensure equal representation, and then assigned exactly one product 
    from each price tier (Budget, Mid, Premium).

    Args:
        financial_profiles: DataFrame with user financial profiles.
        products: DataFrame with product data.
        n_users: Number of unique user sessions to create.
        rng: NumPy random generator for reproducibility.

    Returns:
        (users_df, tier_products) where tier_products maps each 
        PRICE_LABEL to a DataFrame of length n_users.
    """
    # 1. Stratify Users by Income Bracket
    income_bracket_series = pd.cut(
        financial_profiles["monthly_income"],
        bins=INCOME_BINS, labels=INCOME_LABELS, include_lowest=True,
    )
    
    user_groups = {
        label: financial_profiles[income_bracket_series == label]
        for label in INCOME_LABELS
    }
    
    # Identify non-empty income bins.
    valid_income_labels = [l for l in INCOME_LABELS if len(user_groups[l]) > 0]
    if not valid_income_labels:
        raise ValueError("No users found in any income bracket — check INCOME_BINS.")

    # Distribute users evenly across brackets.
    base, remainder = divmod(n_users, len(valid_income_labels))
    
    user_chunks: List[pd.DataFrame] = []
    for i, label in enumerate(valid_income_labels):
        count = base + (1 if i < remainder else 0)
        seed = int(rng.integers(0, 2**31))
        # Sample users for this bracket.
        chunk = user_groups[label].sample(n=count, replace=True, random_state=seed)
        user_chunks.append(chunk)

    users = pd.concat(user_chunks, ignore_index=True)

    # 2. Assign tiered products for each user.
    price_bracket = pd.cut(
        products["price"],
        bins=PRICE_BINS,
        labels=PRICE_LABELS,
        include_lowest=True,
    )

    tier_products: Dict[str, pd.DataFrame] = {}
    for label in PRICE_LABELS:
        tier_pool = products[price_bracket == label]
        if len(tier_pool) == 0:
            raise ValueError(
                f"No products in the '{label}' price tier "
                f"(bins={PRICE_BINS}). Check your product data."
            )
        idx = rng.integers(0, len(tier_pool), size=n_users)
        tier_products[label] = tier_pool.iloc[idx].reset_index(drop=True)

    logger.info(
        "Stratified-Graduated sampling: %d users total (%s balanced), %d tiers per session.",
        n_users,
        ", ".join(valid_income_labels),
        len(PRICE_LABELS),
    )
    return users, tier_products


# ---------------------------------------------------------------------------
# Feature computation & labeling
# ---------------------------------------------------------------------------

def _compute_round(
    users: pd.DataFrame,
    prods: pd.DataFrame,
    cum_di_spent: pd.Series,
    cum_savings_spent: pd.Series,
    engine: DecisionEngine,
) -> pd.DataFrame:
    """
    Vectorized feature computation + Layer 1 labeling for a single
    price tier (one round in a graduated session).

    Spending priority: DI absorbs purchases first; liquid_savings
    covers the shortfall only when DI is insufficient.

    ``cum_di_spent`` / ``cum_savings_spent`` must be Series aligned
    with ``users``, tracking how much of each source prior rounds
    have consumed.
    """
    scenarios = users.copy()
    scenarios["product_id"] = prods["product_id"].values
    scenarios["product_price"] = prods["price"].values

    for col in ("average_rating", "rating_number", "rating_variance"):
        if col in prods.columns:
            scenarios[col] = prods[col].values

    price = scenarios["product_price"]
    income = scenarios["monthly_income"].replace(0, np.nan)
    expenses = scenarios["monthly_expenses"]
    emi = scenarios["monthly_emi"]
    loan_amount = scenarios["loan_amount"].fillna(0)
    credit_score = scenarios["credit_score"].fillna(0)
    total_obligations = (expenses + emi).replace(0, np.nan)
    safe_price = price.replace(0, np.nan)

    # Available resources at decision time (after prior purchases).
    discretionary = scenarios["discretionary_income"] - cum_di_spent.values
    savings = (scenarios["liquid_savings"] - cum_savings_spent.values).clip(lower=0)

    scenarios["liquid_savings"] = savings
    scenarios["discretionary_income"] = discretionary

    scenarios["saving_to_income_ratio"] = savings / income
    scenarios["emergency_fund_months"] = savings / total_obligations

    scenarios["affordability_score"] = discretionary - price
    scenarios["price_to_income_ratio"] = price / income
    scenarios["residual_utility_score"] = (savings - price) / total_obligations
    scenarios["savings_to_price_ratio"] = savings / safe_price
    scenarios["net_worth_indicator"] = (savings - loan_amount) / income
    scenarios["credit_risk_indicator"] = (credit_score - 299) / 550.0

    scenarios["_l1_label"] = scenarios.apply(engine.decide_row, axis=1)
    scenarios["final_recommendation"] = scenarios["_l1_label"]

    return scenarios


def _compute_graduated_scenarios(
    users: pd.DataFrame,
    tier_products: Dict[str, pd.DataFrame],
) -> pd.DataFrame:
    """
    3-round graduated evaluation with RED early-stop.

    Round 1 (budget): all users evaluated with cumulative_spend = 0.
    Round 2 (mid): only users who were NOT RED in round 1.
    Round 3 (premium): only users who were NOT RED in rounds 1 or 2.

    Cumulative spending carries forward across rounds.

    Output is sorted by (session_id, tier_order) so each user's
    graduated journey reads budget → mid → premium on consecutive rows.
    """
    engine = DecisionEngine()
    all_rounds: List[pd.DataFrame] = []

    n = len(users)
    active = np.ones(n, dtype=bool)
    cum_di_spent = np.zeros(n)
    cum_savings_spent = np.zeros(n)

    # Original values needed to compute the DI / savings split each round.
    orig_di = users["discretionary_income"].values.copy()
    orig_savings = users["liquid_savings"].values.copy()

    tier_order_map = {label: i for i, label in enumerate(PRICE_LABELS)}

    for tier_label in PRICE_LABELS:
        if not active.any():
            break

        prods = tier_products[tier_label]
        active_indices = np.where(active)[0]

        active_users = users[active].reset_index(drop=True)
        active_prods = prods[active].reset_index(drop=True)
        active_cum_di = pd.Series(cum_di_spent[active]).reset_index(drop=True)
        active_cum_sav = pd.Series(cum_savings_spent[active]).reset_index(drop=True)

        round_df = _compute_round(
            active_users, active_prods, active_cum_di, active_cum_sav, engine,
        )
        round_df["price_tier"] = tier_label
        round_df["session_id"] = active_indices
        round_df["_tier_order"] = tier_order_map[tier_label]
        all_rounds.append(round_df)

        labels = round_df["_l1_label"].values
        prices = round_df["product_price"].values

        for i, orig_idx in enumerate(active_indices):
            if labels[i] == "RED":
                active[orig_idx] = False
            else:
                # DI absorbs the purchase first; savings cover the shortfall.
                avail_di = max(orig_di[orig_idx] - cum_di_spent[orig_idx], 0.0)
                di_used = min(avail_di, prices[i])
                savings_used = prices[i] - di_used
                cum_di_spent[orig_idx] += di_used
                cum_savings_spent[orig_idx] += savings_used

    result = pd.concat(all_rounds, ignore_index=True)
    result = result.sort_values(
        ["session_id", "_tier_order"], ignore_index=True,
    )
    result = result.drop(columns=["_tier_order"])

    n_users = len(users)
    red_stopped = int((~active).sum())
    logger.info(
        "Graduated scenarios: %d total rows from %d users across %d tiers "
        "(%d users stopped early by RED)",
        len(result), n_users, len(PRICE_LABELS), red_stopped,
    )
    return result


def _apply_layer2(
    scenarios: pd.DataFrame,
    prods: pd.DataFrame,
    reviews: pd.DataFrame,
) -> pd.DataFrame:
    """Apply Layer 2 product/review downgrade logic to labeled scenarios."""
    downgrade_engine = DowngradeEngine()

    unique_prods = prods.drop_duplicates(subset=["product_id"]).copy()
    product_feats_df = compute_product_features_batch(unique_prods)
    product_feats_df = product_feats_df.set_index("product_id")

    review_feats_df = compute_review_features_batch(reviews)

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

    def _apply_downgrade(row: pd.Series) -> str:
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


def _compute_features_and_label(
    users: pd.DataFrame,
    prods: pd.DataFrame,
    reviews: Optional[pd.DataFrame] = None,
) -> pd.DataFrame:
    """
    Given paired user and product DataFrames of equal length, compute the
    6 financial features, apply the Layer 1 financial engine, and
    optionally apply Layer 2 downgrade logic using product/review
    features when reviews are provided.

    Used by legacy sampling paths (stratified, random).
    """
    engine = DecisionEngine()
    n = len(users)
    zero = pd.Series(0.0, index=range(n))
    scenarios = _compute_round(users, prods, zero, zero.copy(), engine)

    if reviews is not None:
        scenarios = _apply_layer2(scenarios, prods, reviews)
    else:
        scenarios["downgraded"] = 0

    scenarios = scenarios.drop(columns=["_l1_label"], errors="ignore")
    return scenarios


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def generate_scenarios(
    financial_profiles: pd.DataFrame,
    products: pd.DataFrame,
    reviews_df: Optional[pd.DataFrame] = None,
    n_scenarios: int = 10_000,
    random_state: int = 42,
    stratified: bool = True,
    graduated: bool = True,
) -> pd.DataFrame:
    """
    Generate synthetic user-product scenarios.

    Pairs real user financial profiles with real products, computes the 6
    financial features for each pair, and labels each scenario
    GREEN/YELLOW/RED using the DecisionEngine.

    When ``graduated`` is True (default), each user evaluates products
    in ascending price tiers (budget → mid → high → premium).
    Cumulative spending depletes savings after each non-RED purchase.
    Evaluation stops on the first RED label, so a user who cannot
    afford a lower tier never sees higher-priced products.  This
    produces realistic multi-purchase sessions with 1–4 rows per user.

    Args:
        financial_profiles: DataFrame from the financial_profiles table.
        products: DataFrame from the products table.
        reviews_df: Optional DataFrame of product reviews for Layer 2.
        n_scenarios: Target number of (user, product) rows to generate.
        random_state: Seed for reproducibility.
        stratified: If True and graduated is False, sample equally
            across 9 (income × price) bracket cells.
        graduated: If True, use graduated price-tier sessions
            (overrides ``stratified``).

    Returns:
        DataFrame with one row per scenario containing user features,
        product columns, 6 computed features, and a rule-based label.
    """
    rng = np.random.default_rng(random_state)

    if graduated:
        # Each user produces 1–K rows (one per tier survived, K = num tiers).
        # RED early-stop means average rows/user < K, so we divide by
        # (K-1) to oversample, then shuffle-truncate to hit n_scenarios.
        n_tiers = len(PRICE_LABELS)
        n_users = max(1, int(np.ceil(n_scenarios / max(n_tiers - 1, 1))))
        users, tier_products = _sample_graduated(
            financial_profiles, products, n_users, rng,
        )
        scenarios = _compute_graduated_scenarios(users, tier_products)

        if reviews_df is not None:
            all_prods = pd.concat(
                list(tier_products.values()), ignore_index=True,
            )
            scenarios = _apply_layer2(scenarios, all_prods, reviews_df)
        else:
            scenarios["downgraded"] = 0

        # Drop the intermediate Layer 1 label; final decision is in `final_recommendation`.
        scenarios = scenarios.drop(columns=["_l1_label"], errors="ignore")

        if len(scenarios) > n_scenarios:
            keep_sessions = scenarios["session_id"].unique()
            cumulative = scenarios.groupby("session_id").size().cumsum()
            n_keep = int((cumulative <= n_scenarios).sum())
            keep_ids = keep_sessions[:n_keep]
            scenarios = scenarios[scenarios["session_id"].isin(keep_ids)]

        # Renumber session_id to be consecutive 0,1,2,...
        session_map = {
            old: new
            for new, old in enumerate(scenarios["session_id"].unique())
        }
        scenarios["session_id"] = scenarios["session_id"].map(session_map)

        # Sort so each user's tiers read budget → mid → high → premium.
        tier_rank = {label: i for i, label in enumerate(PRICE_LABELS)}
        scenarios["_tier_order"] = scenarios["price_tier"].map(tier_rank)
        scenarios = scenarios.sort_values(
            ["session_id", "_tier_order"], ignore_index=True,
        )
        scenarios = scenarios.drop(columns=["_tier_order"])
    elif stratified:
        users, prods = _sample_stratified(
            financial_profiles, products, n_scenarios, rng,
        )
        scenarios = _compute_features_and_label(users, prods, reviews=reviews_df)
    else:
        users, prods = _sample_random(
            financial_profiles, products, n_scenarios, rng,
        )
        scenarios = _compute_features_and_label(users, prods, reviews=reviews_df)

    logger.info(
        "Generated %d scenarios — label distribution:\n%s",
        len(scenarios),
        scenarios["final_recommendation"].value_counts().to_string(),
    )
    return scenarios

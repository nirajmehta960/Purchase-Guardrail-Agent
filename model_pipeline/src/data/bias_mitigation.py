"""
Pre-Training Bias Mitigation for SavVio Model Pipeline.

This module applies all bias mitigations BEFORE model training.
It is called inside training_data_generator.py after scenarios are generated
and before feature engineering and labeling.

Mitigations applied:
    Financial:
        1. Oversample near-zero savings users (savings_balance < $500)
        2. Compute missing financial_runway column from liquid_savings / monthly_expenses
        3. Flag and handle debt-burdened users (monthly_emi > monthly_income)
        4. Assign sample weights for employment status groups

    Product:
        5. Oversample premium products (price > $200)
        6. Assign confidence weights based on rating_number
        7. Simplify category into parent categories
        8. Create brand_tier feature from details column

    Review:
        9. Balance rating sentiment buckets (positive / neutral / negative)
        10. Down-weight unverified reviews
        11. Create review_quality_score from verified + helpful_vote + text length

Usage:
    from data.bias_mitigation import (
        mitigate_financial_bias,
        mitigate_product_bias,
        mitigate_review_bias,
        apply_all_mitigations,
    )

    # Apply all at once (recommended)
    financial_df, products_df, reviews_df = apply_all_mitigations(
        financial_df, products_df, reviews_df
    )

    # Or apply individually
    financial_df = mitigate_financial_bias(financial_df)
    products_df  = mitigate_product_bias(products_df)
    reviews_df   = mitigate_review_bias(reviews_df)
"""

import logging
import pandas as pd
import numpy as np
from sklearn.utils import resample
from typing import Tuple, Optional

from config import Config

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Constants — thresholds aligned with data pipeline bias detection findings
# ---------------------------------------------------------------------------

# Financial thresholds
NEAR_ZERO_SAVINGS_THRESHOLD = 500       # savings_balance < $500 = near-zero
NEAR_ZERO_SAVINGS_TARGET_PCT = 0.10     # oversample to 10% of training data
DEBT_BURDEN_EMI_MULTIPLIER = 3.0        # EMI > 3x income = likely data error, remove
FINANCIAL_RUNWAY_CAP = 120              # cap at 120 months (10 years)

# Product thresholds
PREMIUM_PRICE_THRESHOLD = 200           # price > $200 = premium
PREMIUM_TARGET_PCT = 0.15              # oversample premium to 15% of training data
LOW_CONFIDENCE_REVIEWS = 10            # rating_number < 10 = low confidence
MED_CONFIDENCE_REVIEWS = 100           # rating_number 10-100 = medium confidence
LOW_CONFIDENCE_WEIGHT = 0.3
MED_CONFIDENCE_WEIGHT = 0.7
HIGH_CONFIDENCE_WEIGHT = 1.0

# Review thresholds
UNVERIFIED_REVIEW_WEIGHT = 0.5         # unverified reviews get half weight
HIGH_HELPFUL_VOTE_THRESHOLD = 10       # > 10 helpful votes = high quality review
MIN_QUALITY_REVIEW_LENGTH = 100        # review text > 100 chars = detailed review


# ---------------------------------------------------------------------------
# MITIGATION 1 — Financial Bias
# ---------------------------------------------------------------------------

def mitigate_financial_bias(
    df: pd.DataFrame,
    random_state: int = Config.RANDOM_STATE,
) -> pd.DataFrame:
    """
    Apply all financial pre-training bias mitigations.

    Steps:
        1. Compute financial_runway (100% missing in detected data)
        2. Flag and clean debt-burdened users (EMI > income)
        3. Oversample near-zero savings users to 10% of dataset
        4. Assign sample_weight for employment status groups

    Args:
        df: Financial profiles DataFrame loaded from DB or CSV.
        random_state: Seed for reproducibility.

    Returns:
        Mitigated DataFrame with new columns:
            financial_runway, financial_runway_band,
            debt_burden_flag, sample_weight
    """
    logger.info("--- Applying Financial Bias Mitigations ---")
    df = df.copy()
    original_rows = len(df)

    # ------------------------------------------------------------------
    # Step 1: Compute financial_runway (was 100% missing in detection)
    # ------------------------------------------------------------------
    # financial_runway = how many months the user can survive without income
    # Formula: liquid_savings / monthly_expenses
    # Why: This is the most direct signal for SavVio's Red/Yellow/Green decision.
    # A user with 0.5 months runway should almost never get a Green recommendation.

    if "liquid_savings" in df.columns and "monthly_expenses" in df.columns:
        df["financial_runway"] = (
            df["liquid_savings"] / df["monthly_expenses"].replace(0, np.nan)
        )
        # Cap at 120 months to prevent extreme outliers from skewing the model
        df["financial_runway"] = df["financial_runway"].clip(upper=FINANCIAL_RUNWAY_CAP)

        # Classify into risk bands — these map directly to SavVio's decision logic
        def _runway_band(months):
            if pd.isna(months) or months < 1:
                return "Critical"   # < 1 month → strong Red signal
            elif months < 3:
                return "Fragile"    # 1-3 months → Yellow signal
            else:
                return "Stable"     # 3+ months → Green allowed

        df["financial_runway_band"] = df["financial_runway"].apply(_runway_band)
        logger.info(
            "financial_runway computed — Critical: %d, Fragile: %d, Stable: %d",
            (df["financial_runway_band"] == "Critical").sum(),
            (df["financial_runway_band"] == "Fragile").sum(),
            (df["financial_runway_band"] == "Stable").sum(),
        )
        print(f"[MITIGATION] financial_runway computed for {len(df)} users")
    else:
        logger.warning("liquid_savings or monthly_expenses not found — skipping financial_runway")

    # ------------------------------------------------------------------
    # Step 2: Flag and clean debt-burdened users (EMI > monthly_income)
    # ------------------------------------------------------------------
    # 23.99% of users had EMI > income in bias detection — unsustainable debt.
    # These users are a vulnerable slice that should get Red recommendations.
    # Rows where EMI > 3x income are likely data entry errors — remove them.

    if "monthly_emi" in df.columns and "monthly_income" in df.columns:
        # Create explicit debt burden flag — gives model a direct signal
        df["debt_burden_flag"] = (
            (df["monthly_emi"] > df["monthly_income"]) &
            (df["monthly_income"] > 0)
        ).astype(int)

        # Remove rows where EMI > 3x income — these are almost certainly errors
        # (no lender would approve such loans)
        error_mask = df["monthly_emi"] > (df["monthly_income"] * DEBT_BURDEN_EMI_MULTIPLIER)
        n_errors = error_mask.sum()
        if n_errors > 0:
            df = df[~error_mask].copy()
            logger.warning("Removed %d rows where EMI > 3x income (likely data errors)", n_errors)
            print(f"[MITIGATION] Removed {n_errors} debt error rows (EMI > 3x income)")

        n_debt_burdened = df["debt_burden_flag"].sum()
        logger.info("Debt burden flag: %d users flagged (EMI > income)", n_debt_burdened)
        print(f"[MITIGATION] Debt burden flag applied — {n_debt_burdened} vulnerable users flagged")
    else:
        logger.warning("monthly_emi or monthly_income not found — skipping debt burden flag")

    # ------------------------------------------------------------------
    # Step 3: Oversample near-zero savings users to 10% of dataset
    # ------------------------------------------------------------------
    # In bias detection: near-zero savings = 0.0% of data (only 1 user!)
    # SavVio must protect these users — they need Red recommendations for
    # almost any significant purchase. Without examples, model won't learn this.

    if "savings_balance" in df.columns:
        vulnerable = df[df["savings_balance"] < NEAR_ZERO_SAVINGS_THRESHOLD]
        normal = df[df["savings_balance"] >= NEAR_ZERO_SAVINGS_THRESHOLD]

        n_vulnerable = len(vulnerable)
        target_size = int(len(df) * NEAR_ZERO_SAVINGS_TARGET_PCT)

        if n_vulnerable > 0 and n_vulnerable < target_size:
            vulnerable_oversampled = resample(
                vulnerable,
                replace=True,
                n_samples=target_size,
                random_state=random_state,
            )
            df = pd.concat([normal, vulnerable_oversampled], ignore_index=True)
            logger.info(
                "Near-zero savings oversampled: %d → %d rows (target 10%%)",
                n_vulnerable, target_size,
            )
            print(f"[MITIGATION] Near-zero savings oversampled: {n_vulnerable} → {target_size} rows")
        elif n_vulnerable == 0:
            logger.warning("No near-zero savings users found — skipping oversample")
    else:
        logger.warning("savings_balance not found — skipping near-zero savings oversample")

    # ------------------------------------------------------------------
    # Step 4: Assign sample weights for employment status
    # ------------------------------------------------------------------
    # Unemployed (9.93%) and Student (9.91%) were flagged as underrepresented.
    # Up-weighting ensures model pays more attention to these groups
    # without duplicating rows (unlike oversampling).

    if "employment_status" in df.columns:
        df["sample_weight"] = 1.0

        # Up-weight underrepresented groups
        df.loc[df["employment_status"] == "Unemployed", "sample_weight"] = 1.5
        df.loc[df["employment_status"] == "Student", "sample_weight"] = 1.5

        # Up-weight debt-burdened users if flag exists
        if "debt_burden_flag" in df.columns:
            df.loc[df["debt_burden_flag"] == 1, "sample_weight"] = (
                df.loc[df["debt_burden_flag"] == 1, "sample_weight"] * 2.0
            )

        logger.info(
            "Sample weights assigned — mean: %.2f, max: %.2f",
            df["sample_weight"].mean(),
            df["sample_weight"].max(),
        )
        print(f"[MITIGATION] Sample weights assigned — mean: {df['sample_weight'].mean():.2f}")
    else:
        logger.warning("employment_status not found — skipping sample weight assignment")

    logger.info(
        "Financial mitigation complete: %d → %d rows",
        original_rows, len(df),
    )
    print(f"[MITIGATION] Financial: {original_rows} → {len(df)} rows after mitigation")
    return df


# ---------------------------------------------------------------------------
# MITIGATION 2 — Product Bias
# ---------------------------------------------------------------------------

def mitigate_product_bias(
    df: pd.DataFrame,
    random_state: int = Config.RANDOM_STATE,
) -> pd.DataFrame:
    """
    Apply all product pre-training bias mitigations.

    Steps:
        1. Oversample premium products (price > $200) to 15% of dataset
        2. Assign confidence_weight based on rating_number
        3. Simplify category into parent categories
        4. Create brand_tier feature from details column

    Args:
        df: Products DataFrame loaded from DB.
        random_state: Seed for reproducibility.

    Returns:
        Mitigated DataFrame with new columns:
            confidence_weight, category_simplified, brand_tier
    """
    logger.info("--- Applying Product Bias Mitigations ---")
    df = df.copy()
    original_rows = len(df)

    # ------------------------------------------------------------------
    # Step 1: Oversample premium products to 15%
    # ------------------------------------------------------------------
    # In bias detection: premium (> $200) = only 3.96% of products.
    # Premium purchases are the highest-stakes decisions for SavVio users.
    # A $600 refrigerator decision is exactly where SavVio adds the most value.
    # Without enough premium examples, the model will be poor at these decisions.

    if "price" in df.columns:
        premium = df[df["price"] > PREMIUM_PRICE_THRESHOLD]
        non_premium = df[df["price"] <= PREMIUM_PRICE_THRESHOLD]
        n_premium = len(premium)
        target_premium = int(len(df) * PREMIUM_TARGET_PCT)

        if n_premium > 0 and n_premium < target_premium:
            premium_oversampled = resample(
                premium,
                replace=True,
                n_samples=target_premium,
                random_state=random_state,
            )
            df = pd.concat([non_premium, premium_oversampled], ignore_index=True)
            logger.info(
                "Premium products oversampled: %d → %d rows (target 15%%)",
                n_premium, target_premium,
            )
            print(f"[MITIGATION] Premium products oversampled: {n_premium} → {target_premium} rows")
    else:
        logger.warning("price column not found — skipping premium oversample")

    # ------------------------------------------------------------------
    # Step 2: Assign confidence weights based on rating_number
    # ------------------------------------------------------------------
    # In bias detection: 44.59% of products have < 10 reviews (low confidence).
    # A 5-star product with 2 reviews is NOT the same as a 5-star product
    # with 500 reviews. Low-review products have unreliable ratings.
    # Down-weighting teaches the model to be appropriately uncertain
    # about products with weak review evidence.

    if "rating_number" in df.columns:
        df["confidence_weight"] = HIGH_CONFIDENCE_WEIGHT  # default full weight

        df.loc[df["rating_number"] < LOW_CONFIDENCE_REVIEWS,
               "confidence_weight"] = LOW_CONFIDENCE_WEIGHT

        df.loc[
            (df["rating_number"] >= LOW_CONFIDENCE_REVIEWS) &
            (df["rating_number"] <= MED_CONFIDENCE_REVIEWS),
            "confidence_weight"
        ] = MED_CONFIDENCE_WEIGHT

        low_conf = (df["confidence_weight"] == LOW_CONFIDENCE_WEIGHT).sum()
        med_conf = (df["confidence_weight"] == MED_CONFIDENCE_WEIGHT).sum()
        high_conf = (df["confidence_weight"] == HIGH_CONFIDENCE_WEIGHT).sum()

        logger.info(
            "Confidence weights: low=%.1f (%d), med=%.1f (%d), high=%.1f (%d)",
            LOW_CONFIDENCE_WEIGHT, low_conf,
            MED_CONFIDENCE_WEIGHT, med_conf,
            HIGH_CONFIDENCE_WEIGHT, high_conf,
        )
        print(f"[MITIGATION] Confidence weights — low: {low_conf}, med: {med_conf}, high: {high_conf}")
    else:
        logger.warning("rating_number not found — skipping confidence weight")

    # ------------------------------------------------------------------
    # Step 3: Simplify category into parent categories
    # ------------------------------------------------------------------
    # In bias detection: 100+ categories all below 5% — massively fragmented.
    # The model can't learn from categories with < 5% representation.
    # Grouping into 7 parent categories gives enough examples per group
    # for the model to learn meaningful category-level patterns.

    if "category" in df.columns:
        def _simplify_category(cat):
            if pd.isna(cat) or str(cat).strip() == "":
                return "Unknown"
            c = str(cat)
            if "Parts & Accessories" in c:
                return "Parts & Accessories"
            if "Refrigerator" in c:
                return "Refrigerators"
            if "Washer" in c or "Dryer" in c:
                return "Laundry"
            if "Dishwasher" in c:
                return "Dishwashers"
            if "Range" in c or "Oven" in c or "Cooktop" in c:
                return "Cooking"
            if "Coffee" in c or "Espresso" in c:
                return "Coffee Machines"
            return "Other Appliances"

        df["category_simplified"] = df["category"].apply(_simplify_category)

        cat_dist = df["category_simplified"].value_counts()
        logger.info("Category simplified:\n%s", cat_dist.to_string())
        print(f"[MITIGATION] Categories simplified into {df['category_simplified'].nunique()} groups")
    else:
        logger.warning("category column not found — skipping category simplification")

    # ------------------------------------------------------------------
    # Step 4: Create brand_tier from details column
    # ------------------------------------------------------------------
    # In bias detection: 58% of products have no brand info.
    # Using raw brand names would cause the model to learn brand-specific
    # shortcuts rather than actual quality signals. Converting to tiers
    # (Major / Minor / Unknown) gives a useful signal without brand bias.

    if "details" in df.columns:
        major_brands = {
            "GE", "Whirlpool", "Frigidaire", "Samsung",
            "LG", "Bosch", "KitchenAid", "Maytag",
        }

        def _brand_tier(details):
            if not isinstance(details, dict):
                return "Unknown"
            brand = str(details.get("Brand", "")).strip()
            if not brand or brand.lower() in {"", "nan", "none"}:
                return "Unknown"
            return "Major" if brand in major_brands else "Minor"

        df["brand_tier"] = df["details"].apply(_brand_tier)

        tier_dist = df["brand_tier"].value_counts()
        logger.info("Brand tiers:\n%s", tier_dist.to_string())
        print(f"[MITIGATION] Brand tiers — {tier_dist.to_dict()}")
    else:
        logger.warning("details column not found — skipping brand_tier")

    logger.info(
        "Product mitigation complete: %d → %d rows",
        original_rows, len(df),
    )
    print(f"[MITIGATION] Product: {original_rows} → {len(df)} rows after mitigation")
    return df


# ---------------------------------------------------------------------------
# MITIGATION 3 — Review Bias
# ---------------------------------------------------------------------------

def mitigate_review_bias(
    df: pd.DataFrame,
    random_state: int = Config.RANDOM_STATE,
) -> pd.DataFrame:
    """
    Apply all review pre-training bias mitigations.

    Steps:
        1. Balance rating sentiment buckets equally (positive/neutral/negative)
        2. Down-weight unverified reviews
        3. Create review_quality_score composite feature

    Args:
        df: Reviews DataFrame loaded from DB.
        random_state: Seed for reproducibility.

    Returns:
        Mitigated DataFrame with new columns:
            review_weight, review_quality_score
    """
    logger.info("--- Applying Review Bias Mitigations ---")
    df = df.copy()
    original_rows = len(df)

    # ------------------------------------------------------------------
    # Step 1: Balance rating sentiment buckets
    # ------------------------------------------------------------------
    # In bias detection: 79.63% positive, 15.48% negative, 4.89% neutral.
    # SavVio's value comes from correctly identifying when NOT to buy.
    # A model trained on 80% positive reviews defaults to Green recommendations.
    # Balancing ensures the model learns negative and neutral signals equally well.
    # This is the single most impactful mitigation for SavVio's core functionality.

    if "rating" in df.columns:
        positive = df[df["rating"] >= 4]
        negative = df[df["rating"] <= 2]
        neutral  = df[df["rating"] == 3]

        n = min(len(positive), len(negative), len(neutral))

        if n > 0:
            df_balanced = pd.concat([
                positive.sample(n, random_state=random_state),
                negative.sample(n, random_state=random_state),
                neutral.sample(n, random_state=random_state),
            ], ignore_index=True)

            # Shuffle so classes are not in blocks
            df = df_balanced.sample(frac=1, random_state=random_state).reset_index(drop=True)

            logger.info(
                "Rating balanced: %d positive, %d negative, %d neutral → %d total",
                n, n, n, len(df),
            )
            print(f"[MITIGATION] Ratings balanced — {n} per sentiment bucket, {len(df)} total")
        else:
            logger.warning("One or more rating buckets is empty — skipping balancing")
    else:
        logger.warning("rating column not found — skipping sentiment balancing")

    # ------------------------------------------------------------------
    # Step 2: Down-weight unverified reviews
    # ------------------------------------------------------------------
    # In bias detection: unverified reviews = only 4.16%.
    # Unverified reviews may be fake (competitor attacks or brand self-promotion).
    # SavVio must give honest recommendations — fake reviews distort product quality.
    # Down-weighting teaches the model to trust verified reviews more.

    if "verified_purchase" in df.columns:
        df["review_weight"] = 1.0
        df.loc[df["verified_purchase"] == False, "review_weight"] = UNVERIFIED_REVIEW_WEIGHT

        # Create explicit trust score as a model feature
        df["review_trust_score"] = df["verified_purchase"].map(
            {True: 1.0, False: 0.3}
        ).fillna(0.3)

        unverified = (df["review_weight"] == UNVERIFIED_REVIEW_WEIGHT).sum()
        logger.info(
            "Review weights: %d unverified down-weighted to %.1f",
            unverified, UNVERIFIED_REVIEW_WEIGHT,
        )
        print(f"[MITIGATION] {unverified} unverified reviews down-weighted to {UNVERIFIED_REVIEW_WEIGHT}x")
    else:
        logger.warning("verified_purchase not found — skipping review weight")

    # ------------------------------------------------------------------
    # Step 3: Create review_quality_score composite feature
    # ------------------------------------------------------------------
    # In bias detection: 80.81% of reviews have 0 helpful votes.
    # A 500-word detailed review from a verified buyer with 50 helpful
    # votes is far more valuable than a one-word review with no votes.
    # This composite score (0-1) captures review quality as a model feature
    # so the model learns to weight high-quality reviews more heavily.

    def _review_quality(row) -> float:
        score = 0.5  # base score for every review

        # Verified purchase adds credibility (+0.2)
        if row.get("verified_purchase", False):
            score += 0.2

        # Helpful votes show community trusts this review
        helpful = row.get("helpful_vote", 0) or 0
        if helpful > HIGH_HELPFUL_VOTE_THRESHOLD:
            score += 0.3    # highly trusted review
        elif helpful > 0:
            score += 0.1    # slightly trusted

        # Longer reviews tend to be more detailed and informative
        text = str(row.get("review_text", "") or "")
        if len(text) > MIN_QUALITY_REVIEW_LENGTH:
            score += 0.1

        return min(score, 1.0)  # cap at 1.0

    df["review_quality_score"] = df.apply(_review_quality, axis=1)

    logger.info(
        "Review quality scores — mean: %.2f, min: %.2f, max: %.2f",
        df["review_quality_score"].mean(),
        df["review_quality_score"].min(),
        df["review_quality_score"].max(),
    )
    print(
        f"[MITIGATION] Review quality scores computed — "
        f"mean: {df['review_quality_score'].mean():.2f}"
    )

    logger.info(
        "Review mitigation complete: %d → %d rows",
        original_rows, len(df),
    )
    print(f"[MITIGATION] Review: {original_rows} → {len(df)} rows after mitigation")
    return df


# ---------------------------------------------------------------------------
# Master function — apply all mitigations at once
# ---------------------------------------------------------------------------

def apply_all_mitigations(
    financial_df: pd.DataFrame,
    products_df: pd.DataFrame,
    reviews_df: pd.DataFrame,
    random_state: int = Config.RANDOM_STATE,
) -> Tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """
    Apply all pre-training bias mitigations to all three datasets.

    This is the main entry point called from training_data_generator.py
    after data is loaded from the database and before feature engineering.

    Args:
        financial_df: Financial profiles from load_financial_profiles()
        products_df:  Products from load_products()
        reviews_df:   Reviews from load_reviews()
        random_state: Seed for reproducibility (default: Config.RANDOM_STATE)

    Returns:
        Tuple of (mitigated_financial_df, mitigated_products_df, mitigated_reviews_df)
    """
    print("\n" + "=" * 60)
    print("PRE-TRAINING BIAS MITIGATION")
    print("=" * 60)

    financial_df = mitigate_financial_bias(financial_df, random_state)
    products_df  = mitigate_product_bias(products_df, random_state)
    reviews_df   = mitigate_review_bias(reviews_df, random_state)

    print("=" * 60)
    print("PRE-TRAINING BIAS MITIGATION COMPLETE")
    print("=" * 60 + "\n")

    return financial_df, products_df, reviews_df


# ---------------------------------------------------------------------------
# Smoke test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    print("Testing bias mitigation with mock data...")

    # Mock financial data
    fin_mock = pd.DataFrame({
        "user_id": range(100),
        "monthly_income": [3000] * 80 + [0] * 20,
        "monthly_expenses": [1500] * 100,
        "savings_balance": [10000] * 95 + [100, 200, 300, 50, 0],
        "liquid_savings": [8000] * 95 + [100, 200, 300, 50, 0],
        "monthly_emi": [500] * 90 + [5000] * 10,
        "employment_status": ["Employed"] * 60 + ["Student"] * 20 + ["Unemployed"] * 20,
    })

    # Mock product data
    prod_mock = pd.DataFrame({
        "product_id": range(50),
        "price": [50] * 35 + [150] * 11 + [500] * 4,
        "rating_number": [5] * 20 + [50] * 20 + [200] * 10,
        "category": ["Appliances > Parts & Accessories"] * 30 + ["Appliances > Refrigerators"] * 20,
        "details": [{"Brand": "GE"}] * 10 + [{}] * 40,
    })

    # Mock review data
    rev_mock = pd.DataFrame({
        "rating": [5] * 80 + [3] * 10 + [1] * 10,
        "verified_purchase": [True] * 95 + [False] * 5,
        "helpful_vote": [0] * 80 + [5] * 15 + [20] * 5,
        "review_text": ["Great product!"] * 50 + ["A" * 150] * 50,
    })

    fin_out, prod_out, rev_out = apply_all_mitigations(fin_mock, prod_mock, rev_mock)

    print(f"\nFinancial: {len(fin_mock)} → {len(fin_out)} rows")
    print(f"Products:  {len(prod_mock)} → {len(prod_out)} rows")
    print(f"Reviews:   {len(rev_mock)} → {len(rev_out)} rows")
    print(f"\nNew financial columns: {[c for c in fin_out.columns if c not in fin_mock.columns]}")
    print(f"New product columns:   {[c for c in prod_out.columns if c not in prod_mock.columns]}")
    print(f"New review columns:    {[c for c in rev_out.columns if c not in rev_mock.columns]}")
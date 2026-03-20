"""
Post-Training Bias Detection for SavVio Model Pipeline.

Detects performance disparities across sensitive subgroups after model
training. Run on validation set predictions after model fitting is complete.

Slices checked:
    Demographic : region, employment_status
    Financial   : income_band, dti_band, savings_band, emergency_fund_band
    Product     : price_band, rating_variance_band, review_confidence_band

Metrics computed per slice:
    - Accuracy
    - F1 (weighted)
    - AUC (one-vs-rest, weighted)

Outputs:
    - Per-slice metrics printed to terminal
    - Disparity summary table (per-slice vs aggregate)
    - F1 bar chart per slice (logged to MLflow)
    - All metrics logged to MLflow

Contract (matches run_pipeline.py expectation):
    evaluate_bias(y_test, y_pred, sensitive_features)
        → returns (fairness_metrics dict, bias_passed bool)
"""

import logging
import os
import tempfile

import numpy as np
import pandas as pd
import mlflow
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

from fairlearn.metrics import MetricFrame
from sklearn.metrics import (
    accuracy_score,
    f1_score,
    roc_auc_score,
)
from sklearn.preprocessing import label_binarize

from config import Config

logger = logging.getLogger(__name__)

BIAS_THRESHOLD = Config.BIAS_DISPARITY_THRESHOLD  # 0.10


# ---------------------------------------------------------------------------
# Slice builders — convert raw columns into named bands
# ---------------------------------------------------------------------------

def _add_financial_slices(scenarios: pd.DataFrame) -> pd.DataFrame:
    """
    Add financial slice columns to scenarios DataFrame.

    Income band    : Low / Mid / High
    DTI band       : Safe / Warning / Risky
    Savings band   : Near-zero / Low / Moderate / High
    Emergency fund : Critical / Fragile / Stable
    """
    df = scenarios.copy()

    # Income band
    if "monthly_income" in df.columns:
        df["income_band"] = pd.cut(
            df["monthly_income"],
            bins=[0, 3000, 7000, float("inf")],
            labels=["Low", "Mid", "High"],
            include_lowest=True,
        ).astype(str)

    # DTI band
    if "debt_to_income_ratio" in df.columns:
        df["dti_band"] = pd.cut(
            df["debt_to_income_ratio"],
            bins=[-float("inf"), 0.2, 0.4, float("inf")],
            labels=["Safe", "Warning", "Risky"],
        ).astype(str)

    # Savings band
    if "savings_balance" in df.columns:
        df["savings_band"] = pd.cut(
            df["savings_balance"],
            bins=[-float("inf"), 500, 3000, 15000, float("inf")],
            labels=["Near-zero", "Low", "Moderate", "High"],
        ).astype(str)

    # Emergency fund band
    if "emergency_fund_months" in df.columns:
        df["emergency_fund_band"] = pd.cut(
            df["emergency_fund_months"],
            bins=[-float("inf"), 1, 3, float("inf")],
            labels=["Critical", "Fragile", "Stable"],
        ).astype(str)

    return df


def _add_product_slices(scenarios: pd.DataFrame) -> pd.DataFrame:
    """
    Add product slice columns to scenarios DataFrame.

    Price band             : Budget / Mid-range / Premium
    Rating variance band   : Consensus / Mixed / Polarized / Single-review
    Review confidence band : Low / Medium / High
    """
    df = scenarios.copy()

    # Price band
    if "product_price" in df.columns:
        df["price_band"] = pd.cut(
            df["product_price"],
            bins=[0, 25, 200, float("inf")],
            labels=["Budget", "Mid-range", "Premium"],
            include_lowest=True,
        ).astype(str)
    elif "price" in df.columns:
        df["price_band"] = pd.cut(
            df["price"],
            bins=[0, 25, 200, float("inf")],
            labels=["Budget", "Mid-range", "Premium"],
            include_lowest=True,
        ).astype(str)

    # Rating variance band
    if "rating_variance" in df.columns:
        df["rating_variance_band"] = pd.cut(
            df["rating_variance"],
            bins=[-float("inf"), 0.0, 0.5, 1.0, float("inf")],
            labels=["Single-review", "Consensus", "Mixed", "Polarized"],
        ).astype(str)

    # Review confidence band
    if "rating_number" in df.columns:
        df["review_confidence_band"] = pd.cut(
            df["rating_number"],
            bins=[0, 10, 100, float("inf")],
            labels=["Low", "Medium", "High"],
            include_lowest=True,
        ).astype(str)

    return df


def build_all_slices(
    sensitive_features: pd.DataFrame,
    scenarios_raw: pd.DataFrame = None,
) -> pd.DataFrame:
    """
    Combine demographic, financial, and product slices into one DataFrame.

    Args:
        sensitive_features: DataFrame with demographic columns
                            (region, employment_status)
        scenarios_raw:      Full scenarios DataFrame with all raw columns.
                            If None, only demographic slices are used.

    Returns:
        DataFrame with all slice columns ready for MetricFrame.
    """
    slices = sensitive_features.copy()

    if scenarios_raw is not None:
        # Add financial slices
        fin_sliced = _add_financial_slices(scenarios_raw)
        for col in ["income_band", "dti_band", "savings_band", "emergency_fund_band"]:
            if col in fin_sliced.columns:
                slices[col] = fin_sliced[col].values

        # Add product slices
        prod_sliced = _add_product_slices(scenarios_raw)
        for col in ["price_band", "rating_variance_band", "review_confidence_band"]:
            if col in prod_sliced.columns:
                slices[col] = prod_sliced[col].values

    return slices


# ---------------------------------------------------------------------------
# Per-slice metric computation
# ---------------------------------------------------------------------------

def _compute_slice_metrics(
    y_true,
    y_pred,
    y_prob,
    sf_col: pd.Series,
    feature_name: str,
    n_classes: int,
) -> pd.DataFrame:
    """
    Compute accuracy, F1, and AUC per group in a single sensitive feature.

    Returns:
        DataFrame with columns: group, accuracy, f1, auc, disparity_f1
    """
    groups = sf_col.unique()
    rows = []

    # Aggregate metrics (used to compute disparity)
    agg_acc = accuracy_score(y_true, y_pred)
    agg_f1  = f1_score(y_true, y_pred, average="weighted", zero_division=0)

    if y_prob is not None and n_classes > 1:
        try:
            agg_auc = roc_auc_score(
                y_true, y_prob, multi_class="ovr", average="weighted"
            )
        except Exception:
            agg_auc = float("nan")
    else:
        agg_auc = float("nan")

    for group in sorted(groups):
        mask = sf_col == group
        if mask.sum() < 5:
            # Skip groups with too few samples to be meaningful
            continue

        yt = np.array(y_true)[mask]
        yp = np.array(y_pred)[mask]

        acc = accuracy_score(yt, yp)
        f1  = f1_score(yt, yp, average="weighted", zero_division=0)

        if y_prob is not None and n_classes > 1:
            yprob_g = np.array(y_prob)[mask]
            try:
                auc = roc_auc_score(
                    yt, yprob_g, multi_class="ovr", average="weighted"
                )
            except Exception:
                auc = float("nan")
        else:
            auc = float("nan")

        rows.append({
            "feature":      feature_name,
            "group":        str(group),
            "n_samples":    int(mask.sum()),
            "accuracy":     round(acc, 4),
            "f1":           round(f1, 4),
            "auc":          round(auc, 4) if not np.isnan(auc) else "n/a",
            "disparity_f1": round(abs(f1 - agg_f1), 4),
            "agg_f1":       round(agg_f1, 4),
            "agg_acc":      round(agg_acc, 4),
        })

    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# Visualizations
# ---------------------------------------------------------------------------

def _plot_f1_bar_chart(slice_df: pd.DataFrame, save_dir: str) -> None:
    """
    Generate F1 bar chart per slice group and log to MLflow.

    Each sensitive feature gets its own bar chart showing per-group F1
    vs the aggregate F1, with a red dashed line at the bias threshold.
    """
    features = slice_df["feature"].unique()

    for feature in features:
        df = slice_df[slice_df["feature"] == feature].copy()
        if df.empty:
            continue

        fig, ax = plt.subplots(figsize=(max(8, len(df) * 1.2), 5))

        colors = [
            "tomato" if row["disparity_f1"] > BIAS_THRESHOLD else "steelblue"
            for _, row in df.iterrows()
        ]

        bars = ax.bar(df["group"], df["f1"], color=colors, edgecolor="white")

        # Aggregate F1 line
        agg_f1 = df["agg_f1"].iloc[0]
        ax.axhline(agg_f1, color="black", linestyle="--", linewidth=1.5,
                   label=f"Aggregate F1: {agg_f1:.4f}")

        # Bias threshold band
        ax.axhline(agg_f1 - BIAS_THRESHOLD, color="red", linestyle=":",
                   linewidth=1.2, label=f"Threshold (±{BIAS_THRESHOLD})")

        # Value labels on bars
        for bar, (_, row) in zip(bars, df.iterrows()):
            ax.text(
                bar.get_x() + bar.get_width() / 2,
                bar.get_height() + 0.005,
                f"{row['f1']:.3f}\nn={row['n_samples']}",
                ha="center", va="bottom", fontsize=8,
            )

        ax.set_title(f"F1 Score per Group — {feature}", fontsize=13)
        ax.set_xlabel("Group")
        ax.set_ylabel("Weighted F1")
        ax.set_ylim(0, 1.1)
        ax.legend(loc="lower right")
        plt.xticks(rotation=30, ha="right")
        plt.tight_layout()

        path = os.path.join(save_dir, f"bias_f1_{feature}.png")
        fig.savefig(path, dpi=150, bbox_inches="tight")
        plt.close(fig)
        mlflow.log_artifact(path, "bias_report")
        logger.info("F1 bar chart logged for feature: %s", feature)


def _save_disparity_table(slice_df: pd.DataFrame, save_dir: str) -> None:
    """
    Save disparity summary table as CSV and log to MLflow.

    Table columns: feature, group, n_samples, accuracy, f1, auc,
                   disparity_f1, flagged
    """
    table = slice_df.copy()
    table["flagged"] = table["disparity_f1"] > BIAS_THRESHOLD

    path = os.path.join(save_dir, "bias_disparity_table.csv")
    table.to_csv(path, index=False)
    mlflow.log_artifact(path, "bias_report")

    # Print to terminal
    print("\n  Disparity Summary Table:")
    print(f"  {'Feature':<25} {'Group':<20} {'N':>6} {'Acc':>6} {'F1':>6} "
          f"{'AUC':>6} {'ΔF1':>6} {'Flag':>5}")
    print("  " + "-" * 85)
    for _, row in table.iterrows():
        flag = "⚠️" if row["flagged"] else "✅"
        print(
            f"  {row['feature']:<25} {row['group']:<20} "
            f"{row['n_samples']:>6} {row['accuracy']:>6} {row['f1']:>6} "
            f"{str(row['auc']):>6} {row['disparity_f1']:>6} {flag:>5}"
        )

    logger.info("Disparity table saved: %s", path)


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def evaluate_bias(
    y_test,
    y_pred,
    sensitive_features: pd.DataFrame,
    y_prob=None,
    scenarios_raw: pd.DataFrame = None,
) -> tuple:
    """
    Detect bias in the trained model across all sensitive slices.

    Computes accuracy, F1, and AUC per group for every slice column.
    Flags any group whose F1 deviates from aggregate F1 by more than
    BIAS_THRESHOLD (0.10). Generates F1 bar charts and a disparity
    summary table, all logged to MLflow.

    Args:
        y_test:             True labels (integer encoded)
        y_pred:             Model predictions (integer encoded)
        sensitive_features: DataFrame with demographic slice columns
                            (region, employment_status)
        y_prob:             Predicted probabilities (optional, for AUC)
        scenarios_raw:      Full scenarios DataFrame for financial +
                            product slices (optional)

    Returns:
        Tuple of (fairness_metrics dict, bias_passed bool)
    """
    print("\n" + "=" * 60)
    print("POST-TRAINING BIAS DETECTION")
    print("=" * 60)

    # Build all slices (demographic + financial + product if available)
    all_slices = build_all_slices(sensitive_features, scenarios_raw)

    n_classes = len(np.unique(y_test))
    fairness_metrics = {}
    bias_passed = True
    all_slice_rows = []

    # ── Aggregate baseline metrics ────────────────────────────────────
    agg_acc = accuracy_score(y_test, y_pred)
    agg_f1  = f1_score(y_test, y_pred, average="weighted", zero_division=0)
    print(f"\n  Aggregate — Accuracy: {agg_acc:.4f}  F1: {agg_f1:.4f}")
    fairness_metrics["aggregate_accuracy"] = round(agg_acc, 4)
    fairness_metrics["aggregate_f1"] = round(agg_f1, 4)

    # ── Per-slice analysis ────────────────────────────────────────────
    for feature_name in all_slices.columns:
        sf_col = all_slices[feature_name]

        # Skip columns with only 1 unique value — no comparison possible
        if sf_col.nunique() < 2:
            continue

        print(f"\n  ── Slice: {feature_name} ──")
        print(f"     Groups: {sorted(sf_col.dropna().unique().tolist())}")

        slice_df = _compute_slice_metrics(
            y_test, y_pred, y_prob, sf_col, feature_name, n_classes
        )

        if slice_df.empty:
            continue

        all_slice_rows.append(slice_df)

        # Print per-group results and check threshold
        for _, row in slice_df.iterrows():
            flag = "⚠️  FLAGGED" if row["disparity_f1"] > BIAS_THRESHOLD else "✅"
            print(
                f"     {row['group']:<20} "
                f"acc={row['accuracy']}  f1={row['f1']}  "
                f"auc={row['auc']}  ΔF1={row['disparity_f1']}  {flag}"
            )

            if row["disparity_f1"] > BIAS_THRESHOLD:
                bias_passed = False
                logger.warning(
                    "Bias detected — %s / %s: F1=%.4f, disparity=%.4f > threshold=%.2f",
                    feature_name, row["group"],
                    row["f1"], row["disparity_f1"], BIAS_THRESHOLD,
                )

            # Log per-group metrics to MLflow
            key = f"{feature_name}_{row['group'].replace(' ', '_')}"
            fairness_metrics[f"bias_acc_{key}"]  = row["accuracy"]
            fairness_metrics[f"bias_f1_{key}"]   = row["f1"]
            fairness_metrics[f"bias_disp_{key}"] = row["disparity_f1"]

    # ── Bias gate result ──────────────────────────────────────────────
    fairness_metrics["bias_gate_passed"] = int(bias_passed)
    print(f"\n  Bias Gate: {'✅ PASSED' if bias_passed else '❌ FAILED — mitigation required'}")
    print("=" * 60)

    # ── Visualizations & reports ──────────────────────────────────────
    if all_slice_rows:
        combined = pd.concat(all_slice_rows, ignore_index=True)

        with tempfile.TemporaryDirectory() as tmp_dir:
            # F1 bar charts per slice feature
            _plot_f1_bar_chart(combined, tmp_dir)

            # Disparity summary table
            _save_disparity_table(combined, tmp_dir)

    # ── Log all metrics to MLflow ─────────────────────────────────────
    mlflow.log_metrics(fairness_metrics)
    logger.info("Bias detection complete. bias_passed=%s", bias_passed)

    return fairness_metrics, bias_passed
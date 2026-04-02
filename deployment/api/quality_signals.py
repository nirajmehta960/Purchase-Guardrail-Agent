"""
Map Layer 2 ProductFeatures / ReviewFeatures + product row into API-friendly views.

Same engineered fields as training; values are rounded for JSON/UI readability.
"""

from __future__ import annotations

import pandas as pd

from deployment.api.schemas import ProductSignalsView, ReviewSignalsView
from features.product_features import ProductFeatures
from features.review_features import ReviewFeatures


def _r(x: float | None, nd: int = 4) -> float | None:
    if x is None:
        return None
    v = float(x)
    if v != v:  # NaN
        return None
    return round(v, nd)


def build_quality_signal_views(
    product_row: pd.Series,
    product_feats: ProductFeatures,
    review_feats: ReviewFeatures,
) -> tuple[ProductSignalsView, ReviewSignalsView]:
    pcr = float(product_feats.price_category_rank)
    if pcr < 0.34:
        price_tier = "lower third of category price range"
    elif pcr < 0.67:
        price_tier = "mid category price range"
    else:
        price_tier = "upper third of category price range"

    crd = float(product_feats.category_rating_deviation)
    if crd > 0.15:
        vs_cat = "above category average rating"
    elif crd < -0.15:
        vs_cat = "below category average rating"
    else:
        vs_cat = "near category average rating"

    rn = int(product_row.get("rating_number") or 0)
    cold = bool(product_feats.cold_start_flag == 1)

    ss = float(review_feats.sentiment_spread or 0.0)
    if ss > 0.25:
        sent_txt = "reviews skew positive"
    elif ss < -0.25:
        sent_txt = "reviews skew negative or mixed"
    else:
        sent_txt = "reviews mixed / balanced"

    product_view = ProductSignalsView(
        average_rating=_r(product_row.get("average_rating")),
        rating_count=rn,
        rating_variance=_r(product_row.get("rating_variance")),
        category=str(product_row.get("category") or "") or None,
        price=_r(product_row.get("price")),
        price_position_in_category=price_tier,
        rating_vs_category=vs_cat,
        cold_start=cold,
        value_density=_r(product_feats.value_density),
        review_confidence=_r(product_feats.review_confidence),
        rating_polarization=_r(product_feats.rating_polarization),
        quality_risk_score=_r(product_feats.quality_risk_score),
        price_category_rank=_r(product_feats.price_category_rank),
        category_rating_deviation=_r(product_feats.category_rating_deviation),
    )

    review_view = ReviewSignalsView(
        verified_purchase_ratio=_r(review_feats.verified_purchase_ratio),
        helpful_concentration=_r(review_feats.helpful_concentration),
        sentiment_spread=_r(review_feats.sentiment_spread),
        review_depth_score=_r(review_feats.review_depth_score),
        reviewer_diversity=_r(review_feats.reviewer_diversity),
        extreme_rating_ratio=_r(review_feats.extreme_rating_ratio),
        sentiment_interpretation=sent_txt,
    )

    return product_view, review_view

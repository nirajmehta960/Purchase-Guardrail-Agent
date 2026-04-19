"""Tests for the product resolver — current ILIKE/price-aligned implementation.

The current resolver:
- Uses Postgres `ILIKE` (no embeddings / pgvector)
- Returns a `ProductMatch(product_id, product_name, price)` (3 fields, no
  category / similarity_score in the return value)
- Combines a name-overlap score with a price-closeness score when a hint is set
- Returns None when the price hint is too far from every candidate, so the
  caller can run a hypothetical evaluation
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from llm.product_resolver import (
    ProductMatch,
    _is_noise_token,
    _name_overlap_score,
    _price_score,
    _tokenize,
    resolve_product,
    resolve_product_by_id,
)


# ---------------------------------------------------------------------------
# Helpers to fabricate a sqlalchemy-shaped engine.
# ---------------------------------------------------------------------------

def _make_engine(rows_for_each_call):
    """Return a MagicMock engine whose .connect() yields a connection that
    serves up the supplied rows on successive .execute(...).fetchall() calls.

    rows_for_each_call: list of lists. Each entry is consumed by one execute.
    """
    seq = list(rows_for_each_call)

    class _Result:
        def __init__(self, rows):
            self._rows = rows

        def fetchall(self):
            return list(self._rows)

        def fetchone(self):
            return self._rows[0] if self._rows else None

    conn = MagicMock()

    def _execute(_sql, _params=None):
        rows = seq.pop(0) if seq else []
        return _Result(rows)

    conn.execute.side_effect = _execute

    engine = MagicMock()
    engine.connect.return_value.__enter__ = MagicMock(return_value=conn)
    engine.connect.return_value.__exit__ = MagicMock(return_value=False)
    return engine


def _make_failing_engine():
    conn = MagicMock()
    conn.execute.side_effect = Exception("DB connection lost")
    engine = MagicMock()
    engine.connect.return_value.__enter__ = MagicMock(return_value=conn)
    engine.connect.return_value.__exit__ = MagicMock(return_value=False)
    return engine


# ---------------------------------------------------------------------------
# ProductMatch dataclass
# ---------------------------------------------------------------------------

class TestProductMatch:
    def test_dataclass_fields(self):
        m = ProductMatch(
            product_id="B0BX1234",
            product_name="Sony WH-1000XM5",
            price=349.99,
        )
        assert m.product_id == "B0BX1234"
        assert m.product_name == "Sony WH-1000XM5"
        assert m.price == 349.99


# ---------------------------------------------------------------------------
# Internal scoring helpers
# ---------------------------------------------------------------------------

class TestScoringHelpers:
    def test_tokenize_lowercases_and_filters(self):
        tokens = _tokenize("Sony WH-1000XM5 Headphones!")
        assert "sony" in tokens
        assert "headphones" in tokens

    def test_noise_token_detects_pure_numbers(self):
        assert _is_noise_token("349")
        assert _is_noise_token("1,299.99")
        assert not _is_noise_token("sony")

    def test_name_overlap_full_match(self):
        score = _name_overlap_score({"sony", "headphones"}, "Sony Headphones")
        assert score == 1.0

    def test_name_overlap_partial(self):
        score = _name_overlap_score({"sony", "wireless"}, "Sony Headphones")
        assert 0.0 < score < 1.0

    def test_name_overlap_empty_ref(self):
        assert _name_overlap_score(set(), "Sony Headphones") == 0.0

    def test_price_score_perfect_match(self):
        assert _price_score(350.0, 350.0) == 1.0

    def test_price_score_no_hint_is_neutral(self):
        assert _price_score(350.0, None) == 1.0

    def test_price_score_far_off_clamps_to_zero(self):
        assert _price_score(50.0, 500.0) == 0.0


# ---------------------------------------------------------------------------
# resolve_product — main entry point
# ---------------------------------------------------------------------------

class TestResolveProduct:
    def test_empty_text_returns_none(self):
        assert resolve_product("", MagicMock()) is None

    def test_whitespace_only_returns_none(self):
        assert resolve_product("   ", MagicMock()) is None

    def test_none_engine_returns_none(self):
        assert resolve_product("Sony", None) is None

    def test_no_results_returns_none(self):
        # First ILIKE returns nothing; token-by-token retries also return nothing.
        engine = _make_engine([[], [], [], [], [], [], [], []])
        assert resolve_product("nonexistent xyz", engine) is None

    def test_db_error_returns_none(self):
        assert resolve_product("Sony headphones", _make_failing_engine()) is None

    def test_single_match_returned(self):
        rows = [("B0BX1234", "Sony WH-1000XM5", 349.99)]
        engine = _make_engine([rows])
        result = resolve_product("Sony WH-1000XM5", engine)
        assert result is not None
        assert isinstance(result, ProductMatch)
        assert result.product_id == "B0BX1234"
        assert result.product_name == "Sony WH-1000XM5"
        assert result.price == 349.99

    def test_price_hint_matching_match_is_kept(self):
        rows = [("B1", "Sony WH-1000XM5", 349.99)]
        engine = _make_engine([rows])
        result = resolve_product("Sony WH-1000XM5", engine, price_hint=350.0)
        assert result is not None
        assert result.product_id == "B1"

    def test_price_hint_mismatch_is_rejected(self):
        # Catalog has a $20 trinket but user asked about something near $500
        # — outside the ±38% mismatch window, so resolver should yield None.
        rows = [("B2", "Sony Sticker Pack", 20.0)]
        engine = _make_engine([rows])
        result = resolve_product("Sony headphones", engine, price_hint=500.0)
        assert result is None

    def test_ranks_better_name_overlap_first(self):
        """Among candidates, higher token overlap wins (no price hint)."""
        rows = [
            ("A1", "Random Bluetooth Speaker", 80.0),
            ("A2", "Sony WH-1000XM5 Headphones", 350.0),
        ]
        engine = _make_engine([rows])
        result = resolve_product("Sony headphones", engine)
        assert result is not None
        assert result.product_id == "A2"


# ---------------------------------------------------------------------------
# resolve_product_by_id
# ---------------------------------------------------------------------------

class TestResolveProductById:
    def test_empty_id_returns_none(self):
        assert resolve_product_by_id("", MagicMock()) is None

    def test_none_engine_returns_none(self):
        assert resolve_product_by_id("B0BX1234", None) is None

    def test_found(self):
        engine = _make_engine([[("B0BX1234", "Sony WH-1000XM5", 349.99)]])
        result = resolve_product_by_id("B0BX1234", engine)
        assert result is not None
        assert result.product_id == "B0BX1234"
        assert result.product_name == "Sony WH-1000XM5"
        assert result.price == 349.99

    def test_not_found(self):
        engine = _make_engine([[]])
        assert resolve_product_by_id("NOPE", engine) is None

    def test_db_error_returns_none(self):
        assert resolve_product_by_id("B0BX1234", _make_failing_engine()) is None

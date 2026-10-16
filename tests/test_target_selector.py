"""Tests for strategy/targets/target_selector.py — implements the three
decisions locked at the TP1/TP2 Resolution Gate (see project chat log).

Every distance in this file was independently hand-computed before
being written into an assertion (see the milestone chat log) — two
of my own first-draft "expected" values were wrong (I mis-added the
distances), which the hand-verification step caught before any
assertion was trusted.
"""

from datetime import datetime, timezone
from decimal import Decimal

from core.domain.common import Direction, Timeframe
from core.domain.liquidity import LiquidityPool, LiquidityPoolType, LiquidityScoreComponents
from strategy.targets.target_selector import classify, select_targets, EXTERNAL, INTERNAL

T = datetime(2026, 1, 5, tzinfo=timezone.utc)
ENTRY = Decimal("2400")


def _zero_components() -> LiquidityScoreComponents:
    return LiquidityScoreComponents(
        htf_significance=Decimal("0"), directional_alignment=Decimal("0"),
        session_relevance=Decimal("0"), equal_touch_strength=Decimal("0"),
        recency=Decimal("0"), distance_reachability=Decimal("0"), unconsumed_status=Decimal("5"),
    )


def _pool(pool_id, ptype, price, created_at=T, is_major=None) -> LiquidityPool:
    return LiquidityPool(
        pool_id=pool_id, type=ptype, price=Decimal(str(price)), timeframe=Timeframe.D1,
        created_at=created_at, score=Decimal("5"), score_components=_zero_components(),
        is_major_swing=is_major,
    )


class TestClassification:
    """Decision 2: major swing -> EXTERNAL, minor swing -> INTERNAL."""

    def test_pdh_pdl_pwh_pwl_are_external(self):
        for t in (LiquidityPoolType.PDH, LiquidityPoolType.PDL, LiquidityPoolType.PWH, LiquidityPoolType.PWL):
            assert classify(_pool("p", t, 2400)) == EXTERNAL

    def test_equal_high_low_and_session_are_internal(self):
        for t in (LiquidityPoolType.EQUAL_HIGH, LiquidityPoolType.EQUAL_LOW,
                  LiquidityPoolType.SESSION_HIGH, LiquidityPoolType.SESSION_LOW):
            assert classify(_pool("p", t, 2400)) == INTERNAL

    def test_major_swing_is_external_minor_swing_is_internal(self):
        assert classify(_pool("p", LiquidityPoolType.SWING_HIGH, 2400, is_major=True)) == EXTERNAL
        assert classify(_pool("p", LiquidityPoolType.SWING_LOW, 2400, is_major=False)) == INTERNAL

    def test_swing_with_unknown_major_status_is_excluded_not_guessed(self):
        assert classify(_pool("p", LiquidityPoolType.SWING_HIGH, 2400, is_major=None)) is None

    def test_round_number_is_never_eligible(self):
        assert classify(_pool("p", LiquidityPoolType.ROUND_NUMBER, 2400)) is None


class TestBuySelection:
    """Hand-verified distances (corrected after an initial arithmetic
    slip — see module docstring): from entry=2400, minor_swing_2403 is
    distance 3 (nearer than eq_high_2405's distance 5); among
    externals, pdh_2408 (distance 8) is nearer than major_swing_2410
    (distance 10) and pwh_2420 (distance 20)."""

    def _pools(self):
        return [
            _pool("eq_high_2405", LiquidityPoolType.EQUAL_HIGH, 2405),
            _pool("minor_swing_2403", LiquidityPoolType.SWING_HIGH, 2403, is_major=False),
            _pool("major_swing_2410", LiquidityPoolType.SWING_HIGH, 2410, is_major=True),
            _pool("pwh_2420", LiquidityPoolType.PWH, 2420),
            _pool("pdh_2408", LiquidityPoolType.PDH, 2408),
            _pool("pdl_2390", LiquidityPoolType.PDL, 2390),  # wrong side -- must be excluded
            _pool("round_2406", LiquidityPoolType.ROUND_NUMBER, 2406),  # never eligible
        ]

    def test_nearest_internal_and_external_are_selected(self):
        result = select_targets(Direction.BULLISH, ENTRY, self._pools(), frozenset(), T)
        assert result.tp1_pool_id == "minor_swing_2403"
        assert result.take_profit_1 == Decimal("2403")
        assert result.tp2_pool_id == "pdh_2408"
        assert result.take_profit_2 == Decimal("2408")

    def test_wrong_side_pool_never_selected(self):
        result = select_targets(Direction.BULLISH, ENTRY, self._pools(), frozenset(), T)
        assert result.tp1_pool_id != "pdl_2390"
        assert result.tp2_pool_id != "pdl_2390"

    def test_consuming_the_nearest_external_promotes_the_next_one(self):
        result = select_targets(Direction.BULLISH, ENTRY, self._pools(), frozenset({"pdh_2408"}), T)
        assert result.tp2_pool_id == "major_swing_2410"
        assert result.take_profit_2 == Decimal("2410")


class TestSellSelection:
    """Mirrors the BUY case. Hand-verified: minor_swing_2397 is
    distance 3 (nearer than eq_low_2395's distance 5); pdl_2392
    (distance 8) is nearer than pwl_2380 (distance 20)."""

    def test_nearest_internal_and_external_below_entry(self):
        pools = [
            _pool("eq_low_2395", LiquidityPoolType.EQUAL_LOW, 2395),
            _pool("minor_swing_2397", LiquidityPoolType.SWING_LOW, 2397, is_major=False),
            _pool("pdl_2392", LiquidityPoolType.PDL, 2392),
            _pool("pwl_2380", LiquidityPoolType.PWL, 2380),
            _pool("pdh_wrong_side", LiquidityPoolType.PDH, 2410),  # above entry, wrong side for SELL
        ]
        result = select_targets(Direction.BEARISH, ENTRY, pools, frozenset(), T)
        assert result.tp1_pool_id == "minor_swing_2397"
        assert result.take_profit_1 == Decimal("2397")
        assert result.tp2_pool_id == "pdl_2392"
        assert result.take_profit_2 == Decimal("2392")


class TestNoFabricatedFallback:
    def test_missing_internal_category_returns_none(self):
        pools = [_pool("pdh_only", LiquidityPoolType.PDH, 2408)]
        assert select_targets(Direction.BULLISH, ENTRY, pools, frozenset(), T) is None

    def test_missing_external_category_returns_none(self):
        pools = [_pool("eq_high_only", LiquidityPoolType.EQUAL_HIGH, 2405)]
        assert select_targets(Direction.BULLISH, ENTRY, pools, frozenset(), T) is None

    def test_empty_pool_list_returns_none(self):
        assert select_targets(Direction.BULLISH, ENTRY, [], frozenset(), T) is None


class TestTemporalCausality:
    """A pool not yet knowable at the decision timestamp must never be
    selected, even if present in the candidate list (defensive check —
    should already be excluded upstream, but never trusted silently)."""

    def test_pool_created_after_decision_timestamp_is_excluded(self):
        future_pool = _pool("future_pdh", LiquidityPoolType.PDH, 2408, created_at=datetime(2026, 1, 6, tzinfo=timezone.utc))
        pools = [_pool("int1", LiquidityPoolType.EQUAL_HIGH, 2405), future_pool]
        assert select_targets(Direction.BULLISH, ENTRY, pools, frozenset(), T) is None


class TestDeterministicTieBreak:
    def test_exact_distance_tie_broken_by_pool_id(self):
        tie_a = _pool("zzz_tie", LiquidityPoolType.PDH, 2408)
        tie_b = _pool("aaa_tie", LiquidityPoolType.PWH, 2408)  # identical price, different id
        pools = [_pool("int1", LiquidityPoolType.EQUAL_HIGH, 2405), tie_a, tie_b]
        result = select_targets(Direction.BULLISH, ENTRY, pools, frozenset(), T)
        assert result.tp2_pool_id == "aaa_tie"

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from pydantic import ValidationError

from core.domain.common import Direction, Timeframe
from core.domain.liquidity import (
    DisplacementEvent,
    LiquidityPool,
    LiquidityPoolType,
    LiquidityScoreComponents,
    MSSEvent,
    SweepEvent,
)


def _dt(minute: int) -> datetime:
    # timedelta-based so any minute offset (not just 0-59) is valid.
    return datetime(2026, 1, 5, 0, 0, tzinfo=timezone.utc) + timedelta(minutes=minute)


def _score() -> LiquidityScoreComponents:
    return LiquidityScoreComponents(
        htf_significance=Decimal("25"), directional_alignment=Decimal("20"),
        session_relevance=Decimal("15"), equal_touch_strength=Decimal("15"),
        recency=Decimal("10"), distance_reachability=Decimal("10"),
        unconsumed_status=Decimal("5"),
    )


class TestLiquidityPool:
    def test_valid_pool(self):
        pool = LiquidityPool(
            pool_id="p1", type=LiquidityPoolType.PDH, price=Decimal("2410"),
            timeframe=Timeframe.D1, created_at=_dt(0), score=Decimal("80"),
            score_components=_score(),
        )
        assert pool.consumed_at is None

    def test_consumed_at_without_reason_rejected(self):
        with pytest.raises(ValidationError):
            LiquidityPool(
                pool_id="p1", type=LiquidityPoolType.PDH, price=Decimal("2410"),
                timeframe=Timeframe.D1, created_at=_dt(0), score=Decimal("80"),
                score_components=_score(), consumed_at=_dt(10),
            )

    def test_consumed_with_reason_ok(self):
        pool = LiquidityPool(
            pool_id="p1", type=LiquidityPoolType.PDH, price=Decimal("2410"),
            timeframe=Timeframe.D1, created_at=_dt(0), score=Decimal("80"),
            score_components=_score(), consumed_at=_dt(10),
            consumption_reason="swept",
        )
        assert pool.consumption_reason == "swept"


class TestSweepEvent:
    def test_valid_sweep(self):
        sweep = SweepEvent(
            sweep_id="sw1", liquidity_pool_id="p1", direction=Direction.BEARISH,
            penetration_atr=Decimal("0.1"), sweep_candle_id="c1",
            close_back_candle_id="c2", expiry_at=_dt(20), valid=True,
        )
        assert sweep.valid is True

    def test_non_positive_penetration_rejected(self):
        with pytest.raises(ValidationError):
            SweepEvent(
                sweep_id="sw1", liquidity_pool_id="p1", direction=Direction.BEARISH,
                penetration_atr=Decimal("0"), sweep_candle_id="c1",
                close_back_candle_id="c2", expiry_at=_dt(20), valid=True,
            )


class TestDisplacementEvent:
    def test_valid(self):
        disp = DisplacementEvent(
            displacement_id="d1", direction=Direction.BULLISH, candle_id="c3",
            body_ratio=Decimal("1.6"), range_atr_ratio=Decimal("1.3"),
            broken_swing_id="s1", valid=True, confirmed_at=_dt(0),
        )
        assert disp.valid is True


class TestMSSEvent:
    def test_valid(self):
        mss = MSSEvent(
            mss_id="m1", direction=Direction.BULLISH, sweep_id="sw1",
            displacement_id="d1", broken_swing_id="s1", confirmed_at=_dt(5),
        )
        assert mss.mss_id == "m1"

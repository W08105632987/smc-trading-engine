from datetime import datetime, timedelta, timezone
from decimal import Decimal

from core.domain.common import Direction, Timeframe
from core.domain.liquidity import LiquidityPoolType
from core.domain.market_data import Candle, DataSource
from core.domain.structure import SwingPoint, SwingType
from strategy.liquidity.liquidity_engine import LiquidityEngineImpl


def _dt(hour_offset: float) -> datetime:
    return datetime(2026, 1, 5, 0, 0, tzinfo=timezone.utc) + timedelta(hours=hour_offset)


def make_candle(hour_offset: float, high, low, close, timeframe=Timeframe.H1) -> Candle:
    return Candle(
        candle_id=f"XAUUSD:{timeframe.value}:{_dt(hour_offset).isoformat()}",
        symbol="XAUUSD", timeframe=timeframe,
        open=Decimal(str(close)), high=Decimal(str(high)), low=Decimal(str(low)), close=Decimal(str(close)),
        timestamp_open=_dt(hour_offset - 1), timestamp_close=_dt(hour_offset),
        source=DataSource.BROKER_NATIVE, confirmed=True,
    )


class TestDailyWeeklyPools:
    def test_pdh_pdl_from_previous_completed_day(self):
        # Day 1: hours 1-23 (all within Jan 5). Day 2 starts at hour 24
        # (Jan 6 00:00 UTC exactly).
        day1 = [make_candle(h, high=2400 + h, low=2390 + h, close=2395 + h) for h in range(1, 24)]
        day2_first = make_candle(24, high=2450, low=2440, close=2445)

        engine = LiquidityEngineImpl()
        pools = engine.map_daily_weekly_pools(day1 + [day2_first])

        pdh = next(p for p in pools if p.type == LiquidityPoolType.PDH)
        pdl = next(p for p in pools if p.type == LiquidityPoolType.PDL)
        assert pdh.price == max(c.high for c in day1)
        assert pdl.price == min(c.low for c in day1)
        # Available only from the new day's first candle close — not before.
        assert pdh.created_at == day2_first.timestamp_close

    def test_single_day_produces_no_pdh_pdl(self):
        # No "previous completed day" exists yet.
        day1 = [make_candle(h, high=2400, low=2390, close=2395) for h in range(1, 5)]
        engine = LiquidityEngineImpl()
        pools = engine.map_daily_weekly_pools(day1)
        assert not any(p.type in (LiquidityPoolType.PDH, LiquidityPoolType.PDL) for p in pools)

    def test_empty_candles_returns_empty(self):
        engine = LiquidityEngineImpl()
        assert engine.map_daily_weekly_pools([]) == []


class TestSwingPools:
    def test_confirmed_major_swing_becomes_a_pool(self):
        swing = SwingPoint(
            swing_id="s1", symbol="XAUUSD", timeframe=Timeframe.H4, type=SwingType.HIGH,
            price=Decimal("2410"), pivot_candle_id="p1", formation_timestamp=_dt(0),
            confirmation_timestamp=_dt(10), major_flag=True, major_confirmed_at=_dt(11),
        )
        engine = LiquidityEngineImpl()
        pools = engine.map_swing_pools([swing])
        assert len(pools) == 1
        assert pools[0].type == LiquidityPoolType.SWING_HIGH
        assert pools[0].price == Decimal("2410")
        # Available from the swing's own confirmation time — never earlier.
        assert pools[0].created_at == swing.confirmation_timestamp


class TestEqualLevelPools:
    def test_two_equal_highs_within_tolerance_form_a_pool(self):
        c1 = make_candle(1, high=2410.00, low=2400, close=2405)
        c2 = make_candle(2, high=2410.02, low=2401, close=2406)  # within 0.05 tolerance
        engine = LiquidityEngineImpl()
        pools = engine.map_equal_level_pools([c1, c2], tolerance=Decimal("0.05"))
        equal_highs = [p for p in pools if p.type == LiquidityPoolType.EQUAL_HIGH]
        assert len(equal_highs) == 1

    def test_highs_outside_tolerance_do_not_form_a_pool(self):
        c1 = make_candle(1, high=2410.00, low=2400, close=2405)
        c2 = make_candle(2, high=2415.00, low=2401, close=2406)  # well outside tolerance
        engine = LiquidityEngineImpl()
        pools = engine.map_equal_level_pools([c1, c2], tolerance=Decimal("0.05"))
        equal_highs = [p for p in pools if p.type == LiquidityPoolType.EQUAL_HIGH]
        assert len(equal_highs) == 0


class TestSessionPoolsDeferred:
    def test_session_pools_returns_empty_not_a_guess(self):
        # Session boundaries were never explicitly decided — this must
        # stay empty rather than silently assuming e.g. London hours.
        candles = [make_candle(h, high=2400, low=2390, close=2395) for h in range(1, 5)]
        engine = LiquidityEngineImpl()
        assert engine.map_session_pools(candles) == []


class TestSweepDetection:
    def test_valid_sweep_with_penetration_and_reversal(self):
        pool_price = Decimal("2410")
        pool = LiquidityEngineImpl._build_pool(
            LiquidityPoolType.SWING_HIGH, pool_price, _dt(0)
        )
        sweep_candle = make_candle(1, high=2410.2, low=2408, close=2409.5)
        reversal_candle = make_candle(2, high=2409, low=2404, close=2405)
        candles = [sweep_candle, reversal_candle]
        atr_by_id = {c.candle_id: Decimal("1.0") for c in candles}

        engine = LiquidityEngineImpl()
        sweeps = engine.detect_sweeps(
            candles, [pool], atr_by_id,
            min_penetration_atr_mult=Decimal("0.05"),
            max_penetration_atr_mult=Decimal("0.50"),
            reversal_window=3,
        )

        assert len(sweeps) == 1
        sweep = sweeps[0]
        assert sweep.valid is True
        assert sweep.direction == Direction.BEARISH
        assert sweep.reversal_confirmed_at == reversal_candle.timestamp_close

    def test_penetration_too_small_is_not_a_sweep(self):
        pool_price = Decimal("2410")
        pool = LiquidityEngineImpl._build_pool(LiquidityPoolType.SWING_HIGH, pool_price, _dt(0))
        # Penetration of only 0.01 with ATR=1.0 -> 0.01x ATR, below the 0.05 minimum.
        tiny_candle = make_candle(1, high=2410.01, low=2409, close=2409.5)
        atr_by_id = {tiny_candle.candle_id: Decimal("1.0")}

        engine = LiquidityEngineImpl()
        sweeps = engine.detect_sweeps(
            [tiny_candle], [pool], atr_by_id,
            min_penetration_atr_mult=Decimal("0.05"),
            max_penetration_atr_mult=Decimal("0.50"),
            reversal_window=3,
        )
        assert sweeps == []

    def test_penetration_too_large_is_not_a_sweep(self):
        pool_price = Decimal("2410")
        pool = LiquidityEngineImpl._build_pool(LiquidityPoolType.SWING_HIGH, pool_price, _dt(0))
        # Penetration of 2.0 with ATR=1.0 -> 2.0x ATR, above the 0.50 maximum.
        huge_candle = make_candle(1, high=2412.0, low=2409, close=2409.5)
        atr_by_id = {huge_candle.candle_id: Decimal("1.0")}

        engine = LiquidityEngineImpl()
        sweeps = engine.detect_sweeps(
            [huge_candle], [pool], atr_by_id,
            min_penetration_atr_mult=Decimal("0.05"),
            max_penetration_atr_mult=Decimal("0.50"),
            reversal_window=3,
        )
        assert sweeps == []

    def test_no_reversal_within_window_marks_sweep_invalid(self):
        pool_price = Decimal("2410")
        pool = LiquidityEngineImpl._build_pool(LiquidityPoolType.SWING_HIGH, pool_price, _dt(0))
        sweep_candle = make_candle(1, high=2410.2, low=2408, close=2409.5)
        # Reversal candle closes ABOVE pool price — never confirms bearish reversal.
        no_reversal = make_candle(2, high=2412, low=2409, close=2411)
        candles = [sweep_candle, no_reversal]
        atr_by_id = {c.candle_id: Decimal("1.0") for c in candles}

        engine = LiquidityEngineImpl()
        sweeps = engine.detect_sweeps(
            candles, [pool], atr_by_id,
            min_penetration_atr_mult=Decimal("0.05"),
            max_penetration_atr_mult=Decimal("0.50"),
            reversal_window=1,
        )
        assert len(sweeps) == 1
        assert sweeps[0].valid is False

    def test_candle_before_pool_creation_cannot_trigger_a_sweep(self):
        # No-look-ahead: the pool isn't knowable until created_at.
        pool_price = Decimal("2410")
        pool = LiquidityEngineImpl._build_pool(LiquidityPoolType.SWING_HIGH, pool_price, _dt(10))
        early_candle = make_candle(1, high=2410.2, low=2408, close=2409.5)  # before pool exists
        atr_by_id = {early_candle.candle_id: Decimal("1.0")}

        engine = LiquidityEngineImpl()
        sweeps = engine.detect_sweeps(
            [early_candle], [pool], atr_by_id,
            min_penetration_atr_mult=Decimal("0.05"),
            max_penetration_atr_mult=Decimal("0.50"),
            reversal_window=3,
        )
        assert sweeps == []

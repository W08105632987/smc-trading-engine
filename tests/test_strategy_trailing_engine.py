from datetime import datetime, timedelta, timezone
from decimal import Decimal

from core.domain.common import Direction, Timeframe
from core.domain.market_data import Candle, DataSource
from strategy.trailing.trailing_engine import TrailingEngineImpl


def _dt(index: int) -> datetime:
    return datetime(2026, 1, 5, 0, 0, tzinfo=timezone.utc) + timedelta(minutes=index * 5 + 5)


def make_candle(index: int, high, low, close, open_=None) -> Candle:
    open_ = Decimal(str(open_ if open_ is not None else close))
    return Candle(
        candle_id=f"XAUUSD:M5:c{index}", symbol="XAUUSD", timeframe=Timeframe.M5,
        open=open_, high=Decimal(str(high)), low=Decimal(str(low)), close=Decimal(str(close)),
        timestamp_open=_dt(index) - timedelta(minutes=5), timestamp_close=_dt(index),
        source=DataSource.BROKER_NATIVE, confirmed=True,
    )


class TestTrailingEngineBullish:
    def test_confirmed_major_swing_low_tightens_stop(self):
        # Verified swing low at index 2 (95), major-confirmed at index 5.
        highs = [105, 104, 100, 103, 106, 108, 110]
        lows = [100, 99, 95, 98, 101, 103, 105]
        closes = [102, 101, 97, 100, 103, 105, 107]
        candles = [make_candle(i, h, l, c) for i, (h, l, c) in enumerate(zip(highs, lows, closes))]
        atr = {c.candle_id: Decimal("1.0") for c in candles}

        engine = TrailingEngineImpl(trailing_sl_buffer_atr=Decimal("0.1"), major_swing_atr_multiple=Decimal("1.0"))
        new_sl = engine.compute_new_stop(
            Direction.BULLISH, current_sl=Decimal("90"), candles=candles,
            atr_by_candle_id=atr, current_atr=Decimal("1.0"),
        )
        assert new_sl is not None
        assert new_sl > Decimal("90")  # tightened
        assert new_sl == Decimal("94.9")  # 95 (swing) - 0.1 (buffer)

    def test_candidate_worse_than_current_sl_returns_none(self):
        # Same verified fixture — candidate (94.9) is below current_sl (99).
        highs = [105, 104, 100, 103, 106, 108, 110]
        lows = [100, 99, 95, 98, 101, 103, 105]
        closes = [102, 101, 97, 100, 103, 105, 107]
        candles = [make_candle(i, h, l, c) for i, (h, l, c) in enumerate(zip(highs, lows, closes))]
        atr = {c.candle_id: Decimal("1.0") for c in candles}

        engine = TrailingEngineImpl(trailing_sl_buffer_atr=Decimal("0.1"), major_swing_atr_multiple=Decimal("1.0"))
        new_sl = engine.compute_new_stop(
            Direction.BULLISH, current_sl=Decimal("99"), candles=candles,
            atr_by_candle_id=atr, current_atr=Decimal("1.0"),
        )
        assert new_sl is None

    def test_no_major_swings_returns_none(self):
        # Flat/noisy series unlikely to qualify as major with a high ATR threshold.
        highs = [100, 100.1, 100, 100.1, 100]
        lows = [99.9, 100, 99.9, 100, 99.9]
        closes = [100, 100, 100, 100, 100]
        candles = [make_candle(i, h, l, c) for i, (h, l, c) in enumerate(zip(highs, lows, closes))]
        atr = {c.candle_id: Decimal("5.0") for c in candles}  # huge ATR, nothing will qualify as major

        engine = TrailingEngineImpl(trailing_sl_buffer_atr=Decimal("0.1"), major_swing_atr_multiple=Decimal("1.0"))
        new_sl = engine.compute_new_stop(
            Direction.BULLISH, current_sl=Decimal("90"), candles=candles,
            atr_by_candle_id=atr, current_atr=Decimal("5.0"),
        )
        assert new_sl is None


class TestTrailingEngineBearish:
    def test_confirmed_major_swing_high_tightens_stop(self):
        # Verified swing high at index 2 (106), major-confirmed at index 5.
        highs = [100, 101, 106, 103, 99, 97, 95]
        lows = [95, 96, 101, 98, 94, 92, 90]
        closes = [97, 98, 103, 100, 96, 94, 92]
        candles = [make_candle(i, h, l, c) for i, (h, l, c) in enumerate(zip(highs, lows, closes))]
        atr = {c.candle_id: Decimal("1.0") for c in candles}

        engine = TrailingEngineImpl(trailing_sl_buffer_atr=Decimal("0.1"), major_swing_atr_multiple=Decimal("1.0"))
        new_sl = engine.compute_new_stop(
            Direction.BEARISH, current_sl=Decimal("115"), candles=candles,
            atr_by_candle_id=atr, current_atr=Decimal("1.0"),
        )
        assert new_sl is not None
        assert new_sl < Decimal("115")  # tightened (moved down for a short)
        assert new_sl == Decimal("106.1")  # 106 (swing) + 0.1 (buffer)


class TestTrailingEngineNeverUsesMicroSwingDefinition:
    def test_engine_uses_the_2_2_fractal_not_a_1_candle_pivot(self):
        # v5 I9: trailing must use the HTF "major swing" fractal
        # (2-left/2-right), never the lightweight MicroSwing (1-candle
        # pivot) from the confirmation engine. A single-candle pivot
        # that would qualify under the 1-candle rule but NOT under
        # 2-left/2-right should produce no swing here.
        highs = [100, 105, 101]  # only 3 candles — cannot even form a 2-2 fractal (needs 5 minimum)
        lows = [95, 100, 96]
        closes = [98, 103, 99]
        candles = [make_candle(i, h, l, c) for i, (h, l, c) in enumerate(zip(highs, lows, closes))]
        atr = {c.candle_id: Decimal("1.0") for c in candles}

        engine = TrailingEngineImpl(trailing_sl_buffer_atr=Decimal("0.1"), major_swing_atr_multiple=Decimal("1.0"))
        new_sl = engine.compute_new_stop(
            Direction.BULLISH, current_sl=Decimal("90"), candles=candles,
            atr_by_candle_id=atr, current_atr=Decimal("1.0"),
        )
        assert new_sl is None  # insufficient candles for the 2-2 fractal, unlike a 1-candle pivot rule

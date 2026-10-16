from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from core.domain.common import Timeframe
from core.domain.market_data import Candle, DataSource
from strategy.indicators.atr import (
    DEFAULT_ATR_PERIOD,
    atr_provider_from_period,
    compute_atr_series,
)


def _dt(i: int) -> datetime:
    return datetime(2026, 1, 5, tzinfo=timezone.utc) + timedelta(minutes=5 * i)


def make_candle(i: int, high: float, low: float, close: float) -> Candle:
    return Candle(
        candle_id=f"XAUUSD:M5:{i}", symbol="XAUUSD", timeframe=Timeframe.M5,
        open=Decimal(str(close)), high=Decimal(str(high)), low=Decimal(str(low)), close=Decimal(str(close)),
        timestamp_open=_dt(i), timestamp_close=_dt(i + 1), source=DataSource.BROKER_NATIVE, confirmed=True,
    )


# 20 candles with clearly varying true range, enough to exceed default period=14.
DATA = [
    (2410, 2400, 2405), (2412, 2402, 2408), (2415, 2405, 2410), (2411, 2401, 2406),
    (2420, 2408, 2418), (2422, 2412, 2415), (2418, 2408, 2412), (2425, 2410, 2420),
    (2430, 2415, 2428), (2429, 2419, 2422), (2435, 2420, 2430), (2440, 2425, 2438),
    (2436, 2426, 2430), (2445, 2430, 2440),  # <- 14th candle, first ATR should appear here
    (2450, 2438, 2448), (2455, 2440, 2452), (2452, 2442, 2446), (2460, 2448, 2458),
    (2458, 2448, 2450), (2465, 2452, 2462),
]


def make_series():
    return [make_candle(i, h, l, c) for i, (h, l, c) in enumerate(DATA)]


class TestTrueRangeAndWarmup:
    def test_no_atr_before_period_candles(self):
        candles = make_series()
        series = compute_atr_series(candles, period=14)
        for c in candles[:13]:
            assert c.candle_id not in series.by_candle_id

    def test_first_valid_atr_is_exactly_at_period_th_candle(self):
        candles = make_series()
        series = compute_atr_series(candles, period=14)
        assert series.first_valid_candle_id == candles[13].candle_id
        assert candles[13].candle_id in series.by_candle_id

    def test_insufficient_history_yields_no_values_at_all(self):
        candles = make_series()[:10]  # fewer than period=14
        series = compute_atr_series(candles, period=14)
        assert series.by_candle_id == {}
        assert series.first_valid_candle_id is None

    def test_empty_input_yields_empty_series(self):
        series = compute_atr_series([], period=14)
        assert series.by_candle_id == {}
        assert series.first_valid_candle_id is None

    def test_first_candle_true_range_is_its_own_high_low_range(self):
        # Indirectly verified: with period=1, the very first candle
        # must be valid immediately, and its ATR equals high-low
        # (no previous close exists to widen the true range).
        candles = make_series()[:3]
        series = compute_atr_series(candles, period=1)
        first = candles[0]
        expected = first.high - first.low
        assert series.by_candle_id[first.candle_id] == Decimal(str(float(expected)))


class TestDeterminismAndNoLookahead:
    def test_repeated_calculation_is_identical(self):
        candles = make_series()
        a = compute_atr_series(candles, period=14)
        b = compute_atr_series(candles, period=14)
        assert a.by_candle_id == b.by_candle_id

    def test_atr_at_candle_i_unaffected_by_candles_after_i(self):
        full = make_series()
        truncated = full[:16]
        series_full = compute_atr_series(full, period=14)
        series_truncated = compute_atr_series(truncated, period=14)
        for c in truncated:
            if c.candle_id in series_truncated.by_candle_id:
                assert series_truncated.by_candle_id[c.candle_id] == series_full.by_candle_id[c.candle_id]

    def test_chronological_updates_produce_a_value_for_every_post_warmup_candle(self):
        candles = make_series()
        series = compute_atr_series(candles, period=14)
        for c in candles[13:]:
            assert c.candle_id in series.by_candle_id
            assert series.by_candle_id[c.candle_id] > 0


class TestInputContracts:
    def test_rejects_unsorted_input(self):
        candles = make_series()
        shuffled = [candles[1], candles[0]] + candles[2:]
        with pytest.raises(ValueError):
            compute_atr_series(shuffled, period=14)

    def test_rejects_mixed_timeframes(self):
        candles = make_series()
        bad = candles[0].model_copy(update={"timeframe": Timeframe.H4, "candle_id": "bad"})
        with pytest.raises(ValueError):
            compute_atr_series([candles[0], bad], period=14)

    def test_default_period_matches_documented_default(self):
        assert DEFAULT_ATR_PERIOD == 14


class TestProviderAdapter:
    def test_provider_matches_direct_computation(self):
        candles = make_series()
        provider = atr_provider_from_period(period=14)
        direct = compute_atr_series(candles, period=14).by_candle_id
        via_provider = provider(Timeframe.M5, candles)
        assert direct == via_provider

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from core.domain.common import Timeframe
from market_data.ticks.tick_aggregator import Tick, aggregate_ticks_to_m1


def _t(second_offset: int, bid: str, symbol: str = "XAUUSD", base: datetime = datetime(2026, 1, 5, 10, 0, tzinfo=timezone.utc)) -> Tick:
    return Tick(symbol=symbol, timestamp=base + timedelta(seconds=second_offset), bid=Decimal(bid), ask=Decimal(bid) + Decimal("0.5"))


class TestBasicAggregation:
    """Hand-verified before writing (see milestone chat log): minute 0
    has ticks at 5s/30s/58s giving O=2400.0 H=2402.0 L=2399.5 C=2399.5;
    minute 1 is deliberately empty; minute 2 has one tick."""

    def test_ohlc_and_boundaries_correct(self):
        ticks = [_t(5, "2400.0"), _t(30, "2402.0"), _t(58, "2399.5"), _t(125, "2401.0")]
        candles, gaps = aggregate_ticks_to_m1(ticks, "XAUUSD")

        assert len(candles) == 2  # the empty minute produced nothing
        c0, c2 = candles
        assert (c0.open, c0.high, c0.low, c0.close) == (Decimal("2400.0"), Decimal("2402.0"), Decimal("2399.5"), Decimal("2399.5"))
        assert c0.timestamp_open == datetime(2026, 1, 5, 10, 0, tzinfo=timezone.utc)
        assert c0.timestamp_close == datetime(2026, 1, 5, 10, 1, tzinfo=timezone.utc)
        assert c2.timestamp_open == datetime(2026, 1, 5, 10, 2, tzinfo=timezone.utc)
        assert c0.timeframe == Timeframe.M1

    def test_gap_for_the_empty_minute_is_recorded(self):
        ticks = [_t(5, "2400.0"), _t(125, "2401.0")]
        candles, gaps = aggregate_ticks_to_m1(ticks, "XAUUSD")
        assert len(gaps) == 1
        assert gaps[0].duration == timedelta(minutes=1)
        assert gaps[0].affected_timeframe == "M1"


class TestNeverSynthesizesACandle:
    """Requirement 6/7: a multi-hour silence must leave an equally
    multi-hour hole in the output, never a fabricated flat candle."""

    def test_large_silence_produces_no_candles_for_the_missing_period(self):
        ticks = [_t(0, "2400.0"), _t(3 * 3600, "2450.0")]  # 3-hour silence
        candles, gaps = aggregate_ticks_to_m1(ticks, "XAUUSD")
        assert len(candles) == 2  # exactly the two real minutes, nothing invented in between
        assert gaps[0].duration == timedelta(hours=3) - timedelta(minutes=1)


class TestInputDiscipline:
    def test_empty_input_returns_empty_output(self):
        assert aggregate_ticks_to_m1([], "XAUUSD") == ([], [])

    def test_mismatched_symbol_raises(self):
        ticks = [_t(0, "2400.0", symbol="EURUSD")]
        with pytest.raises(ValueError, match="does not match"):
            aggregate_ticks_to_m1(ticks, "XAUUSD")

    def test_unsorted_input_raises_rather_than_silently_sorting(self):
        ticks = [_t(60, "2400.0"), _t(0, "2401.0")]
        with pytest.raises(ValueError, match="sorted ascending"):
            aggregate_ticks_to_m1(ticks, "XAUUSD")


class TestDeterminism:
    def test_same_ticks_produce_identical_candles(self):
        ticks = [_t(5, "2400.0"), _t(30, "2402.0")]
        c1, g1 = aggregate_ticks_to_m1(ticks, "XAUUSD")
        c2, g2 = aggregate_ticks_to_m1(ticks, "XAUUSD")
        assert c1 == c2
        assert g1 == g2

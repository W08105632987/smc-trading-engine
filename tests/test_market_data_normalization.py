from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from core.domain.common import Timeframe
from core.domain.market_data import Candle, DataQualityFlag, DataSource
from market_data.normalization.candle_validator import DuplicateCandleError, normalize_candles


def _dt(minute: int) -> datetime:
    return datetime(2026, 1, 5, 0, 0, tzinfo=timezone.utc) + timedelta(minutes=minute)


def make_candle(open_min: int, close_min: int, **overrides) -> Candle:
    defaults = dict(
        candle_id=f"XAUUSD:M5:{_dt(close_min).isoformat()}",
        symbol="XAUUSD", timeframe=Timeframe.M5,
        open=Decimal("2400"), high=Decimal("2401"), low=Decimal("2399"), close=Decimal("2400.5"),
        timestamp_open=_dt(open_min), timestamp_close=_dt(close_min),
        source=DataSource.BROKER_NATIVE, confirmed=True,
    )
    defaults.update(overrides)
    return Candle(**defaults)


class TestNormalizeCandles:
    def test_sorts_chronologically(self):
        c1 = make_candle(5, 10)
        c2 = make_candle(0, 5)
        result = normalize_candles([c1, c2])
        assert result[0].timestamp_close < result[1].timestamp_close

    def test_identical_duplicate_collapsed(self):
        c1 = make_candle(0, 5)
        c2 = make_candle(0, 5)  # identical
        result = normalize_candles([c1, c2])
        assert len(result) == 1

    def test_conflicting_duplicate_raises(self):
        c1 = make_candle(0, 5, close=Decimal("2400.5"))
        c2 = make_candle(0, 5, close=Decimal("2400.6"))  # same key, different value, still valid OHLC
        with pytest.raises(DuplicateCandleError):
            normalize_candles([c1, c2])

    def test_no_gap_flagged_for_contiguous_candles(self):
        c1 = make_candle(0, 5)
        c2 = make_candle(5, 10)
        result = normalize_candles([c1, c2])
        assert DataQualityFlag.GAP not in result[1].data_quality_flags

    def test_gap_flagged_for_non_contiguous_candles(self):
        c1 = make_candle(0, 5)
        c2 = make_candle(20, 25)  # big jump — a gap
        result = normalize_candles([c1, c2])
        assert DataQualityFlag.GAP in result[1].data_quality_flags

    def test_first_candle_never_flagged_as_gap(self):
        c1 = make_candle(100, 105)  # arbitrary start, no "previous" to compare
        result = normalize_candles([c1])
        assert DataQualityFlag.GAP not in result[0].data_quality_flags

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from core.domain.common import Timeframe
from core.domain.market_data import Candle, DataQualityFlag, DataSource
from market_data.aggregation.timeframe_aggregator import aggregate_candles


def _m1(minute_offset: int, o: str, h: str, l: str, c: str, base: datetime = datetime(2026, 1, 5, 0, 0, tzinfo=timezone.utc)) -> Candle:
    ts_open = base + timedelta(minutes=minute_offset)
    ts_close = ts_open + timedelta(minutes=1)
    return Candle(
        candle_id=Candle.build_id("XAUUSD", Timeframe.M1, ts_close), symbol="XAUUSD", timeframe=Timeframe.M1,
        open=Decimal(o), high=Decimal(h), low=Decimal(l), close=Decimal(c),
        timestamp_open=ts_open, timestamp_close=ts_close, source=DataSource.AGGREGATED, confirmed=True,
    )


class TestFullCoverage:
    """Hand-verified before writing: 5 contiguous M1 candles (minutes
    0-4) aggregate into exactly one clean M5 candle with no gap flag."""

    def test_clean_m5_from_five_contiguous_m1(self):
        m1s = [_m1(i, f"240{i}.0", f"240{i}.5", f"239{i}.5", f"240{i}.2") for i in range(5)]
        m5 = aggregate_candles(m1s, Timeframe.M5, "XAUUSD")
        assert len(m5) == 1
        candle = m5[0]
        assert candle.timestamp_open == datetime(2026, 1, 5, 0, 0, tzinfo=timezone.utc)
        assert candle.timestamp_close == datetime(2026, 1, 5, 0, 5, tzinfo=timezone.utc)
        assert candle.data_quality_flags == ()
        assert candle.open == m1s[0].open
        assert candle.close == m1s[-1].close
        assert candle.high == max(c.high for c in m1s)
        assert candle.low == min(c.low for c in m1s)


class TestPartialCoverageIsFlaggedNotHidden:
    def test_missing_middle_minute_produces_flagged_candle(self):
        m1s = [_m1(i, "2400.0", "2400.5", "2399.5", "2400.2") for i in (0, 1, 3, 4)]  # minute 2 missing
        m5 = aggregate_candles(m1s, Timeframe.M5, "XAUUSD")
        assert len(m5) == 1
        assert DataQualityFlag.GAP in m5[0].data_quality_flags
        # still built honestly from the real constituent data, not hidden or dropped
        assert m5[0].open == m1s[0].open
        assert m5[0].close == m1s[-1].close

    def test_window_not_starting_at_its_own_boundary_is_flagged(self):
        # only minutes 1-4 present (minute 0 missing) -- window boundary itself is incomplete
        m1s = [_m1(i, "2400.0", "2400.5", "2399.5", "2400.2") for i in (1, 2, 3, 4)]
        m5 = aggregate_candles(m1s, Timeframe.M5, "XAUUSD")
        assert DataQualityFlag.GAP in m5[0].data_quality_flags


class TestEmptyWindowsNeverAppear:
    def test_wholly_missing_window_produces_no_candle(self):
        # minutes 0-4 present, minutes 5-9 (the next M5 window) entirely absent, minutes 10-14 present
        m1s = (
            [_m1(i, "2400.0", "2400.5", "2399.5", "2400.2") for i in range(5)]
            + [_m1(i, "2410.0", "2410.5", "2409.5", "2410.2") for i in range(10, 15)]
        )
        m5 = aggregate_candles(m1s, Timeframe.M5, "XAUUSD")
        assert len(m5) == 2  # not 3 -- the wholly-empty middle window never appears
        assert m5[0].timestamp_open == datetime(2026, 1, 5, 0, 0, tzinfo=timezone.utc)
        assert m5[1].timestamp_open == datetime(2026, 1, 5, 0, 10, tzinfo=timezone.utc)


class TestBoundaryAlignment:
    """UTC-epoch alignment: H4 buckets must land on 00:00/04:00/08:00...
    boundaries, not on wherever the first candle happens to start."""

    def test_h4_bucket_aligns_to_four_hour_utc_boundaries(self):
        # An M15 series entirely within 02:00-02:30 must aggregate into
        # the 00:00-04:00 H4 bucket, not a bucket starting at 02:00.
        base = datetime(2026, 1, 5, 2, 0, tzinfo=timezone.utc)
        m15s = [
            Candle(
                candle_id=Candle.build_id("XAUUSD", Timeframe.M15, base + timedelta(minutes=15 * (i + 1))),
                symbol="XAUUSD", timeframe=Timeframe.M15,
                open=Decimal("2400"), high=Decimal("2401"), low=Decimal("2399"), close=Decimal("2400.5"),
                timestamp_open=base + timedelta(minutes=15 * i), timestamp_close=base + timedelta(minutes=15 * (i + 1)),
                source=DataSource.AGGREGATED, confirmed=True,
            )
            for i in range(2)
        ]
        h4 = aggregate_candles(m15s, Timeframe.H4, "XAUUSD")
        assert len(h4) == 1
        assert h4[0].timestamp_open == datetime(2026, 1, 5, 0, 0, tzinfo=timezone.utc)
        assert h4[0].timestamp_close == datetime(2026, 1, 5, 4, 0, tzinfo=timezone.utc)
        assert DataQualityFlag.GAP in h4[0].data_quality_flags  # far from full 4h coverage


class TestInputDiscipline:
    def test_empty_input_returns_empty_output(self):
        assert aggregate_candles([], Timeframe.M5, "XAUUSD") == []

    def test_mixed_timeframe_input_raises(self):
        m1 = _m1(0, "2400", "2401", "2399", "2400.5")
        m5 = Candle(
            candle_id=Candle.build_id("XAUUSD", Timeframe.M5, m1.timestamp_close + timedelta(minutes=5)),
            symbol="XAUUSD", timeframe=Timeframe.M5, open=Decimal("2400"), high=Decimal("2401"),
            low=Decimal("2399"), close=Decimal("2400.5"), timestamp_open=m1.timestamp_close,
            timestamp_close=m1.timestamp_close + timedelta(minutes=5), source=DataSource.AGGREGATED, confirmed=True,
        )
        with pytest.raises(ValueError, match="same timeframe"):
            aggregate_candles([m1, m5], Timeframe.H1, "XAUUSD")

    def test_unsorted_input_raises(self):
        m1s = [_m1(5, "2400", "2401", "2399", "2400.5"), _m1(0, "2400", "2401", "2399", "2400.5")]
        with pytest.raises(ValueError, match="sorted ascending"):
            aggregate_candles(m1s, Timeframe.M5, "XAUUSD")

    def test_source_not_finer_than_target_raises(self):
        h4 = _m1(0, "2400", "2401", "2399", "2400.5").model_copy(update={"timeframe": Timeframe.H4})
        with pytest.raises(ValueError, match="not finer"):
            aggregate_candles([h4], Timeframe.M5, "XAUUSD")


class TestDeterminism:
    def test_same_input_produces_identical_output(self):
        m1s = [_m1(i, "2400.0", "2400.5", "2399.5", "2400.2") for i in range(5)]
        first = aggregate_candles(m1s, Timeframe.M5, "XAUUSD")
        second = aggregate_candles(m1s, Timeframe.M5, "XAUUSD")
        assert first == second

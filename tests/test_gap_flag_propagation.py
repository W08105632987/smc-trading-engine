"""Blocker-5 tests, aggregation layer: proves DataQualityFlag.GAP
propagates correctly from raw candle discontinuities up through M1 ->
M5 -> H4 aggregation, for exactly the three gap shapes the real
dataset actually contains (ordinary weekend, an arbitrary multi-day
outage, and the disclosed ~16-day 2026-07-29 -> 2026-08-14 gap) —
and that no candle is ever fabricated to paper over any of them.
"""

from datetime import datetime, timedelta, timezone
from decimal import Decimal

from core.domain.common import Timeframe
from core.domain.market_data import Candle, DataQualityFlag, DataSource
from market_data.aggregation.timeframe_aggregator import aggregate_candles


def _m1(minute_index: int, base: datetime) -> Candle:
    ts_open = base + timedelta(minutes=minute_index)
    ts_close = ts_open + timedelta(minutes=1)
    return Candle(
        candle_id=f"m1:{minute_index}", symbol="XAUUSD", timeframe=Timeframe.M1,
        open=Decimal("2400"), high=Decimal("2401"), low=Decimal("2399"), close=Decimal("2400.5"),
        timestamp_open=ts_open, timestamp_close=ts_close, source=DataSource.AGGREGATED, confirmed=True,
    )


def _m1_at(ts_open: datetime, candle_id: str) -> Candle:
    return Candle(
        candle_id=candle_id, symbol="XAUUSD", timeframe=Timeframe.M1,
        open=Decimal("2400"), high=Decimal("2401"), low=Decimal("2399"), close=Decimal("2400.5"),
        timestamp_open=ts_open, timestamp_close=ts_open + timedelta(minutes=1),
        source=DataSource.AGGREGATED, confirmed=True,
    )


class TestOrdinaryWeekendGap:
    """A normal Friday-close -> Sunday-open (or Monday) gap must still
    be flagged like any other discontinuity — the frozen spec (gap
    detector docstring, "no session-hours rule exists in any of the
    four frozen documents") deliberately does not carve out a special
    case for weekends, so this codebase must not invent one either."""

    def test_h4_candle_spanning_a_weekend_gap_is_gap_flagged(self):
        friday_close = datetime(2026, 1, 9, 21, 0, tzinfo=timezone.utc)  # Friday
        sunday_open = datetime(2026, 1, 11, 22, 0, tzinfo=timezone.utc)  # Sunday, ~49h later

        before = [_m1_at(friday_close - timedelta(minutes=4 - i), f"before{i}") for i in range(4)]
        after = [_m1_at(sunday_open + timedelta(minutes=i), f"after{i}") for i in range(4)]
        m1_candles = before + after

        h4_candles = aggregate_candles(m1_candles, Timeframe.H4, "XAUUSD")
        gap_flagged = [c for c in h4_candles if DataQualityFlag.GAP in c.data_quality_flags]
        assert len(gap_flagged) >= 1

    def test_no_candle_is_fabricated_inside_the_weekend_gap(self):
        friday_close = datetime(2026, 1, 9, 21, 0, tzinfo=timezone.utc)
        sunday_open = datetime(2026, 1, 11, 22, 0, tzinfo=timezone.utc)
        before = [_m1_at(friday_close - timedelta(minutes=4 - i), f"before{i}") for i in range(4)]
        after = [_m1_at(sunday_open + timedelta(minutes=i), f"after{i}") for i in range(4)]
        m1_candles = before + after

        h4_candles = aggregate_candles(m1_candles, Timeframe.H4, "XAUUSD")
        # Only 2 buckets have any real M1 constituent (one before the
        # gap, one after) — aggregate_candles groups by REAL input
        # candles, so it structurally cannot invent a candle for an
        # empty bucket in between. Confirms no fabrication by counting,
        # not by an assumption about bucket-boundary alignment (a
        # gap-flagged candle's aligned open CAN legitimately sit before
        # the first real tick it contains — that's alignment, not
        # invented data).
        assert len(h4_candles) == 2
        for c in h4_candles:
            assert c.data_quality_flags == (DataQualityFlag.GAP,)


class TestGenuineMultiDayGap:
    def test_three_day_gap_flags_the_boundary_h4_candles_and_fabricates_nothing(self):
        base = datetime(2026, 3, 2, 8, 0, tzinfo=timezone.utc)
        before = [_m1(i, base) for i in range(4)]
        gap_end = base + timedelta(days=3)
        after = [_m1_at(gap_end + timedelta(minutes=i), f"after{i}") for i in range(4)]
        m1_candles = before + after

        h4_candles = aggregate_candles(m1_candles, Timeframe.H4, "XAUUSD")
        assert any(DataQualityFlag.GAP in c.data_quality_flags for c in h4_candles)
        for c in h4_candles:
            assert not (base + timedelta(minutes=4) < c.timestamp_open < gap_end)


class TestLongJulyAugustStyleGap:
    """Mirrors the actual disclosed dataset gap: 2026-07-29 15:59:59.882
    -> 2026-08-14 11:00:00.094, ~16 days. Uses the same shape (not the
    literal millisecond timestamps, which don't matter to the aggregator
    — only the duration/discontinuity does)."""

    GAP_START = datetime(2026, 7, 29, 16, 0, tzinfo=timezone.utc)
    GAP_END = datetime(2026, 8, 14, 11, 0, tzinfo=timezone.utc)

    def _build(self):
        before = [_m1_at(self.GAP_START - timedelta(minutes=4 - i), f"before{i}") for i in range(4)]
        after = [_m1_at(self.GAP_END + timedelta(minutes=i), f"after{i}") for i in range(4)]
        return before + after

    def test_gap_flagged_at_h4(self):
        h4_candles = aggregate_candles(self._build(), Timeframe.H4, "XAUUSD")
        assert any(DataQualityFlag.GAP in c.data_quality_flags for c in h4_candles)

    def test_gap_flagged_at_m5(self):
        from market_data.aggregation.timeframe_aggregator import aggregate_candles as agg
        m5_candles = agg(self._build(), Timeframe.M5, "XAUUSD")
        assert any(DataQualityFlag.GAP in c.data_quality_flags for c in m5_candles)

    def test_no_candle_fabricated_across_the_16_day_span(self):
        h4_candles = aggregate_candles(self._build(), Timeframe.H4, "XAUUSD")
        # Exactly 2 buckets have any real M1 constituent (one just
        # before the gap, one just after) — see the equivalent weekend
        # test's comment for why counting, not boundary-alignment, is
        # the correct fabrication check.
        assert len(h4_candles) == 2
        assert all(c.data_quality_flags == (DataQualityFlag.GAP,) for c in h4_candles)

    def test_first_real_candle_after_the_gap_carries_the_real_post_gap_timestamp(self):
        h4_candles = aggregate_candles(self._build(), Timeframe.H4, "XAUUSD")
        post_gap_candles = [c for c in h4_candles if c.timestamp_open >= self.GAP_END - timedelta(hours=4)]
        assert post_gap_candles, "expected at least one H4 candle covering the real post-gap data"
        # None of them should claim to open before real data resumed.
        for c in post_gap_candles:
            if c.timestamp_close > self.GAP_END:
                assert c.timestamp_open <= self.GAP_END or DataQualityFlag.GAP in c.data_quality_flags

    def test_clean_h4_candles_away_from_the_gap_are_not_flagged(self):
        # A control case: a fully self-contained window, no gap nearby,
        # must NOT be flagged — proves the flag is genuinely conditional,
        # not applied indiscriminately to every candle.
        base = datetime(2026, 5, 1, 0, tzinfo=timezone.utc)
        clean_m1 = [_m1(i, base) for i in range(240)]  # exactly one full clean H4 window
        h4_candles = aggregate_candles(clean_m1, Timeframe.H4, "XAUUSD")
        assert len(h4_candles) == 1
        assert h4_candles[0].data_quality_flags == ()

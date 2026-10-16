import time
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from core.domain.common import Timeframe
from core.domain.market_data import Candle, DataSource
from market_data.candle_store.store import CandleStore


def _dt(minute: int) -> datetime:
    return datetime(2026, 1, 5, tzinfo=timezone.utc) + timedelta(minutes=minute)


def make_candle(close_min: int, candle_id: str = None) -> Candle:
    return Candle(
        candle_id=candle_id or f"XAUUSD:M1:{close_min}",
        symbol="XAUUSD", timeframe=Timeframe.M1,
        open=Decimal("2400"), high=Decimal("2401"), low=Decimal("2399"), close=Decimal("2400.5"),
        timestamp_open=_dt(close_min - 1), timestamp_close=_dt(close_min),
        source=DataSource.AGGREGATED, confirmed=True,
    )


class TestDuplicateDetectionFix:
    """Regression test for the latent bug fixed alongside the O(n^2)
    rewrite: re-adding an identical candle_id at the SAME timestamp_close
    must be a no-op, not a second entry. Previously untested."""

    def test_readding_identical_candle_is_a_no_op(self):
        store = CandleStore()
        c = make_candle(5)
        store.add(c)
        store.add(c)  # exact same object/id/timestamp again
        assert store.all("XAUUSD", Timeframe.M1) == [c]

    def test_readding_identical_candle_after_other_inserts_is_still_a_no_op(self):
        store = CandleStore()
        store.add(make_candle(1))
        store.add(make_candle(5))
        store.add(make_candle(10))
        dup = make_candle(5)
        store.add(dup)  # same id + timestamp as the second insert above
        all_candles = store.all("XAUUSD", Timeframe.M1)
        assert len(all_candles) == 3
        assert [c.timestamp_close for c in all_candles] == [_dt(1), _dt(5), _dt(10)]

    def test_two_different_candles_at_the_same_timestamp_are_both_kept(self):
        # Different candle_id, same timestamp_close — a genuine
        # data-quality edge case, not a duplicate; both must survive.
        store = CandleStore()
        a = make_candle(5, candle_id="A")
        b = make_candle(5, candle_id="B")
        store.add(a)
        store.add(b)
        assert len(store.all("XAUUSD", Timeframe.M1)) == 2


class TestNoFutureCandleLeakage:
    def test_get_up_to_still_excludes_future_candles_at_scale(self):
        store = CandleStore()
        store.add_many([make_candle(i) for i in range(1, 2001)])
        result = store.get_up_to("XAUUSD", Timeframe.M1, _dt(1000))
        assert len(result) == 1000
        assert all(c.timestamp_close <= _dt(1000) for c in result)
        assert max(c.timestamp_close for c in result) == _dt(1000)

    def test_out_of_order_insertion_still_yields_chronological_order(self):
        store = CandleStore()
        for i in [50, 10, 30, 1, 100, 75]:
            store.add(make_candle(i))
        closes = [c.timestamp_close for c in store.all("XAUUSD", Timeframe.M1)]
        assert closes == sorted(closes)


class TestScalingIsNoLongerQuadratic:
    """Proves get_up_to() no longer scans the whole collection: a query
    for a SMALL prefix of a LARGE store must be fast regardless of the
    store's total size, and total time across many queries must grow
    much slower than O(n^2)."""

    def test_get_up_to_cost_does_not_scale_with_total_store_size(self):
        store = CandleStore()
        n = 60_000
        store.add_many([make_candle(i) for i in range(1, n + 1)])

        # A query near the START of a big store must be fast — with the
        # old full-bucket-scan implementation this cost the same as a
        # query near the end (both scanned all n candles).
        t0 = time.perf_counter()
        for _ in range(200):
            store.get_up_to("XAUUSD", Timeframe.M1, _dt(10))
        early_query_time = time.perf_counter() - t0

        t0 = time.perf_counter()
        for _ in range(200):
            store.get_up_to("XAUUSD", Timeframe.M1, _dt(n - 10))
        late_query_time = time.perf_counter() - t0

        # Both should be small and comparable — neither should be
        # anywhere near n candles' worth of scanning. Generous bound to
        # avoid flakiness in shared CI environments while still catching
        # a real regression back to O(n) full scans.
        assert early_query_time < 0.5
        assert late_query_time < 0.5

    def test_sequential_ascending_insertion_is_practical_at_scale(self):
        store = CandleStore()
        n = 100_000
        t0 = time.perf_counter()
        for i in range(1, n + 1):
            store.add(make_candle(i))
        elapsed = time.perf_counter() - t0
        assert len(store.all("XAUUSD", Timeframe.M1)) == n
        # The old O(n^2) implementation took ~14s for just 20,000 inserts
        # in the readiness audit's benchmark; 100,000 ascending inserts
        # here must complete comfortably under that, proving the O(1)
        # amortized append fast-path is actually being taken.
        assert elapsed < 5.0

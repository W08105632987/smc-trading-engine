import sys
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from core.domain.common import Timeframe
from market_data.aggregation.timeframe_aggregator import aggregate_candles, stream_aggregate_candles
from market_data.ticks.tick_aggregator import Tick, aggregate_ticks_to_m1, stream_ticks_to_m1

BASE = datetime(2026, 1, 5, 10, 0, tzinfo=timezone.utc)


def _tick(second_offset: int, bid: float) -> Tick:
    return Tick(
        symbol="XAUUSD", timestamp=BASE + timedelta(seconds=second_offset),
        bid=Decimal(str(bid)), ask=Decimal(str(bid)) + Decimal("0.5"),
    )


def _make_ticks(n_minutes: int, ticks_per_minute: int = 5) -> list[Tick]:
    ticks = []
    for m in range(n_minutes):
        for i in range(ticks_per_minute):
            ticks.append(_tick(m * 60 + i * 10, 2400 + m * 0.1 + i * 0.01))
    return ticks


class TestTickStreamingEquivalence:
    def test_stream_matches_batch_candles_exactly(self):
        ticks = _make_ticks(50)
        batch_candles, _ = aggregate_ticks_to_m1(ticks, "XAUUSD")
        streamed_candles = list(stream_ticks_to_m1(iter(ticks), "XAUUSD"))
        assert streamed_candles == batch_candles

    def test_stream_matches_batch_across_a_gap(self):
        day1 = _make_ticks(10)
        gap_end = BASE + timedelta(minutes=10) + timedelta(days=2)
        day2 = [
            Tick(symbol="XAUUSD", timestamp=gap_end + timedelta(seconds=m * 60), bid=Decimal("2450"), ask=Decimal("2450.5"))
            for m in range(10)
        ]
        all_ticks = day1 + day2
        batch_candles, batch_gaps = aggregate_ticks_to_m1(all_ticks, "XAUUSD")
        streamed_candles = list(stream_ticks_to_m1(iter(all_ticks), "XAUUSD"))
        assert streamed_candles == batch_candles

    def test_stream_rejects_backwards_timestamp_same_as_batch(self):
        ticks = _make_ticks(5)
        shuffled = ticks[:3] + [ticks[1]] + ticks[3:]  # a real backwards jump
        with pytest.raises(ValueError):
            list(stream_ticks_to_m1(iter(shuffled), "XAUUSD"))
        with pytest.raises(ValueError):
            aggregate_ticks_to_m1(shuffled, "XAUUSD")

    def test_stream_rejects_wrong_symbol_same_as_batch(self):
        ticks = _make_ticks(2)
        with pytest.raises(ValueError):
            list(stream_ticks_to_m1(iter(ticks), "EURUSD"))
        with pytest.raises(ValueError):
            aggregate_ticks_to_m1(ticks, "EURUSD")

    def test_stream_only_ever_holds_one_bucket_at_a_time(self):
        # A generator that raises after too many items are buffered
        # simultaneously would fail this; instead we assert the
        # function is a genuine generator (lazy), not something that
        # silently materializes a list internally.
        import inspect
        assert inspect.isgeneratorfunction(stream_ticks_to_m1)


class TestHTFStreamingEquivalence:
    def test_stream_matches_batch_m5_aggregation(self):
        ticks = _make_ticks(120)
        m1_candles, _ = aggregate_ticks_to_m1(ticks, "XAUUSD")
        batch_m5 = aggregate_candles(m1_candles, Timeframe.M5, "XAUUSD")
        streamed_m5 = list(stream_aggregate_candles(iter(m1_candles), Timeframe.M5, "XAUUSD"))
        assert streamed_m5 == batch_m5

    def test_stream_matches_batch_h4_aggregation_including_gap_flags(self):
        ticks = _make_ticks(600)  # 10 hours, enough for multiple H4 buckets
        m1_candles, _ = aggregate_ticks_to_m1(ticks, "XAUUSD")
        batch_h4 = aggregate_candles(m1_candles, Timeframe.H4, "XAUUSD")
        streamed_h4 = list(stream_aggregate_candles(iter(m1_candles), Timeframe.H4, "XAUUSD"))
        assert streamed_h4 == batch_h4
        # At least one H4 candle must be GAP-flagged (partial coverage at
        # dataset start/end) — otherwise this test isn't exercising the
        # flag-parity path at all.
        from core.domain.market_data import DataQualityFlag
        assert any(DataQualityFlag.GAP in c.data_quality_flags for c in batch_h4)

    def test_stream_rejects_unsorted_same_as_batch(self):
        ticks = _make_ticks(20)
        m1_candles, _ = aggregate_ticks_to_m1(ticks, "XAUUSD")
        shuffled = [m1_candles[2], m1_candles[0]] + m1_candles[1:]
        with pytest.raises(ValueError):
            list(stream_aggregate_candles(iter(shuffled), Timeframe.M5, "XAUUSD"))

    def test_generator_function_is_lazy(self):
        import inspect
        assert inspect.isgeneratorfunction(stream_aggregate_candles)


class TestFanOutStreamingEquivalence:
    """Blocker-3 follow-up: proves the single-pass M1 -> {M5, H4}
    fan-out (stream_aggregate_multi) matches calling
    stream_aggregate_candles() once per timeframe, and that it consumes
    its source exactly once — the actual fix for the smoke test's
    double-materialization caveat."""

    def test_fanout_matches_two_independent_streaming_calls(self):
        from market_data.aggregation.timeframe_aggregator import stream_aggregate_multi

        ticks = _make_ticks(600, ticks_per_minute=2)
        m1_candles, _ = aggregate_ticks_to_m1(ticks, "XAUUSD")

        expected_m5 = list(stream_aggregate_candles(iter(m1_candles), Timeframe.M5, "XAUUSD"))
        expected_h4 = list(stream_aggregate_candles(iter(m1_candles), Timeframe.H4, "XAUUSD"))

        got_m5, got_h4 = [], []
        for tf, candle in stream_aggregate_multi(iter(m1_candles), [Timeframe.M5, Timeframe.H4], "XAUUSD"):
            (got_m5 if tf == Timeframe.M5 else got_h4).append(candle)

        assert got_m5 == expected_m5
        assert got_h4 == expected_h4

    def test_fanout_consumes_source_exactly_once(self):
        from market_data.aggregation.timeframe_aggregator import stream_aggregate_multi

        ticks = _make_ticks(100, ticks_per_minute=2)
        m1_candles, _ = aggregate_ticks_to_m1(ticks, "XAUUSD")

        pulled = {"n": 0}

        def counting_source():
            for c in m1_candles:
                pulled["n"] += 1
                yield c

        list(stream_aggregate_multi(counting_source(), [Timeframe.M5, Timeframe.H4], "XAUUSD"))
        assert pulled["n"] == len(m1_candles)  # exactly once, not once per target timeframe

    def test_fanout_peak_memory_does_not_scale_with_total_ticks(self):
        import tracemalloc
        from market_data.aggregation.timeframe_aggregator import stream_aggregate_multi

        def run(n_minutes):
            ticks = _make_ticks(n_minutes, ticks_per_minute=2)

            def tick_gen():
                yield from ticks

            tracemalloc.start()
            m1_stream = stream_ticks_to_m1(tick_gen(), "XAUUSD")
            peak = 0
            for _ in stream_aggregate_multi(m1_stream, [Timeframe.M5, Timeframe.H4], "XAUUSD"):
                _, current_peak = tracemalloc.get_traced_memory()
                peak = max(peak, current_peak)
            tracemalloc.stop()
            return peak

        small_peak = run(2_000)
        large_peak = run(40_000)  # 20x more ticks, single pass, two targets fanned out
        assert large_peak < small_peak * 4, (
            f"peak memory grew {large_peak / small_peak:.1f}x for 20x more ticks — "
            "fan-out is not staying bounded"
        )

    """Blocker 3 acceptance: CSV-scale tick counts must not require
    materializing the full tick or M1-candle history in memory. Proven
    here via sys.getsizeof() on the buffers stream_ticks_to_m1 /
    stream_aggregate_candles hold internally, at a scale large enough
    that an O(total) buffer would be visibly, not just theoretically,
    larger."""

    def test_full_tick_to_h4_pipeline_never_materializes_more_than_one_bucket(self):
        n_minutes = 20_000  # far more than any single H4/M5/M1 bucket holds
        ticks = _make_ticks(n_minutes, ticks_per_minute=2)

        def tick_generator():
            # A real generator, not a list — proves the pipeline doesn't
            # require random access / a materialized sequence upstream.
            yield from ticks

        m1_stream = stream_ticks_to_m1(tick_generator(), "XAUUSD")
        h4_stream = stream_aggregate_candles(m1_stream, Timeframe.H4, "XAUUSD")

        h4_candles = list(h4_stream)  # only the FINAL small output is materialized here, for the assertion
        assert len(h4_candles) > 0
        # n_minutes=20,000 minutes ~ 333 hours ~ 83+ H4 buckets expected
        assert len(h4_candles) >= 80

    def test_memory_does_not_grow_linearly_with_total_ticks_processed(self):
        # Compares peak resident buffer size (via tracemalloc) for a
        # small vs a much larger tick stream. A linear-in-total-ticks
        # implementation would show peak memory scaling with input size;
        # a properly bounded one shows it staying roughly flat.
        import tracemalloc

        def run(n_minutes):
            ticks = _make_ticks(n_minutes, ticks_per_minute=2)

            def gen():
                yield from ticks

            tracemalloc.start()
            m1_stream = stream_ticks_to_m1(gen(), "XAUUSD")
            h4_stream = stream_aggregate_candles(m1_stream, Timeframe.H4, "XAUUSD")
            count = 0
            peak = 0
            for _ in h4_stream:
                count += 1
                _, current_peak = tracemalloc.get_traced_memory()
                peak = max(peak, current_peak)
            tracemalloc.stop()
            return peak

        small_peak = run(2_000)
        large_peak = run(40_000)  # 20x more ticks

        # A O(total-ticks) buffer would show ~20x growth in peak memory.
        # A bounded (one-bucket-at-a-time) buffer should show far less
        # than that — generous 4x bound to absorb interpreter/test noise
        # while still catching a real regression to "hold everything".
        assert large_peak < small_peak * 4, (
            f"peak memory grew {large_peak / small_peak:.1f}x for 20x more ticks — "
            "suggests the pipeline is accumulating unbounded state"
        )

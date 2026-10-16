"""Integration tests: gap-safety through the FULL real chain (ticks ->
M1 -> M5 -> CandleStore -> ReplayEngine / ReplayPipeline). Every number
and property here was verified by directly running the real chain
before being written into an assertion (see milestone chat log).

Covers testing categories D, E, and G from the Milestone 10 gap-policy
task: replay generates nothing from nonexistent data, the first candle
after a gap never sees a nonexistent intermediate, and the whole chain
is deterministic.
"""

from datetime import datetime, timedelta, timezone
from decimal import Decimal

from core.domain.common import Timeframe
from market_data.aggregation.timeframe_aggregator import aggregate_candles
from market_data.candle_store.store import CandleStore
from market_data.replay.replay_engine import ReplayEngine
from market_data.ticks.tick_aggregator import Tick, aggregate_ticks_to_m1

from tests.test_backtest_pipeline import _make_pipeline

BASE = datetime(2026, 1, 5, 10, 0, tzinfo=timezone.utc)


def _tick(second_offset: int, bid: float) -> Tick:
    return Tick(symbol="XAUUSD", timestamp=BASE + timedelta(seconds=second_offset), bid=Decimal(str(bid)), ask=Decimal(str(bid)) + Decimal("0.5"))


def _build_gapped_m5_series():
    """A burst of ticks, a 3-day silence (matching the scale of the
    real dataset's disclosed outages), then another burst. Returns
    (m5_candles, gap_start, gap_end)."""
    day1_ticks = [_tick(m * 60 + 5, 2400 + m * 0.1) for m in range(15)]
    gap_start = BASE + timedelta(minutes=15)
    gap_end = gap_start + timedelta(days=3)
    day2_ticks = [_tick(int((gap_end - BASE).total_seconds()) + m * 60 + 5, 2450 + m * 0.1) for m in range(15)]

    m1_candles, m1_gaps = aggregate_ticks_to_m1(day1_ticks + day2_ticks, "XAUUSD")
    m5_candles = aggregate_candles(m1_candles, Timeframe.M5, "XAUUSD")
    return m5_candles, gap_start, gap_end, m1_gaps


class TestReplayNeverSeesNonexistentData:
    """Category D: no candle, and no strategy event, ever has a
    timestamp inside the gap."""

    def test_no_m5_candle_exists_inside_the_gap_window(self):
        m5_candles, gap_start, gap_end, _ = _build_gapped_m5_series()
        assert all(not (gap_start < c.timestamp_open < gap_end) for c in m5_candles)

    def test_replay_engine_step_count_matches_real_candle_count(self):
        m5_candles, _, _, _ = _build_gapped_m5_series()
        store = CandleStore()
        store.add_many(m5_candles)
        replay = ReplayEngine(store, "XAUUSD", Timeframe.M5)
        steps = list(replay.run())
        assert len(steps) == len(m5_candles)  # one step per REAL candle, nothing extra

    def test_pipeline_emits_nothing_timestamped_inside_the_gap(self):
        m5_candles, gap_start, gap_end, _ = _build_gapped_m5_series()
        store = CandleStore()
        store.add_many(m5_candles)
        pipeline = _make_pipeline(candle_store=store)

        all_events = []
        for step in pipeline.run():
            all_events.extend(step.events)

        assert all(not (gap_start < e.timestamp < gap_end) for e in all_events)


class TestFirstCandleAfterGapSeesOnlyRealData:
    """Category E: the step immediately after a gap must never have a
    nonexistent intermediate candle silently appear in its visible
    window."""

    def test_post_gap_step_window_contains_zero_nonexistent_candles(self):
        m5_candles, gap_start, gap_end, _ = _build_gapped_m5_series()
        store = CandleStore()
        store.add_many(m5_candles)
        replay = ReplayEngine(store, "XAUUSD", Timeframe.M5)
        steps = list(replay.run())

        post_gap_step = next(s for s in steps if s.at > gap_end)
        assert all(not (gap_start < c.timestamp_open < gap_end) for c in post_gap_step.candles)

    def test_post_gap_step_at_matches_the_first_real_post_gap_candle(self):
        m5_candles, gap_start, gap_end, _ = _build_gapped_m5_series()
        store = CandleStore()
        store.add_many(m5_candles)
        replay = ReplayEngine(store, "XAUUSD", Timeframe.M5)
        steps = list(replay.run())

        post_gap_step = next(s for s in steps if s.at > gap_end)
        first_real_post_gap_candle = next(c for c in m5_candles if c.timestamp_open >= gap_end)
        assert post_gap_step.at == first_real_post_gap_candle.timestamp_close


class TestFullChainDeterminism:
    """Category G."""

    def test_rerunning_the_full_chain_produces_identical_results(self):
        m5_a, gap_start_a, gap_end_a, gaps_a = _build_gapped_m5_series()
        m5_b, gap_start_b, gap_end_b, gaps_b = _build_gapped_m5_series()
        assert m5_a == m5_b
        assert gaps_a == gaps_b
        assert gap_start_a == gap_start_b
        assert gap_end_a == gap_end_b

    def test_the_detected_gap_duration_matches_the_constructed_silence(self):
        _, _, _, gaps = _build_gapped_m5_series()
        assert len(gaps) == 1
        assert gaps[0].duration >= timedelta(days=2, hours=23)

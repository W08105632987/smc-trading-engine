"""Blocker-5 tests, pipeline layer: proves gap-affected H4/M5 candles
are tracked in ReplayState and surfaced on bias-transition events,
that the DEFAULT policy leaves existing behavior completely unchanged
(the frozen "not automatically invalid" rule), and that an opt-in
gap_policy is applied only where documented — never allowed to distort
the "did a new H4 candle actually close" detection.

Builds directly on tests/test_backtest_pipeline.py's own fixtures
(_make_pipeline, CANDLES, _make_setup) and its existing
TestH4CandleCountGating scaffold/style, rather than re-deriving a new
M5/H4 price scenario from scratch.
"""

from datetime import timedelta
from decimal import Decimal

from core.domain.common import Timeframe
from core.domain.market_data import Candle, DataQualityFlag, DataSource

from market_data.candle_store.store import CandleStore

from tests.test_backtest_pipeline import CANDLES, _dt, _make_pipeline, _make_setup


def _h4_candle(i: int, close: Decimal, gap: bool = False) -> Candle:
    ts = _dt(-3) - timedelta(hours=40) + timedelta(hours=4 * i)
    return Candle(
        candle_id=f"h4-{i}", symbol="XAUUSD", timeframe=Timeframe.H4,
        open=close, high=close + Decimal("1"), low=close - Decimal("1"), close=close,
        timestamp_open=ts - timedelta(hours=4), timestamp_close=ts,
        source=DataSource.BROKER_NATIVE, confirmed=True,
        data_quality_flags=(DataQualityFlag.GAP,) if gap else (),
    )


class TestDefaultBehaviorUnchanged:
    """The frozen spec (v5 I10, DEFERRED) says a gap-affected candle is
    NOT automatically invalid — so with no gap_policy supplied, a
    GAP-flagged H4 candle must be treated exactly like a clean one:
    still counted, still fed to the H4 layer, still able to drive a
    recompute."""

    def test_gap_flagged_h4_candle_is_still_counted_by_default(self):
        store = CandleStore()
        h4_candles = [
            _h4_candle(0, Decimal("2395")),
            _h4_candle(1, Decimal("2396"), gap=True),  # gap-affected, must still count
            _h4_candle(2, Decimal("2397")),
        ]
        store.add_many(h4_candles)
        pipeline = _make_pipeline(candle_store=store)
        pipeline.state.active_setups[_make_setup().setup_id] = _make_setup()

        pipeline.step(CANDLES[0].timestamp_close, CANDLES[:1])
        # All 3 H4 candles counted, including the gap-flagged one — not
        # silently excluded by default.
        assert pipeline.state.last_seen_candle_count.get(Timeframe.H4) == 3


class TestGapObservability:
    def test_gap_affected_h4_ids_are_recorded_in_state(self):
        store = CandleStore()
        h4_candles = [
            _h4_candle(0, Decimal("2395")),
            _h4_candle(1, Decimal("2396"), gap=True),
            _h4_candle(2, Decimal("2397")),
        ]
        store.add_many(h4_candles)
        pipeline = _make_pipeline(candle_store=store)
        pipeline.state.active_setups[_make_setup().setup_id] = _make_setup()

        pipeline.step(CANDLES[0].timestamp_close, CANDLES[:1])

        assert "h4-1" in pipeline.state.gap_affected_h4_candle_ids
        assert "h4-0" not in pipeline.state.gap_affected_h4_candle_ids
        assert "h4-2" not in pipeline.state.gap_affected_h4_candle_ids

    def test_gap_affected_ids_accumulate_across_steps_not_reset(self):
        store = CandleStore()
        h4_candles = [_h4_candle(0, Decimal("2395"), gap=True)]
        store.add_many(h4_candles)
        pipeline = _make_pipeline(candle_store=store)
        pipeline.state.active_setups[_make_setup().setup_id] = _make_setup()

        pipeline.step(CANDLES[0].timestamp_close, CANDLES[:1])
        assert "h4-0" in pipeline.state.gap_affected_h4_candle_ids

        # A later step with no new H4 candle must not lose the record.
        pipeline.step(CANDLES[1].timestamp_close, CANDLES[:2])
        assert "h4-0" in pipeline.state.gap_affected_h4_candle_ids

    def test_m5_gap_affected_ids_are_also_recorded(self):
        store = CandleStore()
        pipeline = _make_pipeline(candle_store=store)
        pipeline.state.active_setups[_make_setup().setup_id] = _make_setup()

        flagged_m5 = CANDLES[0].model_copy(update={"data_quality_flags": (DataQualityFlag.GAP,)})
        pipeline.step(flagged_m5.timestamp_close, [flagged_m5])

        assert flagged_m5.candle_id in pipeline.state.gap_affected_m5_candle_ids

    def test_no_gap_affected_ids_recorded_when_data_is_clean(self):
        store = CandleStore()
        store.add_many([_h4_candle(0, Decimal("2395"))])
        pipeline = _make_pipeline(candle_store=store)
        pipeline.state.active_setups[_make_setup().setup_id] = _make_setup()

        pipeline.step(CANDLES[0].timestamp_close, CANDLES[:1])
        assert pipeline.state.gap_affected_h4_candle_ids == set()
        assert pipeline.state.gap_affected_m5_candle_ids == set()


class TestBiasTransitionEventRecordsGapData:
    """Directly exercises _recompute_h4_layer's event-emission path by
    monkeypatching _advance_h4_bias to force a transition — avoids
    needing to hand-construct a full, fragile real H4 price sequence
    just to prove the payload wiring is correct."""

    def test_transition_event_payload_flags_gap_affected_data(self):
        from core.domain.common import Direction
        from core.domain.structure import StructureEvent, StructureEventType
        from core.events.schema import EventType

        store = CandleStore()
        h4_candles = [
            _h4_candle(0, Decimal("2395"), gap=True),
            _h4_candle(1, Decimal("2396")),
        ]
        store.add_many(h4_candles)
        pipeline = _make_pipeline(candle_store=store)  # initial_h4_bias=BULLISH
        pipeline.state.active_setups[_make_setup().setup_id] = _make_setup()

        # A synthetic opposite-direction CHOCH — H4WarningEngineImpl's
        # REAL (unpatched) evaluate() turns this into a genuine
        # BULLISH -> BEARISH_WARNING transition (rule 1 in that module's
        # own docstring). Only structure DETECTION is stubbed, so the
        # actual bias-transition logic being tested is the real one.
        fake_choch = StructureEvent(
            event_id="fake-choch-1", type=StructureEventType.CHOCH, direction=Direction.BEARISH,
            broken_swing_id="fake-swing", confirming_candle_id=h4_candles[-1].candle_id,
            confirmed_at=h4_candles[-1].timestamp_close, timeframe=Timeframe.H4,
        )
        pipeline._structure_detector.detect = lambda candles, swings, prevailing: [fake_choch]

        result = pipeline.step(CANDLES[0].timestamp_close, CANDLES[:1])

        transition_events = [e for e in result.events if e.event_type == EventType.WARNING_ENTERED]
        assert len(transition_events) == 1
        assert transition_events[0].payload["gap_affected_h4_data_seen"] is True



    def test_default_gap_policy_is_identity_and_does_not_exclude_anything(self):
        store = CandleStore()
        store.add_many([
            _h4_candle(0, Decimal("2395")),
            _h4_candle(1, Decimal("2396"), gap=True),
        ])
        pipeline = _make_pipeline(candle_store=store)
        pipeline.state.active_setups[_make_setup().setup_id] = _make_setup()
        pipeline.step(CANDLES[0].timestamp_close, CANDLES[:1])
        assert pipeline.state.last_seen_candle_count.get(Timeframe.H4) == 2

    def test_explicit_opt_in_policy_can_exclude_gap_flagged_candles(self):
        from backtest.pipeline import ReplayPipeline
        import inspect

        def exclude_gap_candles(candles):
            return [c for c in candles if DataQualityFlag.GAP not in c.data_quality_flags]

        store = CandleStore()
        store.add_many([
            _h4_candle(0, Decimal("2395")),
            _h4_candle(1, Decimal("2396"), gap=True),
            _h4_candle(2, Decimal("2397")),
        ])
        pipeline = _make_pipeline(candle_store=store)
        pipeline._gap_policy = exclude_gap_candles  # opt-in, same seam __init__ wires
        pipeline.state.active_setups[_make_setup().setup_id] = _make_setup()

        pipeline.step(CANDLES[0].timestamp_close, CANDLES[:1])

        # RAW count (all 3, including the gap candle) still drives the
        # "did a new candle close" bookkeeping — the policy only changes
        # what the H4 layer's detectors actually see, never this count.
        assert pipeline.state.last_seen_candle_count.get(Timeframe.H4) == 3
        # But the gap-affected id is still recorded (from the raw view,
        # observability is not itself filtered by the policy).
        assert "h4-1" in pipeline.state.gap_affected_h4_candle_ids

    def test_count_gating_is_based_on_raw_store_not_filtered_view(self):
        """Regression test for the specific failure mode flagged during
        review: an opt-in policy that SHRINKS the visible candle list
        must never suppress detection of a genuinely new raw candle on
        a later step, and must never falsely re-trigger on a step where
        nothing new actually closed."""

        def exclude_gap_candles(candles):
            return [c for c in candles if DataQualityFlag.GAP not in c.data_quality_flags]

        store = CandleStore()
        store.add_many([
            _h4_candle(0, Decimal("2395")),
            _h4_candle(1, Decimal("2396"), gap=True),
        ])
        pipeline = _make_pipeline(candle_store=store)
        pipeline._gap_policy = exclude_gap_candles
        pipeline.state.active_setups[_make_setup().setup_id] = _make_setup()

        pipeline.step(CANDLES[0].timestamp_close, CANDLES[:1])
        assert pipeline.state.last_seen_candle_count.get(Timeframe.H4) == 2  # raw count

        # No new raw H4 candle yet — must not "recompute" spuriously.
        pipeline.step(CANDLES[1].timestamp_close, CANDLES[:2])
        assert pipeline.state.last_seen_candle_count.get(Timeframe.H4) == 2

        # A genuinely new raw H4 candle appears — must be detected even
        # though the filtered view's length pattern differs from the
        # raw one.
        store.add(_h4_candle(2, Decimal("2397")))
        pipeline.step(CANDLES[2].timestamp_close, CANDLES[:3])
        assert pipeline.state.last_seen_candle_count.get(Timeframe.H4) == 3


class TestNoLookaheadOrFabricationUnderGapHandling:
    def test_future_h4_candle_still_invisible_regardless_of_gap_flags(self):
        store = CandleStore()
        early = [_h4_candle(0, Decimal("2395"), gap=True)]
        late = Candle(
            candle_id="h4-late", symbol="XAUUSD", timeframe=Timeframe.H4,
            open=Decimal("2400"), high=Decimal("2401"), low=Decimal("2399"), close=Decimal("2400"),
            timestamp_open=CANDLES[1].timestamp_close, timestamp_close=CANDLES[1].timestamp_close + timedelta(hours=4),
            source=DataSource.BROKER_NATIVE, confirmed=True,
        )
        store.add_many(early + [late])
        pipeline = _make_pipeline(candle_store=store)
        pipeline.state.active_setups[_make_setup().setup_id] = _make_setup()

        pipeline.step(CANDLES[0].timestamp_close, CANDLES[:1])
        assert pipeline.state.last_seen_candle_count.get(Timeframe.H4) == 1  # h4-late not yet closed
        assert "h4-late" not in pipeline.state.gap_affected_h4_candle_ids

    def test_events_only_ever_carry_real_candle_close_timestamps(self):
        store = CandleStore()
        store.add_many([
            _h4_candle(0, Decimal("2395")),
            _h4_candle(1, Decimal("2396"), gap=True),
        ])
        pipeline = _make_pipeline(candle_store=store)
        pipeline.state.active_setups[_make_setup().setup_id] = _make_setup()

        result = pipeline.step(CANDLES[0].timestamp_close, CANDLES[:1])
        real_h4_close_times = {c.timestamp_close for c in store.all("XAUUSD", Timeframe.H4)}
        for event in result.events:
            assert event.timestamp == CANDLES[0].timestamp_close or event.timestamp in real_h4_close_times

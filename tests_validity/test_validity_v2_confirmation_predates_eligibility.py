"""VALIDITY DEFECT V2 -- a setup can be CONFIRMED (and traded) by a break candle that closed BEFORE the setup existed.

STATUS: asserts the DESIRED behaviour; EXPECTED TO FAIL on Phase-A-v2/E0.

CLASSIFICATION
  PROVEN CORRECTNESS DEFECT (execution realism / causality), with one POLICY sub-question:
    * Phase A's causality boundary is `setup.created_at` = max(zone creation, MSS confirmation) -- a LOGICAL market time
      (backtest/pipeline.py `_process_touches` ~828-846 passes `available_at=setup.created_at`). The step at which the
      pipeline first CONSTRUCTS the setup can be hours later (pool variants appear late; see V4 evidence).
    * The touch/micro-swing/break search then replays candles from `created_at`, so a break candle that closed before the
      construction step is accepted, and the entry price is that old candle's close (`_attempt_decision`:
      entry = break_candle.close; position.opened_at = that candle's close).
    * A real system cannot place an order at a price from a candle that closed before it knew the setup existed.
    * MEASURED (frozen October run, E0 timestamps): 95 of 294 decided candidates (32%) were confirmed retroactively.
  POLICY SUB-QUESTION (needs approval, NOT decided here): what to do with such an opportunity --
    (a) discard it (the break already happened), or (b) require a NEW break after construction. Both satisfy this test.

HOW THE TEST DEFINES "FIRST SEEN": the step at which the setup first exists in pipeline state. The test injects the setup at
that step (the same mechanism every existing pipeline test uses), so it is independent of how a fix tracks construction time.
"""
import tests.test_backtest_pipeline as T
import tests.test_phase_a_correctness as P

ENTRY_STEP = T.ENTRY_STEP        # candle index of the break (c5); entry would be its close
INJECT_AT = ENTRY_STEP + 2       # the setup first appears two steps AFTER the break candle closed


def _run_with_late_injection():
    p = P._setup_pipeline(min_rr="1")
    results = []
    first_seen = None
    for i in range(len(T.CANDLES)):
        window = T.CANDLES[: i + 1]
        if i == INJECT_AT:
            p.state.active_setups["s1"] = T._make_setup()      # setup first becomes known to the pipeline NOW
            first_seen = window[-1].timestamp_close
        if i < INJECT_AT:
            p.state.active_setups.pop("s1", None)
        results.append(p.step(window[-1].timestamp_close, window))
    return p, results, first_seen


class TestConfirmationCannotPredateSetupEligibility:
    def test_break_candle_must_close_at_or_after_the_step_the_setup_first_existed(self):
        p, results, first_seen = _run_with_late_injection()
        s1 = p.state.active_setups["s1"]
        if s1.touch_events:
            ended = s1.touch_events[-1].ended_at
            assert ended >= first_seen, f"confirmed by a break candle closed at {ended}, before the setup existed ({first_seen})"

    def test_no_position_may_open_at_a_price_from_before_the_setup_existed(self):
        p, results, first_seen = _run_with_late_injection()
        opened = [r for r in results if p.state.open_position is not None or r.trade is not None]
        for t in p.state.completed_trades + ([p.state.open_position] if p.state.open_position else []):
            opened_at = getattr(t, "entry_timestamp", None) or getattr(t, "opened_at", None)
            assert opened_at >= first_seen, f"position entered at {opened_at}, earlier than first-seen {first_seen}"

    def test_the_decision_event_is_not_dated_after_the_candle_it_trades(self):
        p, results, first_seen = _run_with_late_injection()
        for r in results:
            for e in r.events:
                if e.event_type.value == "SETUP_CONFIRMED":
                    s = p.state.active_setups["s1"]
                    assert s.touch_events[-1].ended_at == e.timestamp, "confirmation must be decided in the step of its own break candle"


class TestControls:
    """PASS today: when the setup exists before the break, confirmation at the break step is correct."""

    def test_control_setup_present_from_the_start_confirms_at_the_break_step(self):
        p = P._setup_pipeline(min_rr="1")
        T._run_through(p, T.CANDLES, ENTRY_STEP + 1)
        s1 = p.state.active_setups["s1"]
        assert s1.touch_events and s1.touch_events[-1].ended_at == T.CANDLES[ENTRY_STEP].timestamp_close
        assert p.state.open_position is not None

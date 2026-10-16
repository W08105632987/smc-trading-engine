"""VALIDITY DEFECT V3 -- confirmed setups are decided at STALE prices, or retried indefinitely.

STATUS: asserts the DESIRED behaviour; EXPECTED TO FAIL on Phase-A-v2/E0.

CLASSIFICATION
  PROVEN CORRECTNESS DEFECT (the stale price): `_attempt_decision` (backtest/pipeline.py ~907-1060) uses
    entry = break_candle.close and decision_timestamp = break candle close, but may run in a LATER step when it returned
    early the first time (targets unavailable -> `return setup, events  # retried next step`; wrong-side stop ->
    silent return) or while `_attempt_decisions` is parked behind an open position (`break  # one-open-position rule`).
    CONFIRMED setups are exempt from expiry/invalidation, so they can wait forever.
    MEASURED (frozen October run): 48 of 294 decisions later than their confirmation step (max 109 h); 5 setups stayed
    CONFIRMED with no decision. In October no position was ever open, so the open-position path was not exercised.

  POLICY QUESTIONS (not decided by these tests -- each needs approval):
    1. Missing targets at the break step: terminal reject now (proposed) vs. wait. Waiting is only acceptable if the
       order is re-priced at the CURRENT step; the tests below accept either (no trade at a past price).
    2. ONE-POSITION RULE. The rule itself (never two positions) is NOT touched. What is proven wrong is DEFERRAL at the old
       price. Allowed outcomes for a setup confirmed while a position is open: (A) terminal "skipped, one-position rule"
       at the break step [proposed minimal]; (B) re-evaluate with CURRENT data at close (new policy); (C) queue with
       expiry and re-pricing (new policy). These tests accept A, B or C -- they only forbid a fill at a past price.
    NOTE: tests/test_backtest_pipeline.py::TestOneOpenPositionRule currently asserts the parked setup REMAINS `CONFIRMED`.
    That assertion encodes parking; it must be revisited when V3 is approved (the 'no second trade while one is open'
    part of it stays).
"""
from decimal import Decimal as D

import tests.test_backtest_pipeline as T
import tests.test_phase_a_correctness as P
from core.domain.setups import SetupState, TouchEvent, TouchOutcome

ALL = len(T.CANDLES)
ENTRY_STEP = T.ENTRY_STEP
# three flat candles AFTER the first position closes, so a parked setup gets its turn to be decided
CANDLES_EXT = T.CANDLES + [T._c(ALL - 3 + j, 2412, 2408, 2410) for j in range(1, 4)]


def _events(results, etype=None):
    return [e for r in results for e in r.events if etype is None or e.event_type.value == etype]


def _pipeline_with_delayed_targets(unavailable_calls):
    p = P._setup_pipeline(min_rr="1")
    calls = {"n": 0}

    def provider(setup, entry, stop):
        calls["n"] += 1
        return None if calls["n"] <= unavailable_calls else (D("2415"), D("2500"))

    p._tp_provider = provider
    return p


class TestDecisionIsMadeAtTheBreakStepOrRePriced:
    def test_targets_missing_at_the_break_must_not_produce_a_later_trade_at_the_old_price(self):
        p = _pipeline_with_delayed_targets(unavailable_calls=3)
        results = T._run_through(p, T.CANDLES, ALL)
        confirmed = _events(results, "SETUP_CONFIRMED")
        assert confirmed, "fixture must confirm"
        break_close = confirmed[0].timestamp
        for t in p.state.completed_trades + ([p.state.open_position] if p.state.open_position else []):
            opened_at = getattr(t, "entry_timestamp", None) or getattr(t, "opened_at", None)
            decided_in_step = next(e.timestamp for e in _events(results, "TRADE_APPROVED"))
            assert opened_at == decided_in_step, (
                f"position entered at {opened_at} (price of that old candle) but decided in step {decided_in_step}")

    def test_approval_must_occur_in_the_confirmation_step(self):
        p = _pipeline_with_delayed_targets(unavailable_calls=3)
        results = T._run_through(p, T.CANDLES, ALL)
        conf = _events(results, "SETUP_CONFIRMED")[0].timestamp
        for e in _events(results, "TRADE_APPROVED"):
            assert e.timestamp == conf, f"approved {e.timestamp}, confirmed {conf}"

    def test_a_confirmed_setup_without_targets_must_reach_a_terminal_state_not_linger(self):
        p = P._setup_pipeline(min_rr="1")
        p._tp_provider = lambda setup, entry, stop: None          # targets NEVER available
        T._run_through(p, T.CANDLES, ALL)
        assert p.state.active_setups["s1"].state != SetupState.CONFIRMED, "setup stuck CONFIRMED with no decision"


class TestOpenPositionPathNeverFillsAtAPastPrice:
    def _parked_second_setup(self):
        p = P._setup_pipeline(min_rr="1")
        fake_touch = TouchEvent(
            touch_id="touch:s2:1", setup_id="s2", started_at=T.CANDLES[0].timestamp_close,
            ended_at=T.CANDLES[ENTRY_STEP].timestamp_close, reaction_leg_start_candle_id="c-3",
            micro_swings=(), selected_micro_swing_id=None, break_candle_id=f"c{ENTRY_STEP - 3}",
            outcome=TouchOutcome.CONFIRMED, reason_code="CONFIRMED")
        s2 = T._make_setup("s2").model_copy(update={"state": SetupState.CONFIRMED, "touch_events": (fake_touch,),
                                                     "confirmation_event_id": "touch:s2:1"})
        p.state.active_setups["s2"] = s2
        return p

    def test_a_setup_confirmed_while_a_position_is_open_never_opens_later_at_its_old_break_price(self):
        p = self._parked_second_setup()
        opened = []                                  # (step time, position opened_at) for every position that opens
        for i in range(len(CANDLES_EXT)):
            window = CANDLES_EXT[: i + 1]
            before = p.state.open_position
            p.step(window[-1].timestamp_close, window)
            after = p.state.open_position
            if after is not None and (before is None or after.position_id != before.position_id):
                opened.append((window[-1].timestamp_close, after.opened_at, after.decision_id))
        assert len(opened) >= 1, "fixture must open at least one position"
        for step_time, opened_at, decision_id in opened:
            assert opened_at == step_time, f"{decision_id}: opened in step {step_time} at the price of candle closed {opened_at}"

    def test_fixture_really_exercises_the_deferred_path(self):
        """Guard against a vacuous test: the parked setup IS decided after the first position closed (PASSES today;
        after V3 it must be terminal-skipped or re-priced, so this guard is rewritten at that time)."""
        p = self._parked_second_setup()
        for i in range(len(CANDLES_EXT)):
            window = CANDLES_EXT[: i + 1]
            p.step(window[-1].timestamp_close, window)
        assert p.state.active_setups["s2"].state != SetupState.CONFIRMED

    def test_the_one_position_rule_itself_is_unchanged(self):
        """Guard for the fix: never two simultaneous positions (PASSES today and must keep passing)."""
        p = self._parked_second_setup()
        max_open = 0
        for i in range(ALL):
            window = T.CANDLES[: i + 1]
            p.step(window[-1].timestamp_close, window)
            max_open = max(max_open, 1 if p.state.open_position is not None else 0)
        assert max_open == 1


class TestControls:
    """PASS today."""

    def test_control_targets_available_at_the_break_trade_in_the_same_step(self):
        p = P._setup_pipeline(min_rr="1")
        results = T._run_through(p, T.CANDLES, ALL)
        conf = _events(results, "SETUP_CONFIRMED")[0].timestamp
        assert [e.timestamp for e in _events(results, "TRADE_APPROVED")] == [conf]

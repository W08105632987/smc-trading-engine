from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from pydantic import ValidationError

from core.domain.common import Direction
from core.domain.setups import (
    HTFContextSnapshot,
    MicroSwing,
    MicroSwingQualification,
    Setup,
    SetupState,
    TERMINAL_SETUP_STATES,
    TouchEvent,
    TouchOutcome,
    validate_setup_transition,
)


def _dt(minute: int) -> datetime:
    # timedelta-based so any minute offset (not just 0-59) is valid.
    return datetime(2026, 1, 5, 0, 0, tzinfo=timezone.utc) + timedelta(minutes=minute)


def make_micro_swing(touch_id: str, **overrides) -> MicroSwing:
    defaults = dict(
        micro_swing_id="ms1", touch_id=touch_id, direction=Direction.BULLISH,
        pivot_candle_id="c1", pivot_price=Decimal("2400"),
        confirmation_candle_id="c2", confirmed_at=_dt(5),
        retracement_atr=Decimal("0.15"),
        qualification_status=MicroSwingQualification.QUALIFIED,
    )
    defaults.update(overrides)
    return MicroSwing(**defaults)


class TestMicroSwing:
    def test_valid(self):
        ms = make_micro_swing("t1")
        assert ms.touch_id == "t1"

    def test_negative_retracement_rejected(self):
        with pytest.raises(ValidationError):
            make_micro_swing("t1", retracement_atr=Decimal("-0.1"))


class TestTouchEventCrossTouchInvariant:
    """v4 §A / v5 I1: 'a micro-swing from touch N can never be used by
    touch N+1' — this MUST be enforced at the schema level, not just by
    convention (setups.py docstring)."""

    def test_micro_swing_belonging_to_this_touch_is_accepted(self):
        ms = make_micro_swing("t1")
        touch = TouchEvent(
            touch_id="t1", setup_id="s1", started_at=_dt(0),
            reaction_leg_start_candle_id="c0", micro_swings=(ms,),
            outcome=TouchOutcome.ACTIVE,
        )
        assert touch.touch_id == "t1"

    def test_micro_swing_from_another_touch_is_rejected(self):
        # The critical guard: ms.touch_id="t1" but the TouchEvent itself
        # is "t2" — this must be impossible to construct.
        ms = make_micro_swing("t1")
        with pytest.raises(ValidationError, match="can never be reused"):
            TouchEvent(
                touch_id="t2", setup_id="s1", started_at=_dt(0),
                reaction_leg_start_candle_id="c0", micro_swings=(ms,),
                outcome=TouchOutcome.ACTIVE,
            )

    def test_selected_micro_swing_must_be_one_of_this_touchs_own(self):
        ms = make_micro_swing("t1", micro_swing_id="ms-real")
        with pytest.raises(ValidationError):
            TouchEvent(
                touch_id="t1", setup_id="s1", started_at=_dt(0),
                reaction_leg_start_candle_id="c0", micro_swings=(ms,),
                selected_micro_swing_id="ms-does-not-exist",
                outcome=TouchOutcome.ACTIVE,
            )

    def test_non_active_outcome_requires_ended_at(self):
        with pytest.raises(ValidationError):
            TouchEvent(
                touch_id="t1", setup_id="s1", started_at=_dt(0),
                reaction_leg_start_candle_id="c0",
                outcome=TouchOutcome.NO_BREAK,
            )

    def test_confirmed_outcome_with_ended_at_ok(self):
        ms = make_micro_swing("t1")
        touch = TouchEvent(
            touch_id="t1", setup_id="s1", started_at=_dt(0), ended_at=_dt(10),
            reaction_leg_start_candle_id="c0", micro_swings=(ms,),
            selected_micro_swing_id=ms.micro_swing_id,
            break_candle_id="c9", outcome=TouchOutcome.CONFIRMED,
        )
        assert touch.outcome == TouchOutcome.CONFIRMED


def make_setup(**overrides) -> Setup:
    defaults = dict(
        setup_id="s1", originating_zone_id="z1", originating_candle_id="c1",
        direction=Direction.BULLISH, created_at=_dt(0),
        state=SetupState.WAITING_FOR_RETEST, score=Decimal("74"),
        score_snapshot={"d1_h4_alignment": Decimal("15")},
        htf_context=HTFContextSnapshot(d1_state="BULLISH", h4_state="BULLISH", h1_state="BULLISH"),
        expiry_at=_dt(100), invalidation_level=Decimal("2390"),
    )
    defaults.update(overrides)
    return Setup(**defaults)


class TestSetup:
    def test_valid_setup(self):
        s = make_setup()
        assert s.state == SetupState.WAITING_FOR_RETEST

    def test_expiry_before_created_rejected(self):
        with pytest.raises(ValidationError):
            make_setup(expiry_at=_dt(0), created_at=_dt(10))

    def test_displaced_requires_displaced_by(self):
        with pytest.raises(ValidationError):
            make_setup(state=SetupState.DISPLACED)

    def test_displaced_with_field_ok(self):
        s = make_setup(state=SetupState.DISPLACED, displaced_by_setup_id="s2")
        assert s.displaced_by_setup_id == "s2"

    def test_displaced_by_outside_displaced_state_rejected(self):
        with pytest.raises(ValidationError):
            make_setup(displaced_by_setup_id="s2")  # state stays WAITING_FOR_RETEST

    def test_trade_approved_requires_decision_id(self):
        with pytest.raises(ValidationError):
            make_setup(state=SetupState.TRADE_APPROVED)

    def test_touch_event_setup_id_mismatch_rejected(self):
        touch = TouchEvent(
            touch_id="t1", setup_id="OTHER-SETUP", started_at=_dt(0),
            reaction_leg_start_candle_id="c0", outcome=TouchOutcome.ACTIVE,
        )
        with pytest.raises(ValidationError):
            make_setup(touch_events=(touch,))


class TestSetupTransitionTable:
    """Implementation Spec v1.0 §5 / v5's 7-state model, with CONFIRMED
    exempt from displacement and DISPLACED as its own terminal state."""

    def test_all_terminal_states_have_no_outgoing_transition(self):
        for state in TERMINAL_SETUP_STATES:
            with pytest.raises(ValueError):
                validate_setup_transition(state, SetupState.WAITING_FOR_RETEST)

    def test_waiting_for_retest_to_confirmation_legal(self):
        validate_setup_transition(
            SetupState.WAITING_FOR_RETEST, SetupState.WAITING_FOR_CONFIRMATION
        )

    def test_waiting_for_retest_to_trade_approved_illegal(self):
        # Must go through WAITING_FOR_CONFIRMATION -> CONFIRMED first.
        with pytest.raises(ValueError):
            validate_setup_transition(SetupState.WAITING_FOR_RETEST, SetupState.TRADE_APPROVED)

    def test_confirmed_can_go_to_trade_approved_or_rejected_only(self):
        validate_setup_transition(SetupState.CONFIRMED, SetupState.TRADE_APPROVED)
        validate_setup_transition(SetupState.CONFIRMED, SetupState.REJECTED)
        with pytest.raises(ValueError):
            validate_setup_transition(SetupState.CONFIRMED, SetupState.EXPIRED)

    def test_confirmed_is_not_directly_displaceable(self):
        # v5 final freeze: CONFIRMED is exempt from pending-cap displacement.
        with pytest.raises(ValueError):
            validate_setup_transition(SetupState.CONFIRMED, SetupState.DISPLACED)

    def test_waiting_states_can_be_displaced(self):
        validate_setup_transition(SetupState.WAITING_FOR_RETEST, SetupState.DISPLACED)
        validate_setup_transition(SetupState.WAITING_FOR_CONFIRMATION, SetupState.DISPLACED)

    def test_failed_touch_returns_to_waiting_for_retest(self):
        validate_setup_transition(
            SetupState.WAITING_FOR_CONFIRMATION, SetupState.WAITING_FOR_RETEST
        )

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from pydantic import ValidationError

from core.domain.common import Direction, Timeframe
from core.domain.structure import (
    BiasTransitionEvent,
    BiasTransitionTrigger,
    H4BiasState,
    H4BiasStateValue,
    ReversalConfirmation,
    StructureEvent,
    StructureEventType,
    SwingPoint,
    SwingType,
)


def _dt(minute: int) -> datetime:
    # timedelta-based so any minute offset (not just 0-59) is valid.
    return datetime(2026, 1, 5, 0, 0, tzinfo=timezone.utc) + timedelta(minutes=minute)


class TestSwingPoint:
    def test_confirmation_after_formation_ok(self):
        sp = SwingPoint(
            swing_id="s1", symbol="XAUUSD", timeframe=Timeframe.H4,
            type=SwingType.HIGH, price=Decimal("2400"),
            pivot_candle_id="c1", formation_timestamp=_dt(0),
            confirmation_timestamp=_dt(10),
        )
        assert sp.confirmation_timestamp > sp.formation_timestamp

    def test_confirmation_before_formation_rejected(self):
        # This would be look-ahead by construction — must be impossible.
        with pytest.raises(ValidationError):
            SwingPoint(
                swing_id="s1", symbol="XAUUSD", timeframe=Timeframe.H4,
                type=SwingType.HIGH, price=Decimal("2400"),
                pivot_candle_id="c1", formation_timestamp=_dt(10),
                confirmation_timestamp=_dt(0),
            )

    def test_major_flag_requires_major_confirmed_at(self):
        with pytest.raises(ValidationError):
            SwingPoint(
                swing_id="s1", symbol="XAUUSD", timeframe=Timeframe.H4,
                type=SwingType.HIGH, price=Decimal("2400"),
                pivot_candle_id="c1", formation_timestamp=_dt(0),
                confirmation_timestamp=_dt(10), major_flag=True,
            )

    def test_major_flag_with_confirmation_ok(self):
        sp = SwingPoint(
            swing_id="s1", symbol="XAUUSD", timeframe=Timeframe.H4,
            type=SwingType.HIGH, price=Decimal("2400"),
            pivot_candle_id="c1", formation_timestamp=_dt(0),
            confirmation_timestamp=_dt(10), major_flag=True,
            major_confirmed_at=_dt(20),
        )
        assert sp.major_flag is True


class TestH4BiasStateTransitions:
    """v4 §C: no direct BULLISH<->BEARISH without WARNING; no direct
    *_WARNING<->*_WARNING without resolving first."""

    def test_bullish_to_bearish_warning_is_legal(self):
        BiasTransitionEvent(
            transition_id="t1", from_state=H4BiasStateValue.BULLISH,
            to_state=H4BiasStateValue.BEARISH_WARNING,
            trigger_type=BiasTransitionTrigger.CHOCH,
            triggering_structural_event_id="e1", timestamp=_dt(0),
        )

    def test_bullish_to_bearish_direct_is_illegal(self):
        with pytest.raises(ValidationError):
            BiasTransitionEvent(
                transition_id="t1", from_state=H4BiasStateValue.BULLISH,
                to_state=H4BiasStateValue.BEARISH,
                trigger_type=BiasTransitionTrigger.CHOCH,
                triggering_structural_event_id="e1", timestamp=_dt(0),
            )

    def test_warning_to_warning_direct_is_illegal(self):
        with pytest.raises(ValidationError):
            BiasTransitionEvent(
                transition_id="t1", from_state=H4BiasStateValue.BULLISH_WARNING,
                to_state=H4BiasStateValue.BEARISH_WARNING,
                trigger_type=BiasTransitionTrigger.CHOCH,
                triggering_structural_event_id="e1", timestamp=_dt(0),
            )

    def test_warning_expiry_restore_is_legal(self):
        BiasTransitionEvent(
            transition_id="t1", from_state=H4BiasStateValue.BEARISH_WARNING,
            to_state=H4BiasStateValue.BULLISH,
            trigger_type=BiasTransitionTrigger.WARNING_EXPIRY_RESTORE,
            triggering_structural_event_id="e1", timestamp=_dt(0),
        )

    def test_warning_cancelled_is_legal(self):
        # v5 I3 — the newly-introduced early-exit mechanism.
        BiasTransitionEvent(
            transition_id="t1", from_state=H4BiasStateValue.BEARISH_WARNING,
            to_state=H4BiasStateValue.BULLISH,
            trigger_type=BiasTransitionTrigger.WARNING_CANCELLED,
            triggering_structural_event_id="e1", timestamp=_dt(0),
        )


class TestH4BiasState:
    def test_warning_state_requires_warning_fields(self):
        with pytest.raises(ValidationError):
            H4BiasState(
                state_id="h4-XAUUSD", symbol="XAUUSD",
                current_state=H4BiasStateValue.BEARISH_WARNING,
                entered_at=_dt(0), triggering_event_id="e1",
            )

    def test_resting_state_forbids_warning_fields(self):
        with pytest.raises(ValidationError):
            H4BiasState(
                state_id="h4-XAUUSD", symbol="XAUUSD",
                current_state=H4BiasStateValue.BULLISH,
                entered_at=_dt(0), triggering_event_id="e1",
                warning_started_at=_dt(0),
            )

    def test_valid_warning_state(self):
        state = H4BiasState(
            state_id="h4-XAUUSD", symbol="XAUUSD",
            current_state=H4BiasStateValue.BEARISH_WARNING,
            entered_at=_dt(0), triggering_event_id="e1",
            warning_started_at=_dt(0), warning_expires_at=_dt(100),
        )
        assert state.current_state == H4BiasStateValue.BEARISH_WARNING


class TestStructureEvent:
    def test_valid_bos(self):
        ev = StructureEvent(
            event_id="e1", type=StructureEventType.BOS, direction=Direction.BULLISH,
            broken_swing_id="s1", confirming_candle_id="c1",
            confirmed_at=_dt(0), timeframe=Timeframe.H4,
        )
        assert ev.type == StructureEventType.BOS


class TestReversalConfirmation:
    def test_valid(self):
        rc = ReversalConfirmation(
            confirmation_id="rc1", direction=Direction.BEARISH,
            confirming_swing_id="s2", confirmed_at=_dt(0),
            warning_state_id="h4-XAUUSD",
        )
        assert rc.direction == Direction.BEARISH

from datetime import datetime, timedelta, timezone
from decimal import Decimal

from core.domain.common import Direction
from core.domain.structure import (
    BiasTransitionTrigger,
    H4BiasState,
    H4BiasStateValue,
    ReversalConfirmation,
    StructureEvent,
    StructureEventType,
    Timeframe,
)
from strategy.structure.h4_warning_engine import H4WarningEngineImpl


def _dt(minute: int) -> datetime:
    return datetime(2026, 1, 5, 0, 0, tzinfo=timezone.utc) + timedelta(minutes=minute)


def make_bullish_state() -> H4BiasState:
    return H4BiasState(
        state_id="h4-XAUUSD", symbol="XAUUSD", current_state=H4BiasStateValue.BULLISH,
        entered_at=_dt(0), triggering_event_id="genesis",
    )


def make_bearish_warning_state(started_at_min: int = 0, expires_at_min: int = 100) -> H4BiasState:
    return H4BiasState(
        state_id="h4-XAUUSD", symbol="XAUUSD", current_state=H4BiasStateValue.BEARISH_WARNING,
        entered_at=_dt(started_at_min), warning_started_at=_dt(started_at_min),
        warning_expires_at=_dt(expires_at_min), triggering_event_id="choch-1",
    )


def make_choch(direction: Direction, confirmed_at_min: int) -> StructureEvent:
    return StructureEvent(
        event_id=f"choch-{confirmed_at_min}", type=StructureEventType.CHOCH, direction=direction,
        broken_swing_id="swing-1", confirming_candle_id="c1",
        confirmed_at=_dt(confirmed_at_min), timeframe=Timeframe.H4,
    )


def make_bos(direction: Direction, confirmed_at_min: int) -> StructureEvent:
    return StructureEvent(
        event_id=f"bos-{confirmed_at_min}", type=StructureEventType.BOS, direction=direction,
        broken_swing_id="swing-2", confirming_candle_id="c2",
        confirmed_at=_dt(confirmed_at_min), timeframe=Timeframe.H4,
    )


def make_reversal_confirmation(direction: Direction, confirmed_at_min: int) -> ReversalConfirmation:
    return ReversalConfirmation(
        confirmation_id=f"rc-{confirmed_at_min}", direction=direction,
        confirming_swing_id="swing-3", confirmed_at=_dt(confirmed_at_min),
        warning_state_id="h4-XAUUSD",
    )


ENGINE = H4WarningEngineImpl(choch_warning_max_h4_candles=5, h4_candle_duration=timedelta(hours=4))


class TestRestingToWarning:
    def test_bullish_plus_bearish_choch_enters_bearish_warning(self):
        state = make_bullish_state()
        choch = make_choch(Direction.BEARISH, confirmed_at_min=10)

        new_state, transition = ENGINE.evaluate(
            state, new_choch=choch, reversal_confirmation=None, reconfirming_bos=None, at=_dt(10)
        )

        assert new_state.current_state == H4BiasStateValue.BEARISH_WARNING
        assert transition.trigger_type == BiasTransitionTrigger.CHOCH
        assert new_state.warning_started_at == _dt(10)

    def test_no_choch_leaves_state_unchanged(self):
        state = make_bullish_state()
        new_state, transition = ENGINE.evaluate(
            state, new_choch=None, reversal_confirmation=None, reconfirming_bos=None, at=_dt(10)
        )
        assert new_state == state
        assert transition is None

    def test_bos_event_passed_as_new_choch_argument_is_ignored(self):
        # Only CHOCH-typed events can start a warning — a BOS shouldn't
        # be misinterpreted even if accidentally passed in.
        state = make_bullish_state()
        not_a_choch = make_bos(Direction.BEARISH, confirmed_at_min=10)
        new_state, transition = ENGINE.evaluate(
            state, new_choch=not_a_choch, reversal_confirmation=None,
            reconfirming_bos=None, at=_dt(10),
        )
        assert new_state == state
        assert transition is None


class TestWarningToReversalConfirmed:
    def test_reversal_confirmation_flips_to_bearish(self):
        state = make_bearish_warning_state()
        rc = make_reversal_confirmation(Direction.BEARISH, confirmed_at_min=20)

        new_state, transition = ENGINE.evaluate(
            state, new_choch=None, reversal_confirmation=rc, reconfirming_bos=None, at=_dt(20)
        )

        assert new_state.current_state == H4BiasStateValue.BEARISH
        assert new_state.warning_started_at is None  # resting state has no warning fields
        assert transition.trigger_type == BiasTransitionTrigger.REVERSAL_CONFIRMATION


class TestWarningCancelled:
    def test_reconfirming_bos_in_original_direction_restores_bullish(self):
        # v5 I3: reconfirming the ORIGINAL (bullish) direction during a
        # BEARISH_WARNING cancels the warning early, rather than waiting
        # out the full timeout.
        state = make_bearish_warning_state(started_at_min=0, expires_at_min=100)
        reconfirming = make_bos(Direction.BULLISH, confirmed_at_min=15)

        new_state, transition = ENGINE.evaluate(
            state, new_choch=None, reversal_confirmation=None,
            reconfirming_bos=reconfirming, at=_dt(15),
        )

        assert new_state.current_state == H4BiasStateValue.BULLISH
        assert transition.trigger_type == BiasTransitionTrigger.WARNING_CANCELLED

    def test_reversal_confirmation_takes_priority_over_reconfirming_bos(self):
        # If both somehow arrive together, reversal confirmation wins —
        # it's the more definitive event.
        state = make_bearish_warning_state()
        rc = make_reversal_confirmation(Direction.BEARISH, confirmed_at_min=20)
        reconfirming = make_bos(Direction.BULLISH, confirmed_at_min=20)

        new_state, transition = ENGINE.evaluate(
            state, new_choch=None, reversal_confirmation=rc,
            reconfirming_bos=reconfirming, at=_dt(20),
        )

        assert new_state.current_state == H4BiasStateValue.BEARISH
        assert transition.trigger_type == BiasTransitionTrigger.REVERSAL_CONFIRMATION


class TestWarningExpiry:
    def test_expiry_without_resolution_restores_bullish(self):
        state = make_bearish_warning_state(started_at_min=0, expires_at_min=100)
        new_state, transition = ENGINE.evaluate(
            state, new_choch=None, reversal_confirmation=None,
            reconfirming_bos=None, at=_dt(100),
        )
        assert new_state.current_state == H4BiasStateValue.BULLISH
        assert transition.trigger_type == BiasTransitionTrigger.WARNING_EXPIRY_RESTORE

    def test_before_expiry_with_no_resolution_stays_in_warning(self):
        state = make_bearish_warning_state(started_at_min=0, expires_at_min=100)
        new_state, transition = ENGINE.evaluate(
            state, new_choch=None, reversal_confirmation=None,
            reconfirming_bos=None, at=_dt(50),
        )
        assert new_state.current_state == H4BiasStateValue.BEARISH_WARNING
        assert transition is None

    def test_exactly_at_expiry_boundary_restores(self):
        state = make_bearish_warning_state(started_at_min=0, expires_at_min=100)
        new_state, transition = ENGINE.evaluate(
            state, new_choch=None, reversal_confirmation=None,
            reconfirming_bos=None, at=_dt(100),
        )
        assert new_state.current_state == H4BiasStateValue.BULLISH


class TestResultingTransitionsAreAlwaysSchemaLegal:
    """Every transition this engine ever produces must be constructible
    as a BiasTransitionEvent — which independently validates against
    VALID_H4_BIAS_TRANSITIONS (core/domain/structure.py). If the engine
    ever tried to produce an illegal transition, BiasTransitionEvent's
    own validator would reject it — these tests confirm that's never
    exercised by tripping ValidationError, i.e. the engine's outputs are
    always legal by construction."""

    def test_all_four_scenarios_produce_legal_transitions(self):
        scenarios = [
            (make_bullish_state(), make_choch(Direction.BEARISH, 10), None, None, _dt(10)),
            (make_bearish_warning_state(), None, make_reversal_confirmation(Direction.BEARISH, 20), None, _dt(20)),
            (make_bearish_warning_state(), None, None, make_bos(Direction.BULLISH, 15), _dt(15)),
            (make_bearish_warning_state(0, 100), None, None, None, _dt(100)),
        ]
        for state, choch, rc, bos, at in scenarios:
            new_state, transition = ENGINE.evaluate(
                state, new_choch=choch, reversal_confirmation=rc, reconfirming_bos=bos, at=at
            )
            assert new_state is not None  # constructed without raising

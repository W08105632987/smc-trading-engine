"""H4 WARNING engine. Applies the transitions LOCKED in
core.domain.structure.VALID_H4_BIAS_TRANSITIONS — this module contains
zero new trading logic; it is purely the mechanism that turns incoming
CHoCH/BOS/ReversalConfirmation events into the correct next H4BiasState +
BiasTransitionEvent, per Strategy FROZEN v1.0 / v4 §C / v5 I3.

Four rules encoded here, all traceable to a specific frozen decision:
  1. BULLISH/BEARISH + opposite-direction CHoCH -> *_WARNING (v4 §C)
  2. *_WARNING + ReversalConfirmation (new direction) -> that direction,
     CONFIRMED (Strategy FROZEN v1.0 §B / v5 I2)
  3. *_WARNING + a BOS reconfirming the ORIGINAL direction ->
     WARNING_CANCELLED, restore original immediately (v5 I3 — this was
     explicitly flagged as a genuinely new mechanism when introduced,
     not an obvious inference)
  4. *_WARNING + expiry (CHoCH_WARNING_MAX_CANDLES elapsed, no
     resolution) -> WARNING_EXPIRY_RESTORE, restore original (v4 §C)
"""

from __future__ import annotations

from datetime import datetime, timedelta

from core.domain.structure import (
    BiasTransitionEvent,
    BiasTransitionTrigger,
    H4BiasState,
    H4BiasStateValue,
    ReversalConfirmation,
    StructureEvent,
    StructureEventType,
)


class H4WarningEngineImpl:
    """Concrete implementation of core.contracts.detectors.H4WarningEngine.

    Milestone 2 review: the Protocol was corrected (not this
    implementation) — see core/contracts/detectors.py's H4WarningEngine
    docstring for why the original draft signature could never have been
    satisfied by any correct implementation. This class now implements
    the Protocol exactly as declared; no divergence remains."""

    def __init__(self, choch_warning_max_h4_candles: int, h4_candle_duration: timedelta) -> None:
        self._max_candles = choch_warning_max_h4_candles
        self._h4_duration = h4_candle_duration

    def evaluate(
        self,
        current_state: H4BiasState,
        new_choch: StructureEvent | None,
        reversal_confirmation: ReversalConfirmation | None,
        reconfirming_bos: StructureEvent | None,
        at: datetime,
    ) -> tuple[H4BiasState, BiasTransitionEvent | None]:
        """Implements core.contracts.detectors.H4WarningEngine.evaluate()
        exactly as declared — no signature divergence."""

        if current_state.current_state in (H4BiasStateValue.BULLISH, H4BiasStateValue.BEARISH):
            return self._evaluate_resting(current_state, new_choch)
        return self._evaluate_warning(
            current_state, reversal_confirmation, reconfirming_bos, at
        )

    def _evaluate_resting(
        self, current_state: H4BiasState, new_choch: StructureEvent | None
    ) -> tuple[H4BiasState, BiasTransitionEvent | None]:
        if new_choch is None or new_choch.type != StructureEventType.CHOCH:
            return current_state, None

        # A CHoCH is only relevant if it's counter to the current resting bias.
        expected_warning = (
            H4BiasStateValue.BEARISH_WARNING
            if current_state.current_state == H4BiasStateValue.BULLISH
            else H4BiasStateValue.BULLISH_WARNING
        )

        new_state = H4BiasState(
            state_id=current_state.state_id,
            symbol=current_state.symbol,
            current_state=expected_warning,
            entered_at=new_choch.confirmed_at,
            warning_started_at=new_choch.confirmed_at,
            warning_expires_at=new_choch.confirmed_at + self._max_candles * self._h4_duration,
            triggering_event_id=new_choch.event_id,
        )
        transition = BiasTransitionEvent(
            transition_id=f"transition:{new_choch.event_id}",
            from_state=current_state.current_state,
            to_state=expected_warning,
            trigger_type=BiasTransitionTrigger.CHOCH,
            triggering_structural_event_id=new_choch.event_id,
            timestamp=new_choch.confirmed_at,
        )
        return new_state, transition

    def _evaluate_warning(
        self,
        current_state: H4BiasState,
        reversal_confirmation: ReversalConfirmation | None,
        reconfirming_bos: StructureEvent | None,
        at: datetime,
    ) -> tuple[H4BiasState, BiasTransitionEvent | None]:
        original_direction = (
            H4BiasStateValue.BULLISH
            if current_state.current_state == H4BiasStateValue.BEARISH_WARNING
            else H4BiasStateValue.BEARISH
        )
        new_direction = (
            H4BiasStateValue.BEARISH
            if current_state.current_state == H4BiasStateValue.BEARISH_WARNING
            else H4BiasStateValue.BULLISH
        )

        # Priority 1: reversal confirmed -> new direction, CONFIRMED.
        if reversal_confirmation is not None:
            return self._resolve(
                current_state, new_direction,
                BiasTransitionTrigger.REVERSAL_CONFIRMATION,
                reversal_confirmation.confirmation_id,
                reversal_confirmation.confirmed_at,
            )

        # Priority 2: original direction reconfirmed -> WARNING_CANCELLED (v5 I3).
        if reconfirming_bos is not None and reconfirming_bos.type == StructureEventType.BOS:
            return self._resolve(
                current_state, original_direction,
                BiasTransitionTrigger.WARNING_CANCELLED,
                reconfirming_bos.event_id,
                reconfirming_bos.confirmed_at,
            )

        # Priority 3: expiry, unresolved -> restore original.
        if current_state.warning_expires_at is not None and at >= current_state.warning_expires_at:
            return self._resolve(
                current_state, original_direction,
                BiasTransitionTrigger.WARNING_EXPIRY_RESTORE,
                current_state.triggering_event_id,
                current_state.warning_expires_at,
            )

        return current_state, None

    @staticmethod
    def _resolve(
        current_state: H4BiasState,
        to_direction: H4BiasStateValue,
        trigger: BiasTransitionTrigger,
        triggering_event_id: str,
        at: datetime,
    ) -> tuple[H4BiasState, BiasTransitionEvent]:
        new_state = H4BiasState(
            state_id=current_state.state_id,
            symbol=current_state.symbol,
            current_state=to_direction,
            entered_at=at,
            triggering_event_id=triggering_event_id,
        )
        transition = BiasTransitionEvent(
            transition_id=f"transition:{triggering_event_id}:{trigger.value}",
            from_state=current_state.current_state,
            to_state=to_direction,
            trigger_type=trigger,
            triggering_structural_event_id=triggering_event_id,
            timestamp=at,
        )
        return new_state, transition

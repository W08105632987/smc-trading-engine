"""M5 confirmation engine. v4 §A / v5 I1, the locked algorithm:

    ZONE_ENTRY -> REACTION_LEG -> PULLBACK -> MICRO-SWING -> BREAK -> M5_CONFIRMATION

IMPLEMENTATION NOTE, not a strategy invention: the frozen spec defines
the micro-swing CONCEPT and its qualification THRESHOLD (retracement >=
micro_swing_min_retracement_atr) precisely, but never gives an exact
formula for computing 'retracement' itself — only that it must filter
noise pivots. This module computes it as
    retracement_atr = |pivot_price - confirming_candle.close| / ATR
i.e. how far price actually pulled back from the pivot extreme by the
time the pivot's own 1-candle confirmation closes. This is a reasonable,
documented interpretation of an already-locked concept, not a new rule —
flagging it here so it's visible rather than buried in code.

Direction convention (from the original discovery document's own
example): a BULLISH setup looks for a HIGH-type pivot (a 'lower high'
during the pullback into the zone) and confirms when price closes ABOVE
it. A BEARISH setup is the mirror — a LOW-type pivot, confirmed by a
close BELOW it.
"""

from __future__ import annotations

import bisect
from datetime import datetime
from decimal import Decimal
from typing import Optional, Sequence

from core.domain.common import Direction
from core.domain.market_data import Candle
from core.domain.setups import MicroSwing, MicroSwingQualification, TouchEvent, TouchOutcome
from core.domain.zones import Zone


class ConfirmationEngineImpl:
    """Concrete implementation of the M5 confirmation engine — no
    corresponding core/contracts Protocol exists yet (Milestone 4 is
    the first module to need one); left for the Milestone 4 review gate
    to formalize, same pattern as the H4WarningEngine Protocol fix in
    Milestone 2's review."""

    def __init__(self, min_retracement_atr_mult: Decimal) -> None:
        self._min_retracement = min_retracement_atr_mult

    def process_touch(
        self,
        touch_id: str,
        setup_id: str,
        zone: Zone,
        direction: Direction,
        candles: Sequence[Candle],
        atr_by_candle_id: dict[str, Decimal],
        available_at: Optional[datetime] = None,
    ) -> TouchEvent | None:
        """`candles` must be the full M5 series from which this touch's
        window is drawn — the method finds the touch start itself
        (first candle whose range overlaps the zone) and scopes
        everything after that to this one TouchEvent, per v4 §A / v5 I1.
        Returns None if no touch is found in the given candles at all
        (distinct from a touch that started but hasn't resolved yet).

        PHASE-A / P0.1 (causality): no candle that OPENS before the
        setup's availability boundary may take part in touch detection,
        micro-swing detection or the break. The boundary is
        max(zone.creation_timestamp, available_at) where `available_at`
        is the moment the strategy could first act on the setup
        (Setup.created_at = max(zone creation, MSS confirmation)). A
        candle that opened before that instant contains price action the
        strategy could not have acted on, so it is excluded even if it
        closes after it. Previously the search started at the zone's
        ORIGIN candle (c1 of the FVG), which precedes the instant the zone
        became knowable and let touch/micro-swing events be dated before
        the setup existed."""

        touch_start_index = self._find_touch_start(zone, candles, available_at)
        if touch_start_index is None:
            return None

        started_at = candles[touch_start_index].timestamp_close
        reaction_leg_start_candle_id = candles[touch_start_index].candle_id
        pivot_is_high = direction == Direction.BULLISH

        raw_pivots = self._find_pivots(
            touch_id, candles, touch_start_index, pivot_is_high, atr_by_candle_id
        )

        qualified = [p for p in raw_pivots if p.qualification_status == MicroSwingQualification.QUALIFIED]
        selected = qualified[-1] if qualified else None

        # Supersede every qualified pivot except the last (v4 §A: "most
        # recent qualifying pivot before the break takes precedence;
        # earlier ones remain logged but superseded").
        final_micro_swings = []
        for p in raw_pivots:
            if p.qualification_status == MicroSwingQualification.QUALIFIED and selected is not None and p.micro_swing_id != selected.micro_swing_id:
                p = p.model_copy(update={"qualification_status": MicroSwingQualification.SUPERSEDED})
            final_micro_swings.append(p)

        break_candle = None
        if selected is not None:
            confirming_index = self._index_of(candles, selected.confirmation_candle_id)
            # Break must occur STRICTLY after the pivot's own confirming
            # candle — never on the same candle (v4 §A: "a candle cannot
            # form and break its own pivot").
            for candle in candles[confirming_index + 1 :]:
                if self._closed_beyond(candle, selected.pivot_price, pivot_is_high):
                    break_candle = candle
                    break

        if selected is not None and break_candle is not None:
            outcome = TouchOutcome.CONFIRMED
            ended_at = break_candle.timestamp_close
            selected_id = selected.micro_swing_id
            break_candle_id = break_candle.candle_id
            reason_code = "CONFIRMED"
        else:
            # No break found within the candles given — this does NOT
            # mean the touch has concluded. Only an external event (zone
            # invalidation, setup expiry — orchestration this engine
            # doesn't own) can actually close a touch without a break.
            # Declaring NO_BREAK here would be this detector unilaterally
            # deciding something is finished when it's really just
            # "nothing yet, based on what I've seen so far."
            outcome = TouchOutcome.ACTIVE
            ended_at = None
            selected_id = None
            break_candle_id = None
            reason_code = "NO_QUALIFYING_MICRO_SWING_YET" if not raw_pivots else "AWAITING_BREAK"

        return TouchEvent(
            touch_id=touch_id, setup_id=setup_id, started_at=started_at, ended_at=ended_at,
            reaction_leg_start_candle_id=reaction_leg_start_candle_id,
            micro_swings=tuple(final_micro_swings),
            selected_micro_swing_id=selected_id, break_candle_id=break_candle_id,
            outcome=outcome, reason_code=reason_code,
        )

    @staticmethod
    def availability_boundary(zone: Zone, available_at: Optional[datetime]) -> datetime:
        """Earliest instant a candle may open and still be usable."""
        if available_at is None:
            return zone.creation_timestamp
        return max(zone.creation_timestamp, available_at)

    def _find_touch_start(
        self, zone: Zone, candles: Sequence[Candle], available_at: Optional[datetime] = None,
    ) -> int | None:
        # PHASE-A / P0.1: begin at the first candle whose OPEN is at or
        # after the availability boundary (never at the zone's origin
        # candle). `candles` is chronological, so bisect is exact.
        boundary = self.availability_boundary(zone, available_at)
        start_idx = bisect.bisect_left(candles, boundary, key=lambda c: c.timestamp_open)

        for i in range(start_idx, len(candles)):
            c = candles[i]
            if c.low <= zone.upper and c.high >= zone.lower:
                return i
        return None

    def _find_pivots(
        self,
        touch_id: str,
        candles: Sequence[Candle],
        touch_start_index: int,
        pivot_is_high: bool,
        atr_by_candle_id: dict[str, Decimal],
    ) -> list[MicroSwing]:
        pivots: list[MicroSwing] = []
        for i in range(max(touch_start_index, 1), len(candles) - 1):
            if i - 1 < touch_start_index:
                continue  # left neighbor must also be within this touch's scope
            left, pivot_candle, right = candles[i - 1], candles[i], candles[i + 1]

            if pivot_is_high:
                is_pivot = pivot_candle.high > left.high and pivot_candle.high > right.high
                pivot_price = pivot_candle.high
            else:
                is_pivot = pivot_candle.low < left.low and pivot_candle.low < right.low
                pivot_price = pivot_candle.low

            if not is_pivot:
                continue

            atr = atr_by_candle_id.get(right.candle_id)
            if atr is None:
                continue

            retracement = abs(pivot_price - right.close) / atr
            qualification = (
                MicroSwingQualification.QUALIFIED
                if retracement >= self._min_retracement
                else MicroSwingQualification.REJECTED_INSUFFICIENT_RETRACEMENT
            )

            pivots.append(
                MicroSwing(
                    micro_swing_id=f"ms:{touch_id}:{pivot_candle.candle_id}",
                    touch_id=touch_id,
                    direction=Direction.BULLISH if pivot_is_high else Direction.BEARISH,
                    pivot_candle_id=pivot_candle.candle_id, pivot_price=pivot_price,
                    confirmation_candle_id=right.candle_id, confirmed_at=right.timestamp_close,
                    retracement_atr=retracement, qualification_status=qualification,
                )
            )
        return pivots

    @staticmethod
    def _closed_beyond(candle: Candle, pivot_price: Decimal, pivot_is_high: bool) -> bool:
        return candle.close > pivot_price if pivot_is_high else candle.close < pivot_price

    @staticmethod
    def _index_of(candles: Sequence[Candle], candle_id: str) -> int:
        for i in range(len(candles) - 1, -1, -1):
            if candles[i].candle_id == candle_id:
                return i
        raise ValueError(f"candle_id {candle_id} not found in candles")

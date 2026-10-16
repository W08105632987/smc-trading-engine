"""BOS/CHoCH detector. Phase 2 §6:

    BOS: close beyond a confirmed meaningful (major) swing in the
         prevailing structural direction.
    CHoCH: close beyond a confirmed meaningful swing AGAINST the
           prevailing structure.

A wick through a level is never a BOS/CHoCH — only a candle CLOSE beyond
the level counts (enforced by construction here: we only ever check
`candle.close`, never `candle.high`/`candle.low`, against the swing
price).

This module only classifies events; it does NOT own H4 bias-state
transitions — that's h4_warning_engine.py's job (T3: WARNING belongs to
H4, so CHoCH detection at any timeframe simply reports the event, and the
H4 warning engine is what decides whether it matters for bias)."""

from __future__ import annotations

from typing import Sequence

from core.domain.common import Direction
from core.domain.market_data import Candle
from core.domain.structure import StructureEvent, StructureEventType, SwingPoint, SwingType


class StructureDetectorImpl:
    """Concrete implementation of core.contracts.detectors.StructureDetector.

    `prevailing_direction` must be supplied by the caller (it's a
    property of the timeframe's established structure — HH+HL vs LL+LH,
    Strategy FROZEN v1.0 §2 — which this detector doesn't independently
    reconstruct; that's a deliberate scope boundary, not an oversight,
    since 'established structure' depends on history this detector isn't
    given). Only MAJOR swings can be broken to produce a StructureEvent —
    minor swing breaks are noise, not structure, per the frozen spec's
    'meaningful swing' language throughout."""

    def detect(
        self,
        candles: Sequence[Candle],
        swings: Sequence[SwingPoint],
        prevailing_direction: Direction,
    ) -> Sequence[StructureEvent]:
        major_swings = [s for s in swings if s.major_flag and s.invalidated_at is None]
        events: list[StructureEvent] = []
        broken_swing_ids: set[str] = set()

        for candle in candles:
            for swing in major_swings:
                if swing.swing_id in broken_swing_ids:
                    continue
                # A swing is only usable once it's actually confirmed —
                # never react to a candle that closes before the swing's
                # own confirmation_timestamp (that would be look-ahead).
                if candle.timestamp_close <= swing.confirmation_timestamp:
                    continue

                broken = self._closed_beyond(candle, swing)
                if not broken:
                    continue

                broken_swing_ids.add(swing.swing_id)
                direction = Direction.BULLISH if swing.type == SwingType.HIGH else Direction.BEARISH
                event_type = (
                    StructureEventType.BOS
                    if direction == prevailing_direction
                    else StructureEventType.CHOCH
                )
                events.append(
                    StructureEvent(
                        event_id=f"{event_type.value}:{swing.swing_id}:{candle.candle_id}",
                        type=event_type,
                        direction=direction,
                        broken_swing_id=swing.swing_id,
                        confirming_candle_id=candle.candle_id,
                        confirmed_at=candle.timestamp_close,
                        timeframe=swing.timeframe,
                    )
                )

        return events

    @staticmethod
    def _closed_beyond(candle: Candle, swing: SwingPoint) -> bool:
        if swing.type == SwingType.HIGH:
            return candle.close > swing.price
        return candle.close < swing.price

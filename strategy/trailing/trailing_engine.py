"""Trailing engine. Strategy FROZEN v1.0 §9 / v4 §G / v5 I7, exactly:

  - uses CONFIRMED M5 MAJOR swings (the same SwingDetectorImpl 2-left/
    2-right fractal from Milestone 2 — NOT the lightweight MicroSwing
    from confirmation_engine.py; conflating the two was explicitly
    forbidden back in v5 I9's terminology decision)
  - never uses an unconfirmed swing
  - only tightens the stop, never loosens it (monotonic)
  - when multiple qualifying swings exist, the MOST PROTECTIVE one wins
    — not simply the most recent
  - respects trailing_sl_buffer_atr

No new trailing formula is invented here — every rule above is a direct
restatement of an already-locked decision, not a new one."""

from __future__ import annotations

from decimal import Decimal
from typing import Sequence

from core.domain.common import Direction
from core.domain.market_data import Candle
from core.domain.structure import SwingPoint, SwingType
from strategy.swings.swing_detector import SwingDetectorImpl


class TrailingEngineImpl:
    def __init__(self, trailing_sl_buffer_atr: Decimal, major_swing_atr_multiple: Decimal) -> None:
        self._buffer_mult = trailing_sl_buffer_atr
        self._swing_detector = SwingDetectorImpl(major_swing_atr_multiple=major_swing_atr_multiple)

    def compute_new_stop(
        self,
        direction: Direction,
        current_sl: Decimal,
        candles: Sequence[Candle],
        atr_by_candle_id: dict[str, Decimal],
        current_atr: Decimal,
    ) -> Decimal | None:
        """Returns a new, MORE protective stop, or None if no qualifying
        candidate exists (including: a candidate exists but would be
        LESS protective than current_sl — that's not a valid update,
        it's silently not-an-update, per the monotonic rule)."""

        swings = self._swing_detector.detect(candles, atr_by_candle_id=atr_by_candle_id)
        confirmed_major = [s for s in swings if s.major_flag]

        buffer = current_atr * self._buffer_mult
        candidates: list[Decimal] = []

        # TRAIL-01 (docs/DECISIONS.md) plausibility invariant: a candidate
        # must never be positioned such that the CURRENT (most recent
        # supplied) candle would already breach it. A genuinely protective
        # stop, by definition, sits behind live price -- it cannot already
        # be past the current candle's own range. A candidate that fails
        # this is not a real forward trailing update; it's a symptom of
        # something upstream (e.g. a swing detected from a stale or
        # economically-irrelevant part of the supplied candle history --
        # see the walk_forward_6m_v3 incident this decision documents,
        # where 11 trades closed with impossible R-multiples up to 614R,
        # all via a trailing stop computed this way) producing a level
        # that doesn't reflect where price actually is right now.
        # Filtering here, at the source, means no caller can ever apply a
        # broken stop regardless of what candle window it passes in.
        current_candle = candles[-1] if candles else None

        if direction == Direction.BULLISH:
            # Protective reference = confirmed major swing LOWS.
            for s in confirmed_major:
                if s.type != SwingType.LOW:
                    continue
                candidate = s.price - buffer
                if candidate <= current_sl:  # monotonic: only tightening candidates
                    continue
                if current_candle is not None and candidate >= current_candle.low:
                    continue  # would already be breached by live price -- not real
                candidates.append(candidate)
            if not candidates:
                return None
            return max(candidates)  # most protective for a long = the HIGHEST qualifying candidate
        else:
            for s in confirmed_major:
                if s.type != SwingType.HIGH:
                    continue
                candidate = s.price + buffer
                if candidate >= current_sl:
                    continue
                if current_candle is not None and candidate <= current_candle.high:
                    continue  # would already be breached by live price -- not real
                candidates.append(candidate)
            if not candidates:
                return None
            return min(candidates)  # most protective for a short = the LOWEST qualifying candidate

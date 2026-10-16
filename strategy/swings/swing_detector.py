"""SwingDetector implementation. Strategy FROZEN v1.0 §2 / Phase 2 §5:

    swing_high(i) = high[i] > high[i-1..i-2] and high[i] > high[i+1..i+2]
    swing_low(i)  = inverse

A candidate becomes CONFIRMED only once candle i+2 has closed — its
effective availability timestamp is that candle's close, never i's own
close. This is the single most important rule in this module: get the
confirmation_timestamp wrong and every downstream no-look-ahead guarantee
in the rest of the engine is built on a false foundation.

'Major swing' status requires a SEPARATE, LATER confirmation: the swing
must be followed by qualifying displacement (>= major_swing_atr x ATR of
that timeframe) before the swing is invalidated. This means major status
can only be known even later than raw confirmation — SwingPoint models
that distinction via major_confirmed_at being independently nullable.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Sequence

from core.domain.common import Timeframe
from core.domain.market_data import Candle
from core.domain.structure import SwingPoint, SwingType

FRACTAL_LEFT = 2
FRACTAL_RIGHT = 2


class SwingDetectorImpl:
    """Concrete implementation of core.contracts.detectors.SwingDetector."""

    def __init__(self, major_swing_atr_multiple: Decimal) -> None:
        self._major_swing_atr_multiple = major_swing_atr_multiple

    def detect(
        self,
        candles: Sequence[Candle],
        atr_by_candle_id: dict[str, Decimal] | None = None,
    ) -> Sequence[SwingPoint]:
        """`atr_by_candle_id` is optional: without it, swings are still
        detected and confirmed, but major_flag stays False for all of
        them (no ATR context to evaluate displacement against) rather
        than guessing a value. This keeps the detector usable in
        contexts (e.g. isolated unit tests) that don't need major-swing
        classification, without ever inventing an ATR."""

        if len(candles) < FRACTAL_LEFT + FRACTAL_RIGHT + 1:
            return []

        symbol = candles[0].symbol
        timeframe = candles[0].timeframe
        swings: list[SwingPoint] = []

        for i in range(FRACTAL_LEFT, len(candles) - FRACTAL_RIGHT):
            window = candles[i - FRACTAL_LEFT : i + FRACTAL_RIGHT + 1]
            pivot = candles[i]

            if self._is_swing_high(window, FRACTAL_LEFT):
                swings.append(
                    self._build_swing(
                        symbol, timeframe, SwingType.HIGH, pivot, candles, i, atr_by_candle_id
                    )
                )
            if self._is_swing_low(window, FRACTAL_LEFT):
                swings.append(
                    self._build_swing(
                        symbol, timeframe, SwingType.LOW, pivot, candles, i, atr_by_candle_id
                    )
                )

        return swings

    @staticmethod
    def _is_swing_high(window: Sequence[Candle], pivot_index: int) -> bool:
        pivot_high = window[pivot_index].high
        others = [c.high for j, c in enumerate(window) if j != pivot_index]
        return all(pivot_high > h for h in others)

    @staticmethod
    def _is_swing_low(window: Sequence[Candle], pivot_index: int) -> bool:
        pivot_low = window[pivot_index].low
        others = [c.low for j, c in enumerate(window) if j != pivot_index]
        return all(pivot_low < l for l in others)

    def _build_swing(
        self,
        symbol: str,
        timeframe: Timeframe,
        swing_type: SwingType,
        pivot: Candle,
        candles: Sequence[Candle],
        pivot_index: int,
        atr_by_candle_id: dict[str, Decimal] | None,
    ) -> SwingPoint:
        confirming_candle = candles[pivot_index + FRACTAL_RIGHT]
        price = pivot.high if swing_type == SwingType.HIGH else pivot.low

        major_flag = False
        major_confirmed_at = None
        if atr_by_candle_id is not None:
            major_confirmed_at = self._find_major_confirmation(
                swing_type, price, candles, pivot_index, atr_by_candle_id
            )
            major_flag = major_confirmed_at is not None

        return SwingPoint(
            swing_id=f"{symbol}:{timeframe.value}:{swing_type.value}:{pivot.candle_id}",
            symbol=symbol,
            timeframe=timeframe,
            type=swing_type,
            price=price,
            pivot_candle_id=pivot.candle_id,
            formation_timestamp=pivot.timestamp_close,
            confirmation_timestamp=confirming_candle.timestamp_close,
            major_flag=major_flag,
            major_confirmed_at=major_confirmed_at,
        )

    def _find_major_confirmation(
        self,
        swing_type: SwingType,
        swing_price: Decimal,
        candles: Sequence[Candle],
        pivot_index: int,
        atr_by_candle_id: dict[str, Decimal],
    ):
        """Scan forward from the confirmed swing for the first candle
        whose close displaces far enough (>= major_swing_atr x ATR of
        this timeframe) away from the swing to qualify it as major,
        stopping if the swing is invalidated (price closes back through
        it) first. Returns that candle's timestamp_close, or None if
        neither happens within the available data."""

        start = pivot_index + FRACTAL_RIGHT + 1
        for candle in candles[start:]:
            atr = atr_by_candle_id.get(candle.candle_id)
            if atr is None:
                continue
            threshold = atr * self._major_swing_atr_multiple

            if swing_type == SwingType.HIGH:
                if candle.close > swing_price:
                    return None  # invalidated before qualifying as major
                if swing_price - candle.close >= threshold:
                    return candle.timestamp_close
            else:
                if candle.close < swing_price:
                    return None
                if candle.close - swing_price >= threshold:
                    return candle.timestamp_close
        return None

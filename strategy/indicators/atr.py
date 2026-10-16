"""Average True Range (ATR) — real historical computation.

Milestone 10 blocker-4. ATR's calculation method is LOCKED per
docs/DECISIONS.md ATR-01 (project-owner decision, recorded after
engineering flagged that no frozen document in this repository stated
a period/method explicitly). Do not silently drift from ATR-01 in
later work — if a change is needed, record it there first.

======================================================================
LOCKED DEFINITION (ATR-01 — see docs/DECISIONS.md for full basis):

  1. Numeric Precision Boundary (Technical Architecture v1.0), quoted
     verbatim in core/domain/market_data.py: "Analytical-only fields
     (e.g. ATR values used purely for scoring/filters, never for
     SL/TP/position-size arithmetic) may use float". This module
     computes True Range and the running average in float, and only
     the OUTPUT value handed to strategy code is converted to Decimal
     (via str(), never a raw float->Decimal cast, to avoid binary-float
     noise digits).

  2. Period: 14 (ATR-01, LOCKED). Still passed as an explicit parameter
     rather than hidden inside strategy code — ATR-01 fixes the VALUE,
     it doesn't mandate hardcoding it; `atr_provider_from_period()`'s
     call site in backtest/pipeline.py remains the one place this is
     named, per ATR-01's own "keep it a named, overridable argument"
     spirit even though the value itself is no longer provisional.

  3. Smoothing method: Wilder's smoothing (ATR-01, LOCKED).

  4. Timeframe: ATR is timeframe-specific (ATR-01 §7) — a component
     consumes ATR computed from its own candle series, never a shared
     global series. Already how this is called: pipeline.py invokes
     the provider once for M5 candles and separately for H4 candles,
     each producing its own independent per-timeframe result.

  5. Warm-up: the first `period` candles produce no ATR value (None /
     absent from the returned dict) because Wilder's method requires
     `period` True Range values before its first (simple-average) seed.
     A candle's ATR value is computed using ONLY that candle and
     earlier ones in the input sequence — never a later one. This
     module accepts a plain, chronologically sorted candle sequence and
     enforces that ordering the same way every other Milestone 10
     aggregation function does (raises rather than
     silently re-sorting).
======================================================================
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Optional, Sequence

from core.domain.common import Timeframe
from core.domain.market_data import ATRContext, Candle

DEFAULT_ATR_PERIOD = 14  # LOCKED — docs/DECISIONS.md ATR-01


@dataclass(frozen=True)
class AtrSeries:
    """Full per-candle ATR output for one (symbol, timeframe, period)
    series. `by_candle_id` omits any candle inside the warm-up window —
    absence from the dict IS the "not yet valid" signal, never a
    fabricated placeholder value."""

    symbol: str
    timeframe: Timeframe
    period: int
    by_candle_id: dict  # dict[str, Decimal]
    first_valid_candle_id: Optional[str]


def _true_range(high: float, low: float, previous_close: Optional[float]) -> float:
    """Standard True Range. The first candle in a series has no prior
    close, so TR is simply its own range — the universal convention
    (there is no "previous" data to compare against, and inventing one
    would be fabrication, not calculation)."""
    if previous_close is None:
        return high - low
    return max(high - low, abs(high - previous_close), abs(low - previous_close))


def compute_atr_series(
    candles: Sequence[Candle], period: int = DEFAULT_ATR_PERIOD, symbol: Optional[str] = None,
) -> AtrSeries:
    """Computes Wilder-smoothed ATR over `candles`, which MUST already
    be sorted ascending by timestamp_close and be a single symbol/
    timeframe series — raises rather than silently re-sorting or mixing
    series, consistent with every other Milestone 10 aggregation
    function in this codebase.

    No look-ahead: the ATR value attached to candles[i] is computed
    using only candles[0..i]. Deterministic and reproducible: pure
    function of the input sequence and `period`, no randomness, no
    wall-clock dependency.
    """
    if period < 1:
        raise ValueError("period must be a positive integer")
    if not candles:
        return AtrSeries(symbol=symbol or "", timeframe=Timeframe.M1, period=period,
                          by_candle_id={}, first_valid_candle_id=None)

    timeframe = candles[0].timeframe
    resolved_symbol = symbol or candles[0].symbol
    for c in candles:
        if c.timeframe != timeframe:
            raise ValueError("candles must all share the same timeframe")
        if resolved_symbol and c.symbol != resolved_symbol:
            raise ValueError("candles must all share the same symbol")
    for previous, current in zip(candles, candles[1:]):
        if current.timestamp_close < previous.timestamp_close:
            raise ValueError(
                "candles must already be sorted ascending by timestamp_close — "
                "compute_atr_series() never re-sorts or shuffles input"
            )

    by_id: dict = {}
    previous_close: Optional[float] = None
    true_ranges: list = []
    wilder_atr: Optional[float] = None
    first_valid_id: Optional[str] = None

    for candle in candles:
        high, low, close = float(candle.high), float(candle.low), float(candle.close)
        tr = _true_range(high, low, previous_close)

        if wilder_atr is None:
            true_ranges.append(tr)
            if len(true_ranges) == period:
                wilder_atr = sum(true_ranges) / period
                by_id[candle.candle_id] = Decimal(str(wilder_atr))
                first_valid_id = candle.candle_id
            # else: still warming up — this candle gets no ATR value.
        else:
            wilder_atr = ((wilder_atr * (period - 1)) + tr) / period
            by_id[candle.candle_id] = Decimal(str(wilder_atr))

        previous_close = close

    return AtrSeries(
        symbol=resolved_symbol, timeframe=timeframe, period=period,
        by_candle_id=by_id, first_valid_candle_id=first_valid_id,
    )


def atr_provider_from_period(period: int = DEFAULT_ATR_PERIOD):
    """Builds a `Callable[[Timeframe, Sequence[Candle]], dict]` matching
    the exact `atr_provider` signature backtest/pipeline.py's
    ReplayPipeline requires.

    Milestone 10 performance optimization: tracks running ATR state per
    timeframe so sequential replay steps advance in O(1) amortized time
    instead of O(n) recomputing the entire growing candle history.
    Non-sequential or truncated series fall back to compute_atr_series.
    """
    cache: dict[Timeframe, dict] = {}

    def _provider(timeframe: Timeframe, candles: Sequence[Candle]) -> dict:
        if not candles:
            return {}

        state = cache.get(timeframe)
        n = len(candles)

        can_advance = (
            state is not None
            and n >= state["last_len"]
            and state["last_len"] > 0
            and candles[state["last_len"] - 1].candle_id == state["last_candle_id"]
        )

        if can_advance:
            if n == state["last_len"]:
                return state["by_id"]

            by_id = state["by_id"]
            wilder_atr = state["wilder_atr"]
            previous_close = state["previous_close"]
            true_ranges = state["true_ranges"]

            for candle in candles[state["last_len"]:]:
                high, low, close = float(candle.high), float(candle.low), float(candle.close)
                tr = _true_range(high, low, previous_close)

                if wilder_atr is None:
                    true_ranges.append(tr)
                    if len(true_ranges) == period:
                        wilder_atr = sum(true_ranges) / period
                        by_id[candle.candle_id] = Decimal(str(wilder_atr))
                else:
                    wilder_atr = ((wilder_atr * (period - 1)) + tr) / period
                    by_id[candle.candle_id] = Decimal(str(wilder_atr))

                previous_close = close

            state["last_len"] = n
            state["last_candle_id"] = candles[-1].candle_id
            state["wilder_atr"] = wilder_atr
            state["previous_close"] = previous_close
            return by_id

        series = compute_atr_series(candles, period=period)
        by_id = dict(series.by_candle_id)

        wilder_atr = None
        previous_close = None
        true_ranges = []
        if candles:
            for candle in candles:
                high, low, close = float(candle.high), float(candle.low), float(candle.close)
                tr = _true_range(high, low, previous_close)
                if wilder_atr is None:
                    true_ranges.append(tr)
                    if len(true_ranges) == period:
                        wilder_atr = sum(true_ranges) / period
                else:
                    wilder_atr = ((wilder_atr * (period - 1)) + tr) / period
                previous_close = close

            cache[timeframe] = {
                "last_len": n,
                "last_candle_id": candles[-1].candle_id,
                "wilder_atr": wilder_atr,
                "previous_close": previous_close,
                "true_ranges": true_ranges,
                "by_id": by_id,
            }

        return by_id

    return _provider


def build_atr_context(
    symbol: str, timeframe: Timeframe, period: int, value: Decimal, computed_at,
) -> ATRContext:
    """Convenience wrapper producing the existing, already-tested
    ATRContext domain object (core/domain/market_data.py) from a raw
    ATR value — reuses that type rather than inventing a parallel one."""
    return ATRContext(symbol=symbol, timeframe=timeframe, period=period, value=value, computed_at=computed_at)

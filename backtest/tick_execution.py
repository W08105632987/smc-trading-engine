"""Tick-level execution infrastructure (PHASE A — audit P0.4).

Resolves the OHLC intrabar ambiguity (when one candle spans both the stop
and a target, which came first?) by walking the actual bid/ask tick path in
time order instead of guessing "SL first".

Price-side rules (spread is IN the data — no extra spread parameter):
  * LONG positions are closed by SELLING  -> exits are evaluated on BID.
  * SHORT positions are closed by BUYING  -> exits are evaluated on ASK.
  * Stop: triggers when the exit-side price crosses the stop. If the tick
    GAPS through the stop, the fill is the worse of (stop, tick price);
    one-way ``slippage`` (price points, default 0) is then added adversely.
  * Target (limit): triggers when the exit-side price reaches the target;
    filled AT the target price (conservative: no price improvement credited).
  * If a single tick satisfies both the stop and a target (only possible on
    a gap), the STOP wins (conservative, documented).
  * Ticks are consumed strictly in the order supplied and must be
    chronologically non-decreasing (a backwards tick raises).

STATUS / HONESTY NOTE: this module is software infrastructure. Results
produced from SYNTHETIC ticks are logic tests only and are NOT execution
validation. Execution fidelity is validated only when REAL Tickstory ticks
are supplied (see CHANGELOG_PHASE_A.md, "Pending validation").

Duck-typed on tick objects exposing ``.timestamp``, ``.bid``, ``.ask``
(e.g. market_data.ticks.tick_aggregator.Tick) so this module is
pydantic-free and unit-testable in isolation.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Iterable, Optional, Sequence


@dataclass(frozen=True)
class TickExit:
    reason: str          # "SL_HIT" | "TP1_HIT" | "TP2_HIT"
    exit_price: Decimal
    timestamp: datetime
    tick_index: int


def _check_order(prev: Optional[datetime], ts: datetime) -> None:
    if prev is not None and ts < prev:
        raise ValueError("ticks must be chronologically non-decreasing")


def find_first_exit_ticks(
    is_long: bool,
    stop_loss: Decimal,
    take_profit_1: Optional[Decimal],
    take_profit_2: Optional[Decimal],
    ticks: Iterable,
    slippage: Decimal = Decimal("0"),
) -> Optional[TickExit]:
    prev = None
    for i, tk in enumerate(ticks):
        _check_order(prev, tk.timestamp)
        prev = tk.timestamp
        if is_long:
            px = tk.bid
            if px <= stop_loss:
                return TickExit("SL_HIT", min(px, stop_loss) - slippage, tk.timestamp, i)
            if take_profit_1 is not None and px >= take_profit_1:
                return TickExit("TP1_HIT", take_profit_1, tk.timestamp, i)
            if take_profit_2 is not None and px >= take_profit_2:
                return TickExit("TP2_HIT", take_profit_2, tk.timestamp, i)
        else:
            px = tk.ask
            if px >= stop_loss:
                return TickExit("SL_HIT", max(px, stop_loss) + slippage, tk.timestamp, i)
            if take_profit_1 is not None and px <= take_profit_1:
                return TickExit("TP1_HIT", take_profit_1, tk.timestamp, i)
            if take_profit_2 is not None and px <= take_profit_2:
                return TickExit("TP2_HIT", take_profit_2, tk.timestamp, i)
    return None


@dataclass(frozen=True)
class TickEntryFill:
    price: Decimal
    timestamp: datetime
    tick_index: int


def fill_entry_tick(
    is_long: bool, ticks: Iterable, not_before: datetime, slippage: Decimal = Decimal("0"),
) -> Optional[TickEntryFill]:
    """Market-order entry on the FIRST tick strictly after ``not_before``
    (the decision instant): longs buy at ASK, shorts sell at BID, plus
    adverse one-way slippage. Provided as infrastructure; NOT yet wired
    into the pipeline's entry path (that would change entry prices, which
    is a strategy-behaviour change deferred until real ticks are supplied —
    see CHANGELOG_PHASE_A.md)."""
    prev = None
    for i, tk in enumerate(ticks):
        _check_order(prev, tk.timestamp)
        prev = tk.timestamp
        if tk.timestamp <= not_before:
            continue
        price = (tk.ask + slippage) if is_long else (tk.bid - slippage)
        return TickEntryFill(price, tk.timestamp, i)
    return None


def ohlc_ambiguous(is_long: bool, stop_loss: Decimal, targets: Sequence[Decimal], candle) -> bool:
    """True when a single candle spans BOTH the stop and a target — i.e. the
    exact situation the OHLC adapter must guess ("SL first") and the tick
    path resolves. Used for reporting how often OHLC execution was guessing."""
    if is_long:
        stop_hit = candle.low <= stop_loss
        tp_hit = any(candle.high >= t for t in targets if t is not None)
    else:
        stop_hit = candle.high >= stop_loss
        tp_hit = any(candle.low <= t for t in targets if t is not None)
    return stop_hit and tp_hit

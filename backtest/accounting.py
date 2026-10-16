"""Backtest account accounting (PHASE A — P0.2 / P0.3).

Pure-Python (Decimal + dataclasses only; deliberately NO pydantic
dependency) so the money logic can be unit-tested in isolation.

What this module owns
---------------------
1. ``AccountLedger`` — a real account: balance, realized P&L, daily
   starting equity, daily realized P&L, trades-today, consecutive-loss
   counter, peak equity and account-level drawdown. It replaces the old
   research-runner behaviour where ``account_equity`` and
   ``daily_starting_equity`` were frozen at the initial balance and
   ``daily_realized_pnl`` was always zero (which made the 2% daily-loss
   gate unable to trip).
2. ``SizingMode`` — FIXED_RISK (risk % of the *reference/initial*
   balance; research default) or COMPOUNDING (risk % of current equity).
3. ``split_tp1_volume`` / ``leg_pnl`` / ``combine_legs`` — explicit
   position-quantity accounting so a TP1 "partial close" genuinely closes
   ``tp1_allocation_pct`` of the position and the remainder is managed
   independently.

Day boundary / reset semantics (decision recorded 2026-10-03, owner):
the daily counters AND the consecutive-loss lock reset at 00:00 UTC.
Never broker server time. This is explicit configuration
(``reset_consecutive_losses_at_utc_midnight``), not an accident.

No wall-clock access anywhere: every timestamp is supplied by the caller
from historical data.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from decimal import ROUND_FLOOR, Decimal
from enum import Enum
from typing import Optional

ZERO = Decimal("0")
ONE = Decimal("1")
HUNDRED = Decimal("100")


class SizingMode(str, Enum):
    FIXED_RISK = "FIXED_RISK"      # risk % of reference (initial) balance
    COMPOUNDING = "COMPOUNDING"    # risk % of current equity


def utc_day(at: datetime) -> date:
    """UTC calendar day of ``at``. Naive datetimes are rejected: the
    day boundary must be deterministic and timezone-independent."""
    if at.tzinfo is None:
        raise ValueError("naive datetime not allowed: UTC day boundary must be explicit")
    return at.astimezone(timezone.utc).date()


# ----------------------------------------------------------------------
# Position leg accounting
# ----------------------------------------------------------------------

def floor_to_step(volume: Decimal, volume_step: Decimal) -> Decimal:
    """Floor to the broker volume step — never round up (same rule as
    the risk engine's position sizing)."""
    if volume_step <= ZERO:
        raise ValueError("volume_step must be positive")
    steps = (volume / volume_step).to_integral_value(rounding=ROUND_FLOOR)
    return steps * volume_step


@dataclass(frozen=True)
class TP1Split:
    tp1_volume: Decimal        # volume closed at TP1 (0 if the split is impossible)
    remaining_volume: Decimal  # volume still open after TP1
    executed: bool             # False => no partial close was possible (see reason)
    reason: str


def split_tp1_volume(
    total_volume: Decimal,
    tp1_allocation_pct: Decimal,
    volume_step: Decimal,
    min_volume: Decimal,
) -> TP1Split:
    """Split ``total_volume`` into the TP1 leg and the runner.

    ``tp1_allocation_pct`` is a percentage (30 == 30%). The TP1 leg is
    floored to ``volume_step``. Allocation semantics:

    * 0   -> nothing closed at TP1 (100% runner).
    * 100 -> everything closed at TP1 (runner = 0, trade is finished).
    * otherwise both legs must be >= ``min_volume``; if either would be
      below the broker minimum the partial close is NOT executed (the
      whole position stays open) and ``executed=False`` with an explicit
      reason is returned so the trade record can disclose it. This is a
      deterministic, documented rule — never a silent rounding.
    """
    if total_volume <= ZERO:
        raise ValueError("total_volume must be positive")
    if not (ZERO <= tp1_allocation_pct <= HUNDRED):
        raise ValueError("tp1_allocation_pct must be within [0, 100]")

    if tp1_allocation_pct == ZERO:
        return TP1Split(ZERO, total_volume, False, "ALLOCATION_ZERO")
    if tp1_allocation_pct == HUNDRED:
        return TP1Split(total_volume, ZERO, True, "ALLOCATION_FULL")

    tp1_volume = floor_to_step(total_volume * tp1_allocation_pct / HUNDRED, volume_step)
    remaining = total_volume - tp1_volume
    if tp1_volume < min_volume or remaining < min_volume:
        return TP1Split(ZERO, total_volume, False, "SPLIT_BELOW_MIN_VOLUME")
    return TP1Split(tp1_volume, remaining, True, "OK")


@dataclass(frozen=True)
class LegResult:
    volume: Decimal
    entry_price: Decimal
    exit_price: Decimal
    gross_pnl: Decimal
    commission: Decimal
    net_pnl: Decimal


def leg_pnl(
    is_long: bool,
    entry_price: Decimal,
    exit_price: Decimal,
    volume: Decimal,
    tick_size: Decimal,
    tick_value: Decimal,
    commission_per_lot: Decimal = ZERO,
) -> LegResult:
    """P&L of one closed leg.

    ``commission_per_lot`` is the ONE-WAY per-lot commission; each leg
    pays a round-turn (entry share + exit) on the volume it closes, so
    the sum over all legs equals ``commission_per_lot * total_volume * 2``
    exactly as the previous single-exit implementation charged.
    Spread/slippage are already embedded in the fill prices supplied by
    the execution adapter and are not double counted here.
    """
    if tick_size <= ZERO:
        raise ValueError("tick_size must be positive")
    move = (exit_price - entry_price) if is_long else (entry_price - exit_price)
    gross = (move / tick_size) * tick_value * volume
    commission = commission_per_lot * volume * Decimal("2")
    return LegResult(volume, entry_price, exit_price, gross, commission, gross - commission)


@dataclass(frozen=True)
class CombinedTrade:
    total_volume: Decimal
    gross_pnl: Decimal
    commission: Decimal
    net_pnl: Decimal
    dollar_risk: Decimal   # ORIGINAL risk denominator (full volume, entry->initial stop)
    realized_r: Decimal    # net_pnl / dollar_risk


def initial_dollar_risk(
    entry_price: Decimal, initial_stop: Decimal, total_volume: Decimal,
    tick_size: Decimal, tick_value: Decimal,
) -> Decimal:
    if tick_size <= ZERO:
        raise ValueError("tick_size must be positive")
    return (abs(entry_price - initial_stop) / tick_size) * tick_value * total_volume


def combine_legs(legs: list[LegResult], dollar_risk: Decimal) -> CombinedTrade:
    """Weighted combination of all legs. R keeps the ORIGINAL full-position
    risk denominator, so a 30/70 trade's R is the true weighted outcome
    (never one exit price standing in for the whole original volume)."""
    if not legs:
        raise ValueError("at least one leg required")
    total_volume = sum((l.volume for l in legs), ZERO)
    gross = sum((l.gross_pnl for l in legs), ZERO)
    commission = sum((l.commission for l in legs), ZERO)
    net = gross - commission
    r = (net / dollar_risk) if dollar_risk > ZERO else ZERO
    return CombinedTrade(total_volume, gross, commission, net, dollar_risk, r)


# ----------------------------------------------------------------------
# Account ledger
# ----------------------------------------------------------------------

@dataclass
class LedgerSnapshot:
    """Plain values the risk engine needs (the pydantic RiskSnapshot is
    built from this at the pipeline boundary)."""
    timestamp: datetime
    account_equity: Decimal            # balance + floating P&L (mark-to-market)
    sizing_equity: Decimal             # equity used to size the next trade
    daily_starting_equity: Decimal
    daily_realized_pnl: Decimal
    consecutive_losses_count: int
    trades_today_count: int


@dataclass
class AccountLedger:
    initial_balance: Decimal
    sizing_mode: SizingMode = SizingMode.FIXED_RISK
    reset_consecutive_losses_at_utc_midnight: bool = True

    balance: Decimal = field(init=False)
    realized_pnl_total: Decimal = field(init=False, default=ZERO)
    peak_equity: Decimal = field(init=False)
    max_drawdown_abs: Decimal = field(init=False, default=ZERO)
    max_drawdown_pct: Decimal = field(init=False, default=ZERO)

    current_day: Optional[date] = field(init=False, default=None)
    daily_starting_equity: Decimal = field(init=False)
    daily_realized_pnl: Decimal = field(init=False, default=ZERO)
    trades_today: int = field(init=False, default=0)
    consecutive_losses: int = field(init=False, default=0)
    closed_trade_count: int = field(init=False, default=0)

    def __post_init__(self) -> None:
        if self.initial_balance <= ZERO:
            raise ValueError("initial_balance must be positive")
        self.balance = self.initial_balance
        self.peak_equity = self.initial_balance
        self.daily_starting_equity = self.initial_balance

    # -- day handling ---------------------------------------------------
    def roll_day(self, at: datetime) -> bool:
        """Advance to ``at``'s UTC day. Returns True if a boundary was
        crossed. On a new day: daily starting equity := current balance,
        daily realized P&L and trades-today := 0, and (configurable,
        default True) the consecutive-loss lock resets."""
        day = utc_day(at)
        if self.current_day is None:
            self.current_day = day
            self.daily_starting_equity = self.balance
            return False
        if day == self.current_day:
            return False
        if day < self.current_day:
            raise ValueError("ledger time moved backwards across a UTC day boundary")
        self.current_day = day
        self.daily_starting_equity = self.balance
        self.daily_realized_pnl = ZERO
        self.trades_today = 0
        if self.reset_consecutive_losses_at_utc_midnight:
            self.consecutive_losses = 0
        return True

    # -- sizing ---------------------------------------------------------
    def sizing_equity(self, floating_pnl: Decimal = ZERO) -> Decimal:
        if self.sizing_mode == SizingMode.FIXED_RISK:
            return self.initial_balance
        return self.balance + floating_pnl

    def equity(self, floating_pnl: Decimal = ZERO) -> Decimal:
        return self.balance + floating_pnl

    # -- events ---------------------------------------------------------
    def on_trade_opened(self, at: datetime) -> None:
        """A trade counts toward the daily trade cap when it is OPENED
        (and is attributed to the UTC day it opened on)."""
        self.roll_day(at)
        self.trades_today += 1

    def on_realized(self, at: datetime, net_pnl: Decimal) -> None:
        """Book realized P&L (a partial close or a final close).
        Net of commission; spread/slippage are already in the fill prices."""
        self.roll_day(at)
        self.balance += net_pnl
        self.realized_pnl_total += net_pnl
        self.daily_realized_pnl += net_pnl
        self._update_drawdown(self.balance)

    def on_trade_closed(self, at: datetime, total_net_pnl: Decimal) -> None:
        """Update the consecutive-loss counter from the WHOLE trade's net
        result (all legs combined). Break-even or worse counts as a loss,
        matching the previous ``realized_r <= 0`` rule."""
        self.roll_day(at)
        self.closed_trade_count += 1
        if total_net_pnl > ZERO:
            self.consecutive_losses = 0
        else:
            self.consecutive_losses += 1

    def mark_to_market(self, floating_pnl: Decimal) -> None:
        self._update_drawdown(self.balance + floating_pnl)

    def _update_drawdown(self, equity: Decimal) -> None:
        if equity > self.peak_equity:
            self.peak_equity = equity
        dd = self.peak_equity - equity
        if dd > self.max_drawdown_abs:
            self.max_drawdown_abs = dd
            self.max_drawdown_pct = (dd / self.peak_equity * HUNDRED) if self.peak_equity > ZERO else ZERO

    # -- snapshot -------------------------------------------------------
    def snapshot(self, at: datetime, floating_pnl: Decimal = ZERO) -> LedgerSnapshot:
        self.roll_day(at)
        return LedgerSnapshot(
            timestamp=at,
            account_equity=self.equity(floating_pnl),
            sizing_equity=self.sizing_equity(floating_pnl),
            daily_starting_equity=self.daily_starting_equity,
            daily_realized_pnl=self.daily_realized_pnl,
            consecutive_losses_count=self.consecutive_losses,
            trades_today_count=self.trades_today,
        )

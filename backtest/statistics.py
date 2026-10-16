"""Performance statistics over a set of TradeRecords. Milestone 9,
Section E.

Every formula here is a standard, textbook trading-performance metric
(win rate, expectancy, profit factor, max drawdown, streaks) — none of
this is strategy logic, and none of it changes what the strategy does.
It only describes what already happened, from TradeRecord.realized_r
values that backtest/pipeline.py already computed. No threshold here
gates a trading decision.

CHRONOLOGICAL ORDERING: drawdown and streak calculations are ORDER-
DEPENDENT (a peak-to-trough decline only means something walked forward
in time). Trades are sorted by `exit_timestamp` before those
calculations — never re-derived from anything else, and never shuffled
— matching this project's established "pure timestamp-based, never
shuffles" stance (same principle backtest/validation/walk_forward.py
applies to its own partitioning). Only CLOSED trades (realized_r is not
None) participate in any of these statistics; an OPEN trade has no
realized outcome yet to measure.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Optional, Sequence

from backtest.trade_log import TradeRecord


@dataclass(frozen=True)
class PerformanceStatistics:
    total_trades: int
    closed_trades: int
    open_trades: int
    winning_trades: int
    losing_trades: int
    win_rate: Decimal
    average_win_r: Optional[Decimal]
    average_loss_r: Optional[Decimal]
    expectancy_r: Decimal
    profit_factor: Optional[Decimal]  # None when there are no losses (undefined, not infinite)
    max_drawdown_r: Decimal
    longest_winning_streak: int
    longest_losing_streak: int
    exit_reason_counts: dict  # dict[str, int]
    net_r: Decimal


def compute_statistics(trades: Sequence[TradeRecord]) -> PerformanceStatistics:
    exit_reason_counts: dict = {}
    for t in trades:
        exit_reason_counts[t.exit_reason] = exit_reason_counts.get(t.exit_reason, 0) + 1

    closed = [t for t in trades if t.realized_r is not None]
    closed_sorted = sorted(closed, key=lambda t: t.exit_timestamp)

    total_trades = len(trades)
    closed_count = len(closed_sorted)
    open_count = total_trades - closed_count

    wins = [t.realized_r for t in closed_sorted if t.realized_r > 0]
    losses = [t.realized_r for t in closed_sorted if t.realized_r <= 0]

    winning_trades = len(wins)
    losing_trades = len(losses)
    win_rate = Decimal(winning_trades) / Decimal(closed_count) if closed_count else Decimal("0")

    average_win_r = (sum(wins, Decimal("0")) / len(wins)) if wins else None
    average_loss_r = (sum(losses, Decimal("0")) / len(losses)) if losses else None

    if closed_count:
        loss_rate = Decimal("1") - win_rate
        expectancy_r = win_rate * (average_win_r or Decimal("0")) + loss_rate * (average_loss_r or Decimal("0"))
    else:
        expectancy_r = Decimal("0")

    gross_win = sum(wins, Decimal("0"))
    gross_loss = abs(sum(losses, Decimal("0")))
    profit_factor = (gross_win / gross_loss) if gross_loss > 0 else None

    net_r = sum((t.realized_r for t in closed_sorted), Decimal("0"))

    max_drawdown_r = Decimal("0")
    cumulative = Decimal("0")
    peak = Decimal("0")
    for t in closed_sorted:
        cumulative += t.realized_r
        if cumulative > peak:
            peak = cumulative
        drawdown = peak - cumulative
        if drawdown > max_drawdown_r:
            max_drawdown_r = drawdown

    longest_winning_streak = 0
    longest_losing_streak = 0
    current_win_streak = 0
    current_loss_streak = 0
    for t in closed_sorted:
        if t.realized_r > 0:
            current_win_streak += 1
            current_loss_streak = 0
        else:
            current_loss_streak += 1
            current_win_streak = 0
        longest_winning_streak = max(longest_winning_streak, current_win_streak)
        longest_losing_streak = max(longest_losing_streak, current_loss_streak)

    return PerformanceStatistics(
        total_trades=total_trades, closed_trades=closed_count, open_trades=open_count,
        winning_trades=winning_trades, losing_trades=losing_trades, win_rate=win_rate,
        average_win_r=average_win_r, average_loss_r=average_loss_r, expectancy_r=expectancy_r,
        profit_factor=profit_factor, max_drawdown_r=max_drawdown_r,
        longest_winning_streak=longest_winning_streak, longest_losing_streak=longest_losing_streak,
        exit_reason_counts=exit_reason_counts, net_r=net_r,
    )


@dataclass(frozen=True)
class PlausibilityWarning:
    """One flagged trade from check_plausibility. Advisory only -- this
    never mutates or excludes a trade, it only surfaces something that
    should be looked at by a human before the run's headline statistics
    are trusted."""
    trade_id: str
    reason: str
    realized_r: Optional[Decimal]
    entry_timestamp: object
    exit_timestamp: object
    exit_reason: str


def check_plausibility(
    trades: Sequence[TradeRecord],
    max_abs_r: Decimal = Decimal("20"),
) -> list[PlausibilityWarning]:
    """TRAIL-01 (docs/DECISIONS.md) standing plausibility guard.

    Found via walk_forward_6m_v3: 11 of 61 trades closed with realized_r
    up to 614, all via a trailing-stop bug that has since been fixed at
    its root (strategy/trailing/trailing_engine.py). That fix makes the
    specific mechanism behind THIS incident impossible going forward, but
    this project has now hit three separate bugs that each hid behind
    numbers that looked fine or impressive at a glance (the lockout bug,
    the O(n^2) speed bug, and this one). This check exists so the next
    one -- whatever its cause turns out to be -- gets caught automatically
    rather than requiring another round of manual trade-by-trade
    inspection to notice.

    This does NOT replace the fix above; TRAIL-01's invariant is what
    actually prevents the bug. This is a second, independent layer: if
    some future, different bug produces another implausible trade by any
    other mechanism, this still catches it. Advisory only -- it flags,
    it never silently excludes a trade from the real statistics.

    max_abs_r is deliberately a simple, single, well-justified bound: a
    genuinely good trailing-stop exit capturing a large trend move can
    legitimately produce a double-digit R outcome, so this is not set
    aggressively tight. 20R was chosen because it comfortably clears
    every trade in the walk_forward_6m_v3 run EXCEPT the 11 now-explained
    bugged ones (the next-highest genuine trade was under 21R before the
    fix) -- it is a real, evidence-based line, not an arbitrary round
    number.
    """
    warnings: list[PlausibilityWarning] = []
    for t in trades:
        if t.realized_r is None:
            continue
        if abs(t.realized_r) > max_abs_r:
            warnings.append(PlausibilityWarning(
                trade_id=t.trade_id,
                reason=f"realized_r={t.realized_r} exceeds plausibility ceiling of {max_abs_r}R",
                realized_r=t.realized_r,
                entry_timestamp=t.entry_timestamp,
                exit_timestamp=t.exit_timestamp,
                exit_reason=t.exit_reason,
            ))
    return warnings


@dataclass(frozen=True)
class RobustnessMetric:
    excluded_top_n: int
    excluded_r_values: tuple[Decimal, ...]
    remaining_closed_trades: int
    winning_trades: int
    losing_trades: int
    win_rate: Decimal
    expectancy_r: Decimal
    profit_factor: Optional[Decimal]
    net_r: Decimal


def compute_outlier_robustness(
    trades: Sequence[TradeRecord],
    top_n_list: Sequence[int] = (1, 2),
) -> dict[str, RobustnessMetric]:
    """Recomputes performance statistics with the top N winning trades
    (highest realized_r) excluded, so the system baseline can be evaluated
    without reliance on tail outlier trades.

    Returns a dict keyed by e.g. 'top_1_excluded', 'top_2_excluded'.
    """
    closed = [t for t in trades if t.realized_r is not None]
    winners = sorted([t for t in closed if t.realized_r > Decimal("0")], key=lambda t: t.realized_r, reverse=True)

    results: dict[str, RobustnessMetric] = {}
    for n in top_n_list:
        if n > len(winners):
            non_winners = [t for t in closed if t.realized_r <= Decimal("0")]
            results[f"top_{n}_excluded"] = RobustnessMetric(
                excluded_top_n=n,
                excluded_r_values=tuple(t.realized_r for t in winners),
                remaining_closed_trades=len(non_winners),
                winning_trades=0,
                losing_trades=len(non_winners),
                win_rate=Decimal("0"),
                expectancy_r=Decimal("-1") if non_winners else Decimal("0"),
                profit_factor=Decimal("0") if non_winners else None,
                net_r=sum((t.realized_r for t in non_winners), Decimal("0")),
            )
            continue

        excluded = set(winners[:n])
        remaining = [t for t in closed if t not in excluded]
        rem_stats = compute_statistics(remaining)
        results[f"top_{n}_excluded"] = RobustnessMetric(
            excluded_top_n=n,
            excluded_r_values=tuple(t.realized_r for t in winners[:n]),
            remaining_closed_trades=rem_stats.closed_trades,
            winning_trades=rem_stats.winning_trades,
            losing_trades=rem_stats.losing_trades,
            win_rate=rem_stats.win_rate,
            expectancy_r=rem_stats.expectancy_r,
            profit_factor=rem_stats.profit_factor,
            net_r=rem_stats.net_r,
        )
    return results

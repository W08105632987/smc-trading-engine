"""TradeRecord — Item E's minimal, reproducible trade-level output.
Full Phase 3 statistics (walk-forward, Monte Carlo, ablation) are
explicitly Milestone 9's job — this is only the raw record plus enough
fields to reconstruct exactly what happened and why."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Optional

from core.domain.common import Direction


@dataclass(frozen=True)
class TradeRecord:
    trade_id: str
    decision_id: str
    setup_id: str
    config_hash: str
    direction: Direction
    entry_timestamp: datetime
    entry_price: Decimal
    stop_loss: Decimal
    take_profit_1: Decimal
    take_profit_2: Decimal
    exit_timestamp: Optional[datetime]
    exit_price: Optional[Decimal]
    exit_reason: str  # "SL_HIT" | "TP1_HIT" | "TP2_HIT" | "TRAILING_STOP_HIT" | "OPEN"
    realized_r: Optional[Decimal]
    cost_model: str = "FRICTIONLESS"
    position_size: Optional[Decimal] = None
    dollar_risk: Optional[Decimal] = None
    gross_pnl: Optional[Decimal] = None
    commission: Optional[Decimal] = None
    net_pnl: Optional[Decimal] = None
    # PHASE A (P0.2): explicit partial-close disclosure. `position_size` is
    # the ORIGINAL full volume; realized_r is the weighted result of all
    # legs against the original risk denominator.
    tp1_executed: Optional[bool] = None       # None = TP1 never reached
    tp1_exit_price: Optional[Decimal] = None
    tp1_exit_timestamp: Optional[datetime] = None
    tp1_volume: Optional[Decimal] = None
    runner_volume: Optional[Decimal] = None
    tp1_split_reason: Optional[str] = None    # "OK" | "SPLIT_BELOW_MIN_VOLUME" | ...
    # PHASE A audit-ledger fields (all optional so older callers are unaffected)
    engine: Optional[str] = None                      # "REFERENCE" | "FAST"
    zone_creation_timestamp: Optional[datetime] = None
    setup_created_at: Optional[datetime] = None
    setup_expiry_at: Optional[datetime] = None
    setup_score: Optional[Decimal] = None
    touch_started_at: Optional[datetime] = None
    confirmation_timestamp: Optional[datetime] = None
    balance_before: Optional[Decimal] = None          # ledger balance when the trade opened
    balance_after: Optional[Decimal] = None           # ledger balance after the final leg
    spread_assumption: Optional[Decimal] = None
    slippage_assumption: Optional[Decimal] = None
    commission_per_lot_assumption: Optional[Decimal] = None
    entry_reason: Optional[str] = None


def compute_realized_r(
    direction: Direction,
    entry: Decimal,
    stop: Decimal,
    exit_price: Decimal,
    commission_r: Decimal = Decimal("0"),
) -> Decimal:
    risk = abs(entry - stop)
    if risk == 0:
        return Decimal("0")
    if direction == Direction.BULLISH:
        gross_r = (exit_price - entry) / risk
    else:
        gross_r = (entry - exit_price) / risk
    return gross_r - commission_r


@dataclass(frozen=True)
class BacktestSummary:
    """Minimal pass-through metrics only (Item E) — total/winning/losing
    trade counts and net R. Nothing statistical beyond that."""

    total_trades: int
    winning_trades: int
    losing_trades: int
    net_r: Decimal

    @staticmethod
    def from_trades(trades: list[TradeRecord]) -> "BacktestSummary":
        closed = [t for t in trades if t.realized_r is not None]
        winning = sum(1 for t in closed if t.realized_r > 0)
        losing = sum(1 for t in closed if t.realized_r <= 0)
        net_r = sum((t.realized_r for t in closed), Decimal("0"))
        return BacktestSummary(
            total_trades=len(closed), winning_trades=winning, losing_trades=losing, net_r=net_r,
        )

"""BacktestExecutionAdapter — historical backtesting ONLY. Distinct
from Milestone 6's MockExecutionAdapter (a manually-scripted test
double for exercising the execution state machine in isolation): this
adapter is DATA-DRIVEN — it determines fills and SL/TP hits from actual
historical candle OHLC, deterministically, with no human/test
pre-scripting each response.

COST MODEL (Item D, approved): FRICTIONLESS. Market orders always fill
at intended_entry_price, zero slippage/spread/commission. This is a
disclosed limitation, not a claim about live performance — every
TradeRecord this produces is tagged with cost_model="FRICTIONLESS".

INTRABAR SL/TP ORDERING — DOCUMENTED ASSUMPTION, NOT A FROZEN RULE:
when a single candle's high-low range crosses BOTH the stop and a take-
profit level, real intrabar price path is unknowable from OHLC alone.
This adapter assumes SL is checked FIRST (the conservative, worst-case
ordering) — flagged here and in the Milestone 8 report for explicit
sign-off, since Phase 2 §23 requires 'consistent handling of intrabar
ambiguity' but never specifies which direction. This should be
revisited once real tick data is available.

Never uses datetime.now(), time.time(), or random() — every timestamp
comes from the historical candle data itself."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import Enum
from typing import Optional, Sequence

from core.domain.common import Direction
from core.domain.decision import TradeDecision
from core.domain.execution import ExecutionOrder, OrderState
from core.domain.market_data import Candle


class CostModel(str, Enum):
    FRICTIONLESS = "FRICTIONLESS"
    REALISTIC = "REALISTIC"


@dataclass(frozen=True)
class ExitCheck:
    reason: str  # "SL_HIT" | "TP1_HIT" | "TP2_HIT"
    exit_price: Decimal
    candle_id: str


def find_first_exit(
    direction: Direction,
    stop_loss: Decimal,
    take_profit_1: Optional[Decimal],
    take_profit_2: Optional[Decimal],
    candles: Sequence[Candle],
    spread: Decimal = Decimal("0"),
    slippage: Decimal = Decimal("0"),
) -> Optional[ExitCheck]:
    """Scans candles IN ORDER (never out of sequence) for the first one
    whose OHLC crosses an exit level. Within a single candle, SL is
    checked before TP — see module docstring for why this is a flagged
    assumption, not a frozen rule.

    When spread/slippage are provided (REALISTIC cost model):
    - BULLISH (Long exit is a SELL at Bid):
      - SL trigger: candle.low <= stop_loss. Fills at stop_loss - slippage.
      - TP trigger: candle.high >= take_profit. Fills at take_profit.
    - BEARISH (Short exit is a BUY at Ask = Bid + spread):
      - SL trigger: candle.high >= stop_loss - spread (Ask reaches SL). Fills at stop_loss + slippage.
      - TP trigger: candle.low <= take_profit - spread (Ask reaches TP). Fills at take_profit.
    """

    for candle in candles:
        if direction == Direction.BULLISH:
            sl_hit = candle.low <= stop_loss
            if sl_hit:
                return ExitCheck(reason="SL_HIT", exit_price=stop_loss - slippage, candle_id=candle.candle_id)

            if take_profit_1 is not None and candle.high >= take_profit_1:
                return ExitCheck(reason="TP1_HIT", exit_price=take_profit_1, candle_id=candle.candle_id)

            if take_profit_2 is not None and candle.high >= take_profit_2:
                return ExitCheck(reason="TP2_HIT", exit_price=take_profit_2, candle_id=candle.candle_id)
        else:
            sl_hit = candle.high >= (stop_loss - spread)
            if sl_hit:
                return ExitCheck(reason="SL_HIT", exit_price=stop_loss + slippage, candle_id=candle.candle_id)

            if take_profit_1 is not None and candle.low <= (take_profit_1 - spread):
                return ExitCheck(reason="TP1_HIT", exit_price=take_profit_1, candle_id=candle.candle_id)

            if take_profit_2 is not None and candle.low <= (take_profit_2 - spread):
                return ExitCheck(reason="TP2_HIT", exit_price=take_profit_2, candle_id=candle.candle_id)

    return None


class BacktestExecutionAdapter:
    """Implements the submission side of core.contracts.execution.ExecutionAdapter.
    Position lifecycle/exit management is driven externally by
    backtest/engine.py calling find_first_exit() against replay-visible
    candles only — this class itself holds no time-series state."""

    def __init__(
        self,
        cost_model: str = "FRICTIONLESS",
        spread: Decimal = Decimal("0"),
        slippage: Decimal = Decimal("0"),
        commission_per_lot: Decimal = Decimal("0"),
    ) -> None:
        self._orders: dict[str, ExecutionOrder] = {}
        self._cost_model = cost_model
        self._spread = spread
        self._slippage = slippage
        self._commission_per_lot = commission_per_lot

    @property
    def cost_model(self) -> str:
        return self._cost_model

    @property
    def spread(self) -> Decimal:
        return self._spread

    @property
    def slippage(self) -> Decimal:
        return self._slippage

    @property
    def commission_per_lot(self) -> Decimal:
        return self._commission_per_lot

    def submit_market_order(self, decision: TradeDecision) -> ExecutionOrder:
        if self._cost_model == "REALISTIC":
            if decision.direction == Direction.BULLISH:
                # Long entry: buys at Ask = Bid + spread + adverse slippage
                fill_price = decision.intended_entry_price + self._spread + self._slippage
            else:
                # Short entry: sells at Bid - adverse slippage
                fill_price = decision.intended_entry_price - self._slippage
        else:
            fill_price = decision.intended_entry_price

        order = ExecutionOrder(
            order_id=f"bt:{decision.decision_id}", decision_id=decision.decision_id,
            state=OrderState.ORDER_FILLED, requested_volume=decision.position_size,
            filled_volume=decision.position_size, requested_price=decision.intended_entry_price,
            avg_fill_price=fill_price,
            submitted_at=decision.decision_timestamp, acknowledged_at=decision.decision_timestamp,
        )
        self._orders[order.order_id] = order
        return order

    def get_order(self, order_id: str) -> ExecutionOrder | None:
        return self._orders.get(order_id)

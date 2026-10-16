"""Risk domain objects. daily_loss_limit basis is LOCKED (Implementation
Spec v1.0 §9) as mark-to-market equity, including floating P&L of the
currently-open position — RiskSnapshot.account_equity is expected to
already reflect that at the point it's constructed; this module does not
recompute it, it only carries the value forward for the gate checks."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Optional

from pydantic import field_validator, model_validator

from core.domain.common import UTCModel, ensure_utc


class RiskSnapshot(UTCModel):
    snapshot_id: str
    timestamp: datetime
    account_equity: Decimal  # mark-to-market: realized + floating P&L of the open position
    daily_starting_equity: Decimal
    daily_realized_pnl: Decimal
    consecutive_losses_count: int
    trades_today_count: int
    # PHASE A (P0.3): equity used to SIZE the next trade. None => use
    # account_equity (legacy behaviour). FIXED_RISK mode sets this to the
    # reference/initial balance while account_equity stays the real
    # mark-to-market equity used by the daily-loss gate.
    sizing_equity: Optional[Decimal] = None

    @field_validator("timestamp")
    @classmethod
    def _utc(cls, value: datetime) -> datetime:
        return ensure_utc(value)

    @field_validator("consecutive_losses_count", "trades_today_count")
    @classmethod
    def _non_negative(cls, value: int) -> int:
        if value < 0:
            raise ValueError("count fields cannot be negative")
        return value

    def daily_loss(self) -> Decimal:
        """daily_starting_equity - account_equity (mark-to-market, LOCKED
        basis). Positive means a loss; the caller compares this against
        the configured daily_loss_limit."""
        return self.daily_starting_equity - self.account_equity


class PositionSizingResult(UTCModel):
    entry: Decimal
    stop: Decimal
    tick_size: Decimal
    tick_value: Decimal
    volume_step: Decimal
    min_volume: Decimal
    max_volume: Decimal
    computed_volume: Decimal
    rounded_volume: Decimal
    risk_amount: Decimal
    risk_percentage_actual: Decimal
    approved: bool
    reason_code: str

    @model_validator(mode="after")
    def _rounding_never_increases_risk(self) -> "PositionSizingResult":
        # Execution FROZEN v1.0 §13: broker rounding may never silently
        # increase approved risk. Rounded volume must never exceed the
        # mathematically computed volume.
        if self.rounded_volume > self.computed_volume:
            raise ValueError(
                "rounded_volume exceeds computed_volume — broker rounding "
                "must never silently increase approved risk"
            )
        return self


class RiskDecision(UTCModel):
    daily_loss_limit_ok: bool
    consecutive_loss_ok: bool
    trade_count_ok: bool
    sizing_ok: bool
    approved: bool
    reason_code: str

    @model_validator(mode="after")
    def _approval_consistent_with_gates(self) -> "RiskDecision":
        all_gates_pass = (
            self.daily_loss_limit_ok
            and self.consecutive_loss_ok
            and self.trade_count_ok
            and self.sizing_ok
        )
        if self.approved and not all_gates_pass:
            raise ValueError("approved=True requires every individual gate to be True")
        return self

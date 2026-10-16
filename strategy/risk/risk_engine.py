"""Risk engine. Execution FROZEN v1.0 §13 / Phase 2 §19:

    risk_amount = account_equity * risk_per_trade_pct
    position_size = risk_amount / monetary_loss_per_unit_at_SL

Broker rounding must NEVER silently increase approved risk — rounding
is always FLOORED to the nearest volume_step, never ceiling'd, and
PositionSizingResult's own validator (core/domain/risk.py) independently
rejects any result where rounded_volume > computed_volume as a second,
structural line of defense.

daily_loss_limit basis is LOCKED as mark-to-market equity (Implementation
Spec v1.0 §9) — RiskSnapshot.account_equity is expected to already
reflect that; this engine only compares it against the limit, it does
not recompute equity itself."""

from __future__ import annotations

from decimal import ROUND_FLOOR, Decimal

from core.domain.risk import PositionSizingResult, RiskDecision, RiskSnapshot


class RiskEngineImpl:
    """Concrete implementation of core.contracts.detectors.RiskEngine."""

    def __init__(
        self,
        risk_per_trade_pct: Decimal,
        daily_loss_limit_pct: Decimal,
        max_consecutive_losses: int,
        max_trades_per_day: int,
    ) -> None:
        self._risk_per_trade_pct = risk_per_trade_pct
        self._daily_loss_limit_pct = daily_loss_limit_pct
        self._max_consecutive_losses = max_consecutive_losses
        self._max_trades_per_day = max_trades_per_day

    def compute_position_size(
        self,
        account_equity: Decimal,
        entry: Decimal,
        stop: Decimal,
        tick_size: Decimal,
        tick_value: Decimal,
        volume_step: Decimal,
        min_volume: Decimal,
        max_volume: Decimal,
    ) -> PositionSizingResult:
        risk_amount = account_equity * (self._risk_per_trade_pct / Decimal("100"))
        stop_distance = abs(entry - stop)

        if stop_distance == 0 or tick_size == 0:
            return PositionSizingResult(
                entry=entry, stop=stop, tick_size=tick_size, tick_value=tick_value,
                volume_step=volume_step, min_volume=min_volume, max_volume=max_volume,
                computed_volume=Decimal("0"), rounded_volume=Decimal("0"),
                risk_amount=risk_amount, risk_percentage_actual=Decimal("0"),
                approved=False, reason_code="STOP_TOO_TIGHT",
            )

        stop_distance_ticks = stop_distance / tick_size
        loss_per_lot = stop_distance_ticks * tick_value
        computed_volume = risk_amount / loss_per_lot

        # Floor to volume_step — never ceiling. Flooring is the only
        # rounding direction that can never increase risk above what was
        # approved.
        steps = (computed_volume / volume_step).to_integral_value(rounding=ROUND_FLOOR)
        rounded_volume = steps * volume_step

        if rounded_volume < min_volume:
            return PositionSizingResult(
                entry=entry, stop=stop, tick_size=tick_size, tick_value=tick_value,
                volume_step=volume_step, min_volume=min_volume, max_volume=max_volume,
                computed_volume=computed_volume, rounded_volume=Decimal("0"),
                risk_amount=risk_amount, risk_percentage_actual=Decimal("0"),
                approved=False, reason_code="LOT_BELOW_MINIMUM",
            )

        rounded_volume = min(rounded_volume, max_volume)
        actual_risk_amount = rounded_volume * loss_per_lot
        actual_risk_pct = (actual_risk_amount / account_equity) * Decimal("100") if account_equity else Decimal("0")

        return PositionSizingResult(
            entry=entry, stop=stop, tick_size=tick_size, tick_value=tick_value,
            volume_step=volume_step, min_volume=min_volume, max_volume=max_volume,
            computed_volume=computed_volume, rounded_volume=rounded_volume,
            risk_amount=actual_risk_amount, risk_percentage_actual=actual_risk_pct,
            approved=True, reason_code="OK",
        )

    def evaluate_gates(self, snapshot: RiskSnapshot, sizing: PositionSizingResult) -> RiskDecision:
        daily_loss_limit_value = snapshot.daily_starting_equity * (self._daily_loss_limit_pct / Decimal("100"))
        daily_loss_limit_ok = snapshot.daily_loss() < daily_loss_limit_value
        consecutive_loss_ok = snapshot.consecutive_losses_count < self._max_consecutive_losses
        trade_count_ok = snapshot.trades_today_count < self._max_trades_per_day
        sizing_ok = sizing.approved

        all_ok = daily_loss_limit_ok and consecutive_loss_ok and trade_count_ok and sizing_ok
        if all_ok:
            reason_code = "OK"
        elif not daily_loss_limit_ok:
            reason_code = "DAILY_LOSS_LIMIT"
        elif not consecutive_loss_ok:
            reason_code = "CONSECUTIVE_LOSS_LOCK"
        elif not trade_count_ok:
            reason_code = "DAILY_TRADE_LIMIT"
        else:
            reason_code = sizing.reason_code

        return RiskDecision(
            daily_loss_limit_ok=daily_loss_limit_ok, consecutive_loss_ok=consecutive_loss_ok,
            trade_count_ok=trade_count_ok, sizing_ok=sizing_ok,
            approved=all_ok, reason_code=reason_code,
        )

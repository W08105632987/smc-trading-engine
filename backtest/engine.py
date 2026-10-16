"""Backtest orchestrator. HONEST SCOPE DISCLOSURE, read this first:

This module wires the DOWNSTREAM half of the pipeline: given a Setup
that already exists (pending, from zone/liquidity/MSS detection built
in Milestones 2-4) plus the M5 candles visible from that point forward,
it drives: touch confirmation -> lifecycle (expiry/invalidation) ->
scoring -> risk -> TradeDecision -> simulated execution -> exit
(SL/TP/trailing) -> TradeRecord.

It does NOT automatically run the full swings->structure->liquidity->
zones->setup-CREATION chain across a continuous multi-day historical
dataset to discover setups on its own — that upstream wiring (gluing 8
individually-built-and-tested detectors from Milestones 2-4 into one
continuously-running discovery loop) is real additional integration
work this milestone did not attempt to build and verify to the same
standard as everything else here. Flagging this explicitly rather than
claiming a fully automatic pipeline that wasn't actually built and
tested end-to-end.

TRAILING SIMPLIFICATION, also disclosed: trailing is recomputed ONCE
using all candles between the TP1 candle and the eventual exit, not
continuously re-evaluated candle-by-candle. This is monotonic and uses
only confirmed swings (so it's not incorrect), but it's coarser than a
fully step-by-step replay would be."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Optional, Sequence

from core.domain.common import Direction, Environment
from core.domain.market_data import Candle
from core.domain.risk import RiskSnapshot
from core.domain.setups import Setup, SetupState, TouchOutcome
from core.domain.zones import Zone

from backtest.execution_adapter import BacktestExecutionAdapter, find_first_exit
from backtest.setup_lifecycle import evaluate_setup_lifecycle
from backtest.trade_log import TradeRecord, compute_realized_r
from decision.trade_decision_generator import TradeDecisionGeneratorImpl, TradeDecisionRejection
from strategy.confirmation.confirmation_engine import ConfirmationEngineImpl
from strategy.risk.risk_engine import RiskEngineImpl
from strategy.scoring.scoring_engine import ScoringEngineImpl, ScoringFactors
from strategy.trailing.trailing_engine import TrailingEngineImpl


@dataclass(frozen=True)
class BacktestRunConfig:
    symbol: str
    tick_size: Decimal
    tick_value: Decimal
    volume_step: Decimal
    min_volume: Decimal
    max_volume: Decimal
    minimum_stop_atr: Decimal  # unused directly here; kept for future stop-buffer wiring
    tp1_allocation_pct: Decimal  # sourced from core.config.registry.StrategyConfig — never hardcoded here


class BacktestEngine:
    def __init__(
        self,
        confirmation_engine: ConfirmationEngineImpl,
        scoring_engine: ScoringEngineImpl,
        risk_engine: RiskEngineImpl,
        decision_generator: TradeDecisionGeneratorImpl,
        trailing_engine: TrailingEngineImpl,
        adapter: BacktestExecutionAdapter,
        run_config: BacktestRunConfig,
    ) -> None:
        self._confirmation = confirmation_engine
        self._scoring = scoring_engine
        self._risk = risk_engine
        self._decision_gen = decision_generator
        self._trailing = trailing_engine
        self._adapter = adapter
        self._config = run_config

    def run_setup(
        self,
        setup: Setup,
        zone: Zone,
        candles: Sequence[Candle],
        atr_by_candle_id: dict[str, Decimal],
        take_profit_1: Decimal,
        take_profit_2: Decimal,
        scoring_factors: ScoringFactors,
        risk_snapshot: RiskSnapshot,
        htf_context_still_valid: bool,
    ) -> Optional[TradeRecord]:
        """`candles` is every M5 candle visible from setup creation
        through the end of available replay data — the CALLER is
        responsible for slicing this via CandleStore.get_up_to() at
        whatever the current replay point is; this method never reaches
        outside what it's given."""

        touch = self._confirmation.process_touch(
            touch_id=f"touch:{setup.setup_id}:1", setup_id=setup.setup_id,
            zone=zone, direction=setup.direction, candles=list(candles),
            atr_by_candle_id=atr_by_candle_id,
            available_at=setup.created_at,  # PHASE-A P0.1: causality boundary
        )

        if touch is None or touch.outcome != TouchOutcome.CONFIRMED:
            # Not confirmed with the data available — check lifecycle
            # against the LATEST visible candle only (never anything
            # beyond what's actually in `candles`).
            if candles:
                latest = candles[-1]
                lifecycle_result = evaluate_setup_lifecycle(
                    setup, at=latest.timestamp_close, latest_close=latest.close
                )
                if lifecycle_result.state in (SetupState.EXPIRED, SetupState.INVALIDATED):
                    return None  # no trade — setup died before confirming
            return None  # still pending, not yet resolved either way

        # CONFIRMED — entry = market order at M5 confirmation candle close
        break_candle = next(c for c in candles if c.candle_id == touch.break_candle_id)
        entry_price = break_candle.close
        stop_loss = setup.invalidation_level

        if not htf_context_still_valid:
            return None  # hard gate failed — no trade, matches Setup FROZEN v1.0 T12

        sizing = self._risk.compute_position_size(
            account_equity=risk_snapshot.account_equity, entry=entry_price, stop=stop_loss,
            tick_size=self._config.tick_size, tick_value=self._config.tick_value,
            volume_step=self._config.volume_step, min_volume=self._config.min_volume,
            max_volume=self._config.max_volume,
        )
        risk_decision = self._risk.evaluate_gates(risk_snapshot, sizing)

        score, _ = self._scoring.score(scoring_factors)
        # Setup.score should already equal this (computed once at
        # creation, per Implementation Spec v1.0 §5) — this call is for
        # generating a comparable value here, not recomputing the
        # setup's own immutable score.

        decision_result = self._decision_gen.generate(
            setup=setup, symbol=self._config.symbol, entry=entry_price, stop_loss=stop_loss,
            take_profit_1=take_profit_1, take_profit_2=take_profit_2,
            tp1_allocation_pct=self._config.tp1_allocation_pct, htf_context_still_valid=htf_context_still_valid,
            risk_decision=risk_decision, position_size=sizing.rounded_volume,
            risk_amount=sizing.risk_amount, risk_percentage=sizing.risk_percentage_actual,
            decision_timestamp=break_candle.timestamp_close, environment=Environment.BACKTEST,
        )

        if isinstance(decision_result, TradeDecisionRejection):
            return None

        decision = decision_result
        order = self._adapter.submit_market_order(decision)

        # Only candles STRICTLY AFTER entry can determine the exit —
        # the entry candle itself is excluded.
        entry_index = candles.index(break_candle)
        post_entry_candles = list(candles[entry_index + 1 :])

        exit_check = find_first_exit(
            setup.direction, stop_loss, take_profit_1, take_profit_2, post_entry_candles
        )

        if exit_check is None:
            return TradeRecord(
                trade_id=f"trade:{decision.decision_id}", decision_id=decision.decision_id,
                setup_id=setup.setup_id, config_hash=decision.config_hash, direction=setup.direction,
                entry_timestamp=break_candle.timestamp_close, entry_price=entry_price,
                stop_loss=stop_loss, take_profit_1=take_profit_1, take_profit_2=take_profit_2,
                exit_timestamp=None, exit_price=None, exit_reason="OPEN", realized_r=None,
            )

        if exit_check.reason != "TP1_HIT":
            # Hit SL or TP2 directly — done, no trailing phase entered.
            realized_r = compute_realized_r(setup.direction, entry_price, stop_loss, exit_check.exit_price)
            exit_candle = next(c for c in post_entry_candles if c.candle_id == exit_check.candle_id)
            return TradeRecord(
                trade_id=f"trade:{decision.decision_id}", decision_id=decision.decision_id,
                setup_id=setup.setup_id, config_hash=decision.config_hash, direction=setup.direction,
                entry_timestamp=break_candle.timestamp_close, entry_price=entry_price,
                stop_loss=stop_loss, take_profit_1=take_profit_1, take_profit_2=take_profit_2,
                exit_timestamp=exit_candle.timestamp_close, exit_price=exit_check.exit_price,
                exit_reason=exit_check.reason, realized_r=realized_r,
            )

        # TP1 hit — enter the post-TP1 trailing phase (simplified: one
        # trailing recomputation, see module docstring).
        tp1_index = post_entry_candles.index(
            next(c for c in post_entry_candles if c.candle_id == exit_check.candle_id)
        )
        post_tp1_candles = post_entry_candles[tp1_index + 1 :]

        trailing_sl = stop_loss
        if post_tp1_candles:
            current_atr = atr_by_candle_id.get(post_tp1_candles[-1].candle_id)
            if current_atr is not None:
                new_sl = self._trailing.compute_new_stop(
                    setup.direction, trailing_sl, post_tp1_candles, atr_by_candle_id, current_atr,
                )
                if new_sl is not None:
                    trailing_sl = new_sl

        second_leg_exit = find_first_exit(
            setup.direction, trailing_sl, None, take_profit_2, post_tp1_candles
        )

        if second_leg_exit is None:
            return TradeRecord(
                trade_id=f"trade:{decision.decision_id}", decision_id=decision.decision_id,
                setup_id=setup.setup_id, config_hash=decision.config_hash, direction=setup.direction,
                entry_timestamp=break_candle.timestamp_close, entry_price=entry_price,
                stop_loss=trailing_sl, take_profit_1=take_profit_1, take_profit_2=take_profit_2,
                exit_timestamp=None, exit_price=None, exit_reason="OPEN", realized_r=None,
            )

        reason = "TRAILING_STOP_HIT" if second_leg_exit.reason == "SL_HIT" else second_leg_exit.reason
        realized_r = compute_realized_r(setup.direction, entry_price, stop_loss, second_leg_exit.exit_price)
        exit_candle = next(c for c in post_tp1_candles if c.candle_id == second_leg_exit.candle_id)
        return TradeRecord(
            trade_id=f"trade:{decision.decision_id}", decision_id=decision.decision_id,
            setup_id=setup.setup_id, config_hash=decision.config_hash, direction=setup.direction,
            entry_timestamp=break_candle.timestamp_close, entry_price=entry_price,
            stop_loss=trailing_sl, take_profit_1=take_profit_1, take_profit_2=take_profit_2,
            exit_timestamp=exit_candle.timestamp_close, exit_price=second_leg_exit.exit_price,
            exit_reason=reason, realized_r=realized_r,
        )

"""End-to-end research backtest runner (PHASE A rebuild).

Wires the full, real, unmocked path:

    Tickstory CSV -> streaming ticks -> streaming M1 -> streaming M5/H4
    fan-out -> CandleStore -> ReplayEngine -> ReplayPipeline -> TradeRecord /
    ExecutionEvent output, written incrementally to disk.

PHASE A CHANGES (see CHANGELOG_PHASE_A.md):

* SINGLE CONFIGURATION AUTHORITY (audit P0.5). Every trading-rule parameter
  comes from core.config.registry.EngineConfig. The CLI owns NO trading-rule
  defaults: every strategy/risk flag defaults to None, meaning "use the
  registry value". An override must be passed explicitly, is applied BEFORE
  the config hash is computed, is printed, and is recorded in
  run_metadata.json. (Previously --minimum-rr silently defaulted to 1.5
  while the registry said 3.8.)
* REAL ACCOUNT LEDGER (audit P0.3): balance, equity, daily P&L, trades-today
  and the consecutive-loss lock all evolve from realized (partial and final)
  trade P&L; the 2% daily-loss gate can now trip. Day boundary = 00:00 UTC.
  Sizing mode FIXED_RISK (research default) or COMPOUNDING, from the config.
* CHRONOLOGICAL WARM-UP (audit 3.2): when --window-start is given, the runner
  also loads --warmup-days of data BEFORE it and replays forward; no trade may
  open before the window start (setups confirming in warm-up are rejected
  with WARMUP_PERIOD). The initial H4 bias is a neutral seed taken from the
  start of the warm-up and is then advanced chronologically by the real H4
  structure engine, instead of being voted from the first 10 candles of the
  test window.
* ENGINE MODE: --engine FAST (default; incremental, disclosed cutoffs) or
  REFERENCE (canonical, exact, slow).
* RUN METADATA: run_metadata.json holds the effective config (full
  human-readable snapshot + sha256 hash), CLI overrides, engine mode, window,
  warm-up, cost model, data-file identity and ingestion report.

Usage:
    python scripts/run_backtest.py "<csv_path>" \\
        --window-start "2025-09-01 00:00:00" --window-end "2026-08-31 23:59:59" \\
        --warmup-days 30 --engine REFERENCE --output-dir run_A1

Omit --window-start/--window-end to process the entire file (no warm-up is
possible then; the first candles of the file serve as implicit warm-up and
this is recorded in the metadata).
"""

from __future__ import annotations

import argparse
import hashlib
import platform
import dataclasses
import json
import sys
import time
import traceback
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from enum import Enum
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backtest.accounting import AccountLedger, SizingMode
from backtest.execution_adapter import BacktestExecutionAdapter
from backtest.pipeline import ReplayPipeline, RunConfig
from backtest.telemetry import Telemetry
from backtest.statistics import check_plausibility, compute_outlier_robustness, compute_statistics
from backtest.trade_log import TradeRecord
from core.config.registry import EngineConfig
from core.domain.common import Timeframe
from core.domain.structure import H4BiasStateValue
from decision.trade_decision_generator import TradeDecisionGeneratorImpl
from market_data.aggregation.timeframe_aggregator import stream_aggregate_multi
from market_data.candle_store.store import CandleStore
from market_data.ingestion.tickstory_reader import IngestionReport, stream_tickstory_csv
from market_data.replay.replay_engine import ReplayEngine
from market_data.ticks.tick_aggregator import stream_ticks_to_m1
from strategy.confirmation.confirmation_engine import ConfirmationEngineImpl
from strategy.displacement.displacement_engine import DisplacementEngineImpl
from strategy.indicators.atr import DEFAULT_ATR_PERIOD, atr_provider_from_period
from strategy.liquidity.liquidity_engine import LiquidityEngineImpl
from strategy.risk.risk_engine import RiskEngineImpl
from strategy.scoring.scoring_engine import ScoringEngineImpl
from strategy.setups.setup_engine import SetupAdmissionEngine
from strategy.structure.bos_choch_detector import StructureDetectorImpl
from strategy.structure.h4_warning_engine import H4WarningEngineImpl
from strategy.swings.swing_detector import SwingDetectorImpl
from strategy.trailing.trailing_engine import TrailingEngineImpl
from strategy.zones.zone_engine import ZoneEngineImpl


def _json_default(obj):
    if isinstance(obj, Decimal):
        return str(obj)
    if isinstance(obj, datetime):
        return obj.isoformat()
    if isinstance(obj, Enum):
        return obj.value
    raise TypeError(f"not JSON serializable: {type(obj)}")


# ----------------------------------------------------------------------
# Configuration: registry is the ONLY source of trading parameters
# ----------------------------------------------------------------------

# CLI flag (argparse dest) -> (config section, parameter name, converter).
# Every one defaults to None == "use the registry value".
OVERRIDABLE = {
    "minimum_rr": ("strategy", "minimum_rr", Decimal),
    "minimum_score": ("strategy", "minimum_score", int),
    "risk_pct": ("risk", "risk_per_trade_pct", Decimal),
    "sizing_mode": ("risk", "sizing_mode", str),
    "touch_timeout_minutes": ("strategy", "touch_confirmation_timeout_minutes", int),
    "sweep_displacement_max_age_candles": ("strategy", "sweep_to_displacement_max_age_candles", int),
}


def apply_cli_overrides(config: EngineConfig, args) -> dict:
    """Apply explicitly-passed overrides to ``config`` (in place) and return
    {param: {"registry": old, "override": new}} for the metadata/log. Anything
    not passed leaves the registry value untouched."""
    applied: dict = {}
    for dest, (section, name, conv) in OVERRIDABLE.items():
        raw = getattr(args, dest, None)
        if raw is None:
            continue
        param = getattr(getattr(config, section), name)
        new = conv(raw)
        if dest == "sizing_mode":
            new = SizingMode(new).value  # validates
        applied[f"{section}.{name}"] = {"registry": str(param.value), "override": str(new)}
        param.value = new
    return applied


def make_ledger(config: EngineConfig, initial_equity: Decimal) -> AccountLedger:
    return AccountLedger(
        initial_balance=initial_equity,
        sizing_mode=SizingMode(config.risk.sizing_mode.value),
        reset_consecutive_losses_at_utc_midnight=config.risk.reset_consecutive_losses_at_utc_midnight.value,
    )


def _detect_initial_h4_bias(
    candle_store: CandleStore, symbol: str, warmup_candles: int = 10,
) -> H4BiasStateValue:
    """Neutral SEED for the H4 state machine: majority direction of the first
    ``warmup_candles`` H4 bars of the LOADED data. With a pre-window warm-up
    loaded (the default when --window-start is given) these bars are the start
    of the warm-up, not the start of the test window, and the real H4
    structure engine then advances the state chronologically across the whole
    warm-up before trading is allowed (audit 3.2)."""
    from core.domain.common import Timeframe as TF
    h4s = candle_store.get_all(symbol, TF.H4) if hasattr(candle_store, "get_all") else []
    if not h4s:
        from datetime import timezone as _tz
        h4s = candle_store.get_up_to(symbol, TF.H4, datetime(2099, 1, 1, tzinfo=_tz.utc))
    if not h4s:
        return H4BiasStateValue.BULLISH  # no data: documented default
    sample = h4s[:warmup_candles]
    bullish = sum(1 for c in sample if c.close >= c.open)
    return H4BiasStateValue.BULLISH if bullish >= len(sample) - bullish else H4BiasStateValue.BEARISH


def build_pipeline(
    candle_store: CandleStore, symbol: str, config: EngineConfig, ledger: AccountLedger,
    initial_h4_bias: H4BiasStateValue = H4BiasStateValue.BULLISH,
    cost_model: str = "FRICTIONLESS", spread: Decimal = Decimal("0"), slippage: Decimal = Decimal("0"),
    commission_per_lot: Decimal = Decimal("0"), m5_engine: str = "FAST",
    trading_start: Optional[datetime] = None, tick_window_provider=None, telemetry=None,
) -> ReplayPipeline:
    """Constructs the real pipeline from ONE EngineConfig. Nothing here reads
    a trading parameter from anywhere except ``config``."""
    run_config = RunConfig(
        tick_size=Decimal("0.01"), tick_value=Decimal("1.00"), volume_step=Decimal("0.01"),
        min_volume=Decimal("0.01"), max_volume=Decimal("100"),
    )
    s, r = config.strategy, config.risk
    return ReplayPipeline(
        symbol=symbol, candle_store=candle_store, config=config, run_config=run_config,
        swing_detector=SwingDetectorImpl(major_swing_atr_multiple=s.major_swing_atr.value),
        structure_detector=StructureDetectorImpl(),
        h4_warning_engine=H4WarningEngineImpl(
            choch_warning_max_h4_candles=s.choch_warning_max_candles.value,
            h4_candle_duration=timedelta(hours=4),
        ),
        liquidity_engine=LiquidityEngineImpl(),
        displacement_engine=DisplacementEngineImpl(
            body_median_multiple=s.displacement_body_median_multiple.value,
            range_atr_multiple=s.displacement_range_atr.value,
        ),
        zone_engine=ZoneEngineImpl(),
        setup_admission_engine=SetupAdmissionEngine(max_pending_setups=s.max_pending_setups.value),
        confirmation_engine=ConfirmationEngineImpl(min_retracement_atr_mult=s.micro_swing_min_retracement_atr.value),
        scoring_engine=ScoringEngineImpl(weights=s.scoring_weights),
        risk_engine=RiskEngineImpl(
            risk_per_trade_pct=r.risk_per_trade_pct.value, daily_loss_limit_pct=r.daily_loss_limit_pct.value,
            max_consecutive_losses=r.max_consecutive_losses.value, max_trades_per_day=r.max_trades_per_day.value,
        ),
        decision_generator=TradeDecisionGeneratorImpl(
            strategy_version="v1-phaseA", config_hash=config.config_hash(),
            minimum_rr=s.minimum_rr.value, minimum_score=s.minimum_score.value,
            decision_expiry_seconds=config.execution.decision_expiry_seconds.value,
            max_stop_atr=s.max_stop_atr.value, min_stop_atr=s.minimum_stop_atr.value,
        ),
        trailing_engine=TrailingEngineImpl(
            trailing_sl_buffer_atr=s.trailing_sl_buffer_atr.value, major_swing_atr_multiple=s.major_swing_atr.value,
        ),
        execution_adapter=BacktestExecutionAdapter(
            cost_model=cost_model, spread=spread, slippage=slippage, commission_per_lot=commission_per_lot,
        ),
        atr_provider=atr_provider_from_period(DEFAULT_ATR_PERIOD),  # ATR-01, LOCKED
        account_ledger=ledger,
        tp_provider=None,  # real target_selector.py
        genesis_at=datetime(1970, 1, 1, tzinfo=timezone.utc),
        initial_h4_bias=initial_h4_bias, m5_engine=m5_engine,
        trading_start=trading_start, tick_window_provider=tick_window_provider, telemetry=telemetry,
    )


def build_run_metadata(config: EngineConfig, overrides: dict, args, window_start, window_end,
                       trading_start, warmup_start, csv_path) -> dict:
    p = Path(csv_path)
    return {
        "schema": "run_metadata/v1-phaseA",
        "started_at_utc": datetime.now(timezone.utc).isoformat(),
        "config_hash": config.config_hash(),
        "effective_config": config.model_dump(mode="json"),
        "cli_overrides_applied": overrides,
        "engine": args.engine,
        "engine_status": ("CANONICAL (REFERENCE)" if args.engine == "REFERENCE"
                          else "NON-CANONICAL (FAST: disclosed cutoffs, equivalence not demonstrated on real data)"),
        "research_path": "backtest.pipeline.ReplayPipeline (NOT the legacy backtest.engine)",
        "sizing_mode": config.risk.sizing_mode.value,
        "initial_equity": str(args.initial_equity),
        "cost_model": args.cost_model,
        "cost_inputs": {"spread": args.spread, "slippage": args.slippage, "commission_per_lot": args.commission_per_lot},
        "window_start": window_start.isoformat() if window_start else None,
        "window_end": window_end.isoformat() if window_end else None,
        "warmup_days": args.warmup_days if window_start else None,
        "data_loaded_from": warmup_start.isoformat() if warmup_start else None,
        "trading_start": trading_start.isoformat() if trading_start else None,
        "warmup_note": (None if window_start else
                        "No --window-start: the whole file is replayed and the first candles act as implicit warm-up; "
                        "the H4 seed comes from the first 10 H4 candles of the file."),
        "data_file": {"path": str(p), "name": p.name, "size_bytes": (p.stat().st_size if p.exists() else None)},
        "python": platform.python_version(),
        "execution_model": "OHLC (SL-first on intrabar ambiguity); tick execution NOT used by this runner yet",
        "validation_status": "NOT VALIDATED: Phase A baseline; no real-tick execution validation performed",
    }


def _parse_dt(raw: str) -> datetime:
    return datetime.strptime(raw, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("csv_path")
    parser.add_argument("--symbol", default="XAUUSD")
    parser.add_argument("--window-start", default=None, help="YYYY-MM-DD HH:MM:SS (UTC); omit for whole file")
    parser.add_argument("--window-end", default=None, help="YYYY-MM-DD HH:MM:SS (UTC); omit for whole file")
    parser.add_argument("--warmup-days", type=int, default=30,
                        help="Days of data loaded BEFORE --window-start for chronological warm-up (no trading). Default 30.")
    parser.add_argument("--engine", default="REFERENCE", choices=["FAST", "REFERENCE"],
                        help="REFERENCE (default) = canonical exact engine (slow). FAST = incremental with disclosed "
                             "cutoffs; NON-CANONICAL until real-data equivalence is demonstrated.")
    parser.add_argument("--initial-equity", type=str, default="10000", help="Reference/initial account balance")
    parser.add_argument("--output-dir", default="research_round_1")
    parser.add_argument("--progress-seconds", type=float, default=15.0)
    parser.add_argument("--no-telemetry", action="store_true",
                        help="E0: disable candidate-level telemetry (candidates.jsonl, h4_transitions.jsonl, funnel_counters.json). "
                             "Telemetry is observation-only and never changes events/trades/statistics.")
    parser.add_argument("--stats-every-n-trades", type=int, default=25)
    parser.add_argument("--cost-model", default="FRICTIONLESS", choices=["FRICTIONLESS", "REALISTIC"])
    parser.add_argument("--spread", type=str, default="0", help="[REALISTIC] price points; SYNTHETIC default 0")
    parser.add_argument("--slippage", type=str, default="0", help="[REALISTIC] price points; SYNTHETIC default 0")
    parser.add_argument("--commission-per-lot", type=str, default="0", help="[REALISTIC] per lot one-way; SYNTHETIC default 0")
    # ---- trading-rule overrides: default None == registry value. -------------
    parser.add_argument("--minimum-rr", dest="minimum_rr", default=None,
                        help="OVERRIDE registry minimum blended RR (registry default applies if omitted)")
    parser.add_argument("--minimum-score", dest="minimum_score", type=int, default=None,
                        help="OVERRIDE registry minimum score (registry default applies if omitted)")
    parser.add_argument("--risk-pct", dest="risk_pct", default=None,
                        help="OVERRIDE risk %% per trade, in percent (0.5 == 0.5%%); registry default applies if omitted")
    parser.add_argument("--sizing-mode", dest="sizing_mode", default=None, choices=["FIXED_RISK", "COMPOUNDING"],
                        help="OVERRIDE registry sizing mode (default FIXED_RISK)")
    parser.add_argument("--touch-timeout-minutes", dest="touch_timeout_minutes", type=int, default=None,
                        help="PHASE B experiment only; 0/omitted = disabled")
    parser.add_argument("--sweep-displacement-max-age-candles", dest="sweep_displacement_max_age_candles", type=int,
                        default=None, help="PHASE B experiment only; 0/omitted = disabled")
    return parser


def main(argv=None) -> int:
    args = build_arg_parser().parse_args(argv)

    window_start = _parse_dt(args.window_start) if args.window_start else None
    window_end = _parse_dt(args.window_end) if args.window_end else None
    initial_equity = Decimal(args.initial_equity)

    costs_nonzero = any(Decimal(v) != 0 for v in (args.spread, args.slippage, args.commission_per_lot))
    if args.cost_model == "FRICTIONLESS" and costs_nonzero:
        print("ERROR: --spread/--slippage/--commission-per-lot are non-zero but --cost-model is FRICTIONLESS. "
              "Use --cost-model REALISTIC (costs are never applied silently or ignored silently).")
        return 2

    config = EngineConfig()                      # registry defaults ...
    overrides = apply_cli_overrides(config, args)  # ... plus explicit overrides ONLY
    config_hash = config.config_hash()

    warmup_start = (window_start - timedelta(days=args.warmup_days)) if window_start else None
    trading_start = window_start  # no trades before the requested window

    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    trades_path, events_path = out_dir / "trades.jsonl", out_dir / "events.jsonl"
    stats_path, run_log_path = out_dir / "statistics_partial.json", out_dir / "run_log.txt"

    def log(msg: str) -> None:
        line = f"[{datetime.now(timezone.utc).isoformat()}] {msg}"
        print(line); sys.stdout.flush()
        with open(run_log_path, "a", encoding="utf-8") as f:
            f.write(line + "\n")

    metadata = build_run_metadata(config, overrides, args, window_start, window_end, trading_start, warmup_start, args.csv_path)
    (out_dir / "run_metadata.json").write_text(json.dumps(metadata, default=_json_default, indent=2), encoding="utf-8")

    log(f"Starting backtest. CSV={args.csv_path} window=({window_start} -> {window_end}) "
        f"warmup_from={warmup_start} trading_start={trading_start} engine={args.engine}")
    log(f"EFFECTIVE CONFIG HASH: {config_hash}")
    log(f"ENGINE: {args.engine} ({'CANONICAL' if args.engine == 'REFERENCE' else 'NON-CANONICAL / optimisation candidate'}); "
        f"research path = ReplayPipeline")
    log(f"minimum_rr={config.strategy.minimum_rr.value} minimum_score={config.strategy.minimum_score.value} "
        f"risk_per_trade_pct={config.risk.risk_per_trade_pct.value} sizing_mode={config.risk.sizing_mode.value} "
        f"tp1_allocation_pct={config.strategy.tp1_allocation_pct.value} min_stop_atr={config.strategy.minimum_stop_atr.value}")
    if overrides:
        for k, v in overrides.items():
            log(f"*** CLI OVERRIDE of registry value: {k}: {v['registry']} -> {v['override']}")
    else:
        log("No CLI overrides: running exactly the registry configuration.")
    if window_start is None:
        log("WARNING: no --window-start; no pre-window warm-up possible (see run_metadata.json warmup_note).")
    log("EXECUTION MODEL: OHLC with SL-first intrabar assumption. Not tick-validated.")
    if args.cost_model == "REALISTIC":
        log("[COST MODEL] REALISTIC: spread/slippage/commission defaults are SYNTHETIC placeholders (0) unless supplied.")

    ingestion_report = IngestionReport()
    store = CandleStore()
    t_start = time.perf_counter()
    m5_count = h4_count = completed_trade_count = total_events = 0
    trades_file = open(trades_path, "a", encoding="utf-8")
    events_file = open(events_path, "a", encoding="utf-8")
    pipeline = None
    telemetry = None if args.no_telemetry else Telemetry()
    ledger = make_ledger(config, initial_equity)
    rejection_counts: dict = {}

    try:
        log("Streaming CSV -> ticks -> M1 -> M5/H4 fan-out ...")
        raw_ticks = stream_tickstory_csv(
            args.csv_path, args.symbol, report=ingestion_report,
            not_before=warmup_start, not_after=window_end,
        )
        m1_stream = stream_ticks_to_m1(raw_ticks, args.symbol)
        last_progress, replay_steps = t_start, 0
        for tf, candle in stream_aggregate_multi(m1_stream, [Timeframe.M5, Timeframe.H4], args.symbol):
            store.add(candle)
            if tf == Timeframe.M5:
                m5_count += 1
            else:
                h4_count += 1
            now = time.perf_counter()
            if now - last_progress >= args.progress_seconds:
                log(f"progress: elapsed={now - t_start:.0f}s valid_ticks={ingestion_report.valid_ticks:,} "
                    f"M5={m5_count:,} H4={h4_count:,}")
                last_progress = now
        log(f"Streaming complete. M5={m5_count:,} H4={h4_count:,} valid_ticks={ingestion_report.valid_ticks:,} "
            f"invalid_rows={ingestion_report.skipped_rows}")

        initial_bias = _detect_initial_h4_bias(store, args.symbol)
        log(f"Initial H4 seed (start of loaded data): {initial_bias.value}")

        pipeline = build_pipeline(
            store, args.symbol, config, ledger, initial_h4_bias=initial_bias,
            cost_model=args.cost_model, spread=Decimal(args.spread), slippage=Decimal(args.slippage),
            commission_per_lot=Decimal(args.commission_per_lot), m5_engine=args.engine,
            trading_start=trading_start, telemetry=telemetry,
        )

        log("Starting ReplayPipeline ...")
        replay = ReplayEngine(store, args.symbol, Timeframe.M5)
        for step in replay.run():
            result = pipeline.step(step.at, step.candles)
            replay_steps += 1
            total_events += len(result.events)
            for event in result.events:
                if event.event_type.value == "TRADE_REJECTED":
                    rc = (event.payload or {}).get("reason_code", "UNKNOWN")
                    rejection_counts[rc] = rejection_counts.get(rc, 0) + 1
                events_file.write(json.dumps(event.model_dump(mode="json"), default=_json_default) + "\n")
            if result.events:
                events_file.flush()
            if result.trade is not None:
                completed_trade_count += 1
                trades_file.write(json.dumps(dataclasses.asdict(result.trade), default=_json_default) + "\n")
                trades_file.flush()
                if completed_trade_count % args.stats_every_n_trades == 0:
                    with open(stats_path, "w", encoding="utf-8") as f:
                        json.dump(dataclasses.asdict(compute_statistics(pipeline.state.completed_trades)), f,
                                  default=_json_default, indent=2)
            now = time.perf_counter()
            if now - last_progress >= args.progress_seconds:
                log(f"progress: elapsed={now - t_start:.0f}s replay_steps={replay_steps:,} "
                    f"trades={completed_trade_count:,} balance={ledger.balance}")
                last_progress = now
        log("Replay complete.")

    except KeyboardInterrupt:
        log("Stopped by Ctrl+C -- partial results are on disk.")
    except Exception:
        log("ERROR -- backtest run failed with an exception:")
        log(traceback.format_exc())
        trades_file.close(); events_file.close()
        return 1
    finally:
        trades_file.close(); events_file.close()

    if pipeline is not None and telemetry is not None:
        manifest = telemetry.write(out_dir, extra_manifest={"config_hash": config_hash, "engine": args.engine,
                                                            "code_marker": "E0 (observation-only telemetry)"})
        log(f"TELEMETRY: {manifest['candidate_rows']} candidate rows, {manifest['h4_log_rows']} H4 log rows, "
            f"errors={manifest['telemetry_error_count']} (files: candidates.jsonl, h4_transitions.jsonl, funnel_counters.json)")

    if pipeline is not None:
        final_stats = compute_statistics(pipeline.state.completed_trades)
        with open(out_dir / "statistics_final.json", "w", encoding="utf-8") as f:
            json.dump(dataclasses.asdict(final_stats), f, default=_json_default, indent=2)
        robustness = compute_outlier_robustness(pipeline.state.completed_trades, top_n_list=(1, 2))
        with open(out_dir / "statistics_robustness.json", "w", encoding="utf-8") as f:
            json.dump({k: dataclasses.asdict(v) for k, v in robustness.items()}, f, default=_json_default, indent=2)
        from scripts.baseline_report import write_trade_ledger_csv
        write_trade_ledger_csv(out_dir / "trades.jsonl", out_dir / "trade_ledger.csv")
        warnings = check_plausibility(pipeline.state.completed_trades)
        with open(out_dir / "plausibility_report.json", "w", encoding="utf-8") as f:
            json.dump({"flagged_trade_count": len(warnings), "warnings": [dataclasses.asdict(w) for w in warnings]},
                      f, default=_json_default, indent=2)
        if warnings:
            log(f"*** PLAUSIBILITY GUARD: {len(warnings)} trade(s) flagged -- see plausibility_report.json ***")
        else:
            log("Plausibility guard: no flagged trades.")
        metadata["finished_at_utc"] = datetime.now(timezone.utc).isoformat()
        metadata["result_summary"] = {
            "m5_candles_loaded": m5_count, "h4_candles_loaded": h4_count,
            "valid_ticks": ingestion_report.valid_ticks, "invalid_rows": ingestion_report.skipped_rows,
            "completed_trades": completed_trade_count,
            "account": {
                "initial_balance": str(ledger.initial_balance), "final_balance": str(ledger.balance),
                "realized_pnl_total": str(ledger.realized_pnl_total),
                "max_drawdown_abs": str(ledger.max_drawdown_abs), "max_drawdown_pct": str(ledger.max_drawdown_pct),
            },
            "ohlc_ambiguous_candles_while_in_position": pipeline.ohlc_ambiguous_candles,
            "trade_rejection_counts": rejection_counts,
        }
        (out_dir / "run_metadata.json").write_text(json.dumps(metadata, default=_json_default, indent=2), encoding="utf-8")

    log(f"DONE in {time.perf_counter() - t_start:.0f}s. trades={completed_trade_count:,} events={total_events:,} "
        f"final_balance={ledger.balance} max_dd%={ledger.max_drawdown_pct:.2f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

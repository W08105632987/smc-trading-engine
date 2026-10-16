"""Run ONE scenario through a given project root and dump canonical outputs.
Usage: run_scenario.py <root> <csv> <engine FAST|REFERENCE> <preset default|relaxed|relaxed_costs> <telemetry on|off> <outdir> [days_warmup_hours]
Works on BOTH the original and the E0 root (telemetry is only passed when the root supports it)."""
import sys, os, json, inspect, dataclasses, hashlib
from decimal import Decimal as D
from datetime import datetime, timedelta, timezone
root, csv_path, engine, preset, tel, outdir = sys.argv[1:7]
sys.path.insert(0, root); os.chdir(root)
sys.path.insert(0, "/home/claude/shim")
from core.config.registry import EngineConfig
from core.domain.common import Timeframe
from market_data.ingestion.tickstory_reader import stream_tickstory_csv, IngestionReport
from market_data.ticks.tick_aggregator import stream_ticks_to_m1
from market_data.aggregation.timeframe_aggregator import stream_aggregate_multi
from market_data.replay.replay_engine import ReplayEngine
from market_data.candle_store.store import CandleStore
import scripts.run_backtest as R

cfg = EngineConfig()
if preset in ("relaxed", "relaxed_costs"):
    cfg.strategy.minimum_rr.value = D("0.2")
    cfg.strategy.minimum_score.value = 0
    cfg.strategy.max_stop_atr.value = D("50")
    cfg.strategy.min_target_atr.value = D("0.2")
    cfg.strategy.minimum_stop_atr.value = D("0.01")
store = CandleStore(); rep = IngestionReport()
for tf, c in stream_aggregate_multi(stream_ticks_to_m1(stream_tickstory_csv(csv_path, "XAUUSD", report=rep), "XAUUSD"), [Timeframe.M5, Timeframe.H4], "XAUUSD"):
    store.add(c)
ledger = R.make_ledger(cfg, D("10000"))
kw = dict(m5_engine=engine)
if preset == "relaxed_costs":
    kw.update(cost_model="REALISTIC", spread=D("0.3"), slippage=D("0.1"), commission_per_lot=D("3.5"))
tel_obj = None
if tel == "on":
    from backtest.telemetry import Telemetry   # only exists in the E0 root
    tel_obj = Telemetry(strict=True)
    kw["telemetry"] = tel_obj
pipe = R.build_pipeline(store, "XAUUSD", cfg, ledger, initial_h4_bias=R._detect_initial_h4_bias(store, "XAUUSD"), **kw)
os.makedirs(outdir, exist_ok=True)
ev_f = open(f"{outdir}/events.jsonl", "w"); tr_f = open(f"{outdir}/trades.jsonl", "w")
n_steps = 0
for step in ReplayEngine(store, "XAUUSD", Timeframe.M5).run():
    res = pipe.step(step.at, step.candles); n_steps += 1
    for e in res.events: ev_f.write(json.dumps(e.model_dump(mode="json"), default=str, sort_keys=True) + "\n")
    if res.trade is not None: tr_f.write(json.dumps(dataclasses.asdict(res.trade), default=str, sort_keys=True) + "\n")
ev_f.close(); tr_f.close()
acct = dict(balance=str(ledger.balance), realized=str(ledger.realized_pnl_total), dd=str(ledger.max_drawdown_abs), trades=ledger.closed_trade_count,
            trades_today=ledger.trades_today, consec=ledger.consecutive_losses, peak=str(ledger.peak_equity))
summ = dict(steps=n_steps, engine=engine, preset=preset, account=acct, config_hash=cfg.config_hash(),
            state_final=dict(active=len(pipe.state.active_setups), completed=len(pipe.state.completed_trades),
                             h4=str(pipe.state.h4_bias_state.current_state), emitted_ids=len(pipe.state.emitted_event_ids)))
json.dump(summ, open(f"{outdir}/summary.json", "w"), indent=1, sort_keys=True)
if tel_obj is not None:
    man = tel_obj.write(outdir + "/telemetry")
    print("telemetry rows", man["candidate_rows"], "errors", man["telemetry_error_count"], dict(tel_obj.counters))
from collections import Counter
print("steps", n_steps, "trades", len(pipe.state.completed_trades), dict(Counter(json.loads(l)["event_type"] for l in open(f"{outdir}/events.jsonl"))))

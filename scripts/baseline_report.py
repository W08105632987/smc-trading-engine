"""Baseline report + trade-ledger tooling (PHASE A). Stdlib only.

    python scripts/baseline_report.py <run_output_dir>

Reads trades.jsonl / run_metadata.json written by scripts/run_backtest.py and
writes baseline_report.json + baseline_report.md + trade_ledger.csv into the
same directory. Pure post-processing: it never changes a trade, never
filters outliers, and states what it cannot compute rather than guessing.
All money maths uses Decimal.
"""

from __future__ import annotations

import csv
import json
import sys
from collections import defaultdict
from datetime import datetime
from decimal import Decimal
from pathlib import Path

D = Decimal
LEDGER_COLUMNS = [
    "trade_id", "engine", "config_hash", "direction", "setup_id", "zone_creation_timestamp", "setup_created_at",
    "setup_expiry_at", "touch_started_at", "confirmation_timestamp", "entry_timestamp", "entry_price", "stop_loss",
    "take_profit_1", "take_profit_2", "position_size", "tp1_volume", "runner_volume", "tp1_executed",
    "tp1_split_reason", "tp1_exit_timestamp", "tp1_exit_price", "exit_timestamp", "exit_price", "exit_reason",
    "gross_pnl", "commission", "net_pnl", "dollar_risk", "realized_r", "cost_model", "spread_assumption",
    "slippage_assumption", "commission_per_lot_assumption", "balance_before", "balance_after", "setup_score",
    "entry_reason",
]


def load_trades(path) -> list[dict]:
    p = Path(path)
    if not p.exists():
        return []
    return [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines() if l.strip()]


def write_trade_ledger_csv(trades_jsonl, out_csv) -> int:
    trades = load_trades(trades_jsonl)
    with open(out_csv, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=LEDGER_COLUMNS, extrasaction="ignore")
        w.writeheader()
        for t in trades:
            w.writerow({k: ("" if t.get(k) is None else t.get(k)) for k in LEDGER_COLUMNS})
    return len(trades)


def _d(x):
    return D(str(x)) if x not in (None, "") else None


def _ts(x):
    return datetime.fromisoformat(x)


def compute_report(trades: list[dict], metadata: dict) -> dict:
    n = len(trades)
    rep: dict = {"trade_count": n}
    if n == 0:
        rep["note"] = "ZERO completed trades: no performance statistics are defined. Nothing was relaxed to manufacture trades."
        return rep
    r = [_d(t["realized_r"]) for t in trades]
    pnl = [_d(t["net_pnl"]) if t.get("net_pnl") is not None else None for t in trades]
    have_pnl = all(p is not None for p in pnl)
    wins = [x for x in r if x > 0]
    losses = [x for x in r if x <= 0]
    longs = [t for t in trades if t["direction"] == "BULLISH"]
    shorts = [t for t in trades if t["direction"] == "BEARISH"]

    def mean(xs):
        return (sum(xs, D(0)) / len(xs)) if xs else None

    gp = sum((p for p in pnl if p and p > 0), D(0)) if have_pnl else None
    gl = -sum((p for p in pnl if p and p <= 0), D(0)) if have_pnl else None
    rep.update({
        "long_trades": len(longs), "short_trades": len(shorts),
        "winners": len(wins), "losers": len(losses), "win_rate": str(D(len(wins)) / D(n)),
        "avg_winner_R": str(mean(wins)) if wins else None, "avg_loser_R": str(mean(losses)) if losses else None,
        "expectancy_R_per_trade": str(mean(r)),
        "profit_factor": (str(gp / gl) if (gl and gl > 0) else None) if have_pnl else None,
        "gross_profit": str(gp) if gp is not None else None, "gross_loss": str(gl) if gl is not None else None,
        "net_pnl": str(sum(pnl, D(0))) if have_pnl else None,
        "total_R": str(sum(r, D(0))),
    })
    # streaks
    run = best = 0
    for x in r:
        run = run + 1 if x <= 0 else 0
        best = max(best, run)
    rep["max_consecutive_losses_observed"] = best
    # drawdown from the ledger's own balances when present, else R-curve
    bals = [_d(t.get("balance_after")) for t in trades]
    if all(b is not None for b in bals) and metadata.get("result_summary", {}).get("account"):
        rep["max_drawdown_pct_account_ledger"] = metadata["result_summary"]["account"]["max_drawdown_pct"]
        rep["max_drawdown_abs_account_ledger"] = metadata["result_summary"]["account"]["max_drawdown_abs"]
    peak = cur = dd = D(0)
    for x in r:
        cur += x
        peak = max(peak, cur)
        dd = max(dd, peak - cur)
    rep["max_drawdown_R_closed_trade_curve"] = str(dd)
    # frequency
    days = {_ts(t["entry_timestamp"]).date() for t in trades}
    rep["distinct_trading_days"] = len(days)
    months = defaultdict(lambda: {"trades": 0, "R": D(0), "pnl": D(0)})
    for t in trades:
        m = _ts(t["entry_timestamp"]).strftime("%Y-%m")
        months[m]["trades"] += 1
        months[m]["R"] += _d(t["realized_r"])
        months[m]["pnl"] += (_d(t.get("net_pnl")) or D(0))
    rep["by_month"] = {k: {"trades": v["trades"], "R": str(v["R"]), "net_pnl": str(v["pnl"])} for k, v in sorted(months.items())}
    rep["trades_per_month_avg"] = str(D(n) / D(len(months)))
    # outcomes
    reasons = defaultdict(int)
    for t in trades:
        reasons[t["exit_reason"]] += 1
    rep["exit_reason_counts"] = dict(reasons)
    tp1_reached = [t for t in trades if t.get("tp1_executed") is not None]
    rep["tp1_reached_count"] = len(tp1_reached)
    rep["tp1_reached_frequency"] = str(D(len(tp1_reached)) / D(n))
    rep["tp1_split_reason_counts"] = dict(_count(t.get("tp1_split_reason") for t in tp1_reached))
    rep["stop_out_frequency"] = str(D(sum(1 for t in trades if t["exit_reason"] in ("SL_HIT",))) / D(n))
    rep["by_direction"] = {
        "long": {"trades": len(longs), "R": str(sum((_d(t["realized_r"]) for t in longs), D(0)))},
        "short": {"trades": len(shorts), "R": str(sum((_d(t["realized_r"]) for t in shorts), D(0)))},
    }
    # outliers (never hidden)
    order = sorted(range(n), key=lambda i: r[i], reverse=True)
    tot = sum(r, D(0))
    rep["largest_winner_R"] = str(r[order[0]]); rep["largest_loser_R"] = str(r[order[-1]])
    for k in (1, 5):
        top = sum((r[i] for i in order[:k]), D(0))
        rep[f"top_{k}_trades_R"] = str(top)
        rep[f"top_{k}_share_of_total_R"] = str(top / tot) if tot != 0 else None
        rep[f"total_R_without_top_{k}"] = str(tot - top)
    sizes = sorted(_d(t["position_size"]) for t in trades if t.get("position_size") is not None)
    if sizes:
        rep["position_size_lots"] = {"min": str(sizes[0]), "median": str(sizes[len(sizes) // 2]), "max": str(sizes[-1])}
    return rep


def _count(it):
    d = defaultdict(int)
    for x in it:
        d[x] += 1
    return d


def build_report(out_dir) -> dict:
    out = Path(out_dir)
    md = json.loads((out / "run_metadata.json").read_text(encoding="utf-8"))
    trades = load_trades(out / "trades.jsonl")
    rs = md.get("result_summary", {})
    rej = rs.get("trade_rejection_counts", {})
    report = {
        "DATA": {
            "dataset": md.get("data_file"), "loaded_from": md.get("data_loaded_from"),
            "window_start": md.get("window_start"), "window_end": md.get("window_end"),
            "m5_candles": rs.get("m5_candles_loaded"), "h4_candles": rs.get("h4_candles_loaded"),
            "valid_ticks": rs.get("valid_ticks"), "invalid_rows": rs.get("invalid_rows"),
            "timezone": "UTC (reader treats Tickstory timestamps as UTC; broker-offset NOT independently verified)",
            "missing_data": "Gap statistics are NOT computed by this report (see KNOWN LIMITATIONS).",
        },
        "CONFIG": {
            "config_hash": md.get("config_hash"), "engine": md.get("engine"), "engine_status": md.get("engine_status"),
            "research_path": md.get("research_path"), "sizing_mode": md.get("sizing_mode"),
            "cost_model": md.get("cost_model"), "cost_inputs": md.get("cost_inputs"),
            "cli_overrides_applied": md.get("cli_overrides_applied"), "effective_config": md.get("effective_config"),
        },
        "TRADING": compute_report(trades, md),
        "RISK": {
            "rejection_counts_by_reason": rej,
            "daily_limit_blocks": rej.get("DAILY_LOSS_LIMIT", 0),
            "consecutive_loss_blocks": rej.get("CONSECUTIVE_LOSS_LOCK", 0),
            "trade_count_blocks": rej.get("DAILY_TRADE_LIMIT", 0),
            "account": rs.get("account"),
        },
        "EXECUTION": {
            "execution_model": md.get("execution_model"),
            "entry": "market fill at break-candle close (+spread+slippage if REALISTIC); NOT first-tick",
            "exit": "OHLC, stop-first on intrabar ambiguity; exit spread/slippage applied if REALISTIC; trailing updated at candle close",
            "ohlc_ambiguous_candles_while_in_position": rs.get("ohlc_ambiguous_candles_while_in_position"),
            "validation_status": md.get("validation_status"),
        },
    }
    return report


def render_markdown(rep: dict) -> str:
    lines = ["# Phase A baseline report", "",
             f"**Engine:** {rep['CONFIG'].get('engine')} - {rep['CONFIG'].get('engine_status')}  ",
             f"**Config hash:** `{rep['CONFIG'].get('config_hash')}`  ",
             f"**Validation status:** {rep['EXECUTION'].get('validation_status')}", ""]
    for sec in ("DATA", "CONFIG", "TRADING", "RISK", "EXECUTION"):
        lines += [f"## {sec}", "```json", json.dumps(rep[sec], indent=2, default=str), "```", ""]
    return "\n".join(lines)


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) != 1:
        print(__doc__); return 2
    out = Path(argv[0])
    rep = build_report(out)
    (out / "baseline_report.json").write_text(json.dumps(rep, indent=2, default=str), encoding="utf-8")
    (out / "baseline_report.md").write_text(render_markdown(rep), encoding="utf-8")
    write_trade_ledger_csv(out / "trades.jsonl", out / "trade_ledger.csv")
    print(f"wrote baseline_report.json/.md and trade_ledger.csv in {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

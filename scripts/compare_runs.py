"""Reproducibility / divergence comparison of two run output directories.

    python scripts/compare_runs.py <run_dir_A> <run_dir_B>

Compares config hash, trade count, and for every trade (in order) the
timestamps, prices, volumes, R and P&L; also the event streams. Exit code 0
only if EVERYTHING matches. Used for (a) run-twice determinism and
(b) REFERENCE-vs-FAST divergence (where a mismatch is a finding, not an error).
Wall-clock fields (started_at/finished_at) are ignored.
"""
import json
import sys
from pathlib import Path

TRADE_KEYS = ["trade_id", "direction", "setup_created_at", "zone_creation_timestamp", "confirmation_timestamp",
              "entry_timestamp", "entry_price", "stop_loss", "take_profit_1", "take_profit_2", "position_size",
              "tp1_volume", "runner_volume", "tp1_exit_timestamp", "tp1_exit_price", "exit_timestamp",
              "exit_price", "exit_reason", "realized_r", "net_pnl", "commission", "balance_before", "balance_after"]


def _lines(p):
    return [json.loads(l) for l in Path(p).read_text(encoding="utf-8").splitlines() if l.strip()] if Path(p).exists() else []


def compare(a, b) -> dict:
    a, b = Path(a), Path(b)
    ma = json.loads((a / "run_metadata.json").read_text()); mb = json.loads((b / "run_metadata.json").read_text())
    ta, tb = _lines(a / "trades.jsonl"), _lines(b / "trades.jsonl")
    ea, eb = _lines(a / "events.jsonl"), _lines(b / "events.jsonl")
    res = {"config_hash_match": ma["config_hash"] == mb["config_hash"], "engine_a": ma.get("engine"), "engine_b": mb.get("engine"),
           "trade_count": [len(ta), len(tb)], "event_count": [len(ea), len(eb)], "trade_differences": [], "event_differences": []}
    for i, (x, y) in enumerate(zip(ta, tb)):
        for k in TRADE_KEYS:
            if x.get(k) != y.get(k):
                res["trade_differences"].append({"trade_index": i, "field": k, "a": x.get(k), "b": y.get(k)})
    sig = lambda e: (e["event_id"], e["event_type"], e["timestamp"], e["entity_id"], e["new_state"], json.dumps(e.get("payload"), sort_keys=True))
    sa, sb = [sig(e) for e in ea], [sig(e) for e in eb]
    for i, (x, y) in enumerate(zip(sa, sb)):
        if x != y:
            res["event_differences"].append({"event_index": i, "a": x, "b": y})
            if len(res["event_differences"]) >= 50:
                break
    only_a, only_b = set(sa) - set(sb), set(sb) - set(sa)
    res["events_only_in_a"], res["events_only_in_b"] = len(only_a), len(only_b)
    res["IDENTICAL"] = (res["config_hash_match"] and len(ta) == len(tb) and not res["trade_differences"]
                        and sa == sb and ma.get("result_summary", {}).get("account") == mb.get("result_summary", {}).get("account"))
    return res


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print(__doc__); raise SystemExit(2)
    r = compare(sys.argv[1], sys.argv[2])
    print(json.dumps(r, indent=2, default=str))
    raise SystemExit(0 if r["IDENTICAL"] else 1)

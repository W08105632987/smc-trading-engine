"""E0 EARLY-WARNING prefix check (NOT a substitute for the full Gate 0 comparison).

    python scripts/e0_prefix_compare.py <frozen_run_dir> <shorter_E0_run_dir>

A run with the SAME csv, window start, warm-up, engine and configuration but an EARLIER window end must emit exactly
the leading part of the frozen run's event stream (the replay is strictly chronological and each step depends only on
earlier data). End the shorter run on a Friday after the market closed (for example 2025-10-03 23:59:59) so that no
candle is cut in half at the boundary.

Checks: the config hash is identical; every event line of the shorter run equals the same-numbered line of the frozen
run (byte for byte); trades.jsonl of the shorter run is a prefix of the frozen one; telemetry has 0 errors and 0
shadow-gate mismatches. Exit code 0 only if all hold.
"""
import json, sys
from pathlib import Path

def lines(p):
    return Path(p).read_text(encoding="utf-8").splitlines() if Path(p).exists() else []

def main(frozen, short):
    frozen, short = Path(frozen), Path(short)
    rep = {"checks": []}; ok = True
    mf = json.loads((frozen / "run_metadata.json").read_text()); ms = json.loads((short / "run_metadata.json").read_text())
    same_cfg = mf["config_hash"] == ms["config_hash"]; ok &= same_cfg
    rep["checks"].append({"check": "config_hash identical", "frozen": mf["config_hash"][:16], "short": ms["config_hash"][:16], "result": "PASS" if same_cfg else "FAIL"})
    for k in ("engine", "window_start", "warmup_days", "data_loaded_from", "cost_model", "sizing_mode", "initial_equity"):
        same = mf.get(k) == ms.get(k); ok &= same
        rep["checks"].append({"check": f"metadata.{k} identical", "frozen": mf.get(k), "short": ms.get(k), "result": "PASS" if same else "FAIL"})
    ef, es = lines(frozen / "events.jsonl"), lines(short / "events.jsonl")
    n = len(es); longer = len(ef) > n
    first_diff = next((i + 1 for i in range(min(n, len(ef))) if ef[i] != es[i]), None)
    pre_ok = (first_diff is None) and n <= len(ef) and n > 0
    ok &= pre_ok
    res = {"check": "events.jsonl of shorter run == first N lines of frozen", "short_lines": n, "frozen_lines": len(ef), "result": "PASS" if pre_ok else "FAIL"}
    if first_diff: res.update({"first_difference_line": first_diff, "frozen": ef[first_diff - 1][:300], "short": es[first_diff - 1][:300]})
    if not longer: res["note"] = "shorter run is not shorter than the frozen run (use an earlier window end for an early-warning check)"
    rep["checks"].append(res)
    tf, ts = lines(frozen / "trades.jsonl"), lines(short / "trades.jsonl")
    t_ok = tf[: len(ts)] == ts and len(ts) <= len(tf); ok &= t_ok
    rep["checks"].append({"check": "trades.jsonl is a prefix", "short": len(ts), "frozen": len(tf), "result": "PASS" if t_ok else "FAIL"})
    man = short / "telemetry_manifest.json"
    if man.exists():
        m = json.loads(man.read_text()); fc = json.loads((short / "funnel_counters.json").read_text())
        e_ok = m.get("telemetry_error_count") == 0 and fc.get("shadow_gate_mismatch", 0) == 0; ok &= e_ok
        rep["checks"].append({"check": "telemetry errors == 0 and shadow-gate mismatches == 0", "errors": m.get("telemetry_error_count"), "mismatches": fc.get("shadow_gate_mismatch", 0), "result": "PASS" if e_ok else "FAIL"})
    rep["OVERALL"] = "PASS: prefix identical (early-warning only; full Gate 0 still required)" if ok else "FAIL"
    print(json.dumps(rep, indent=1)); return 0 if ok else 1

if __name__ == "__main__":
    if len(sys.argv) != 3: print(__doc__); sys.exit(2)
    sys.exit(main(sys.argv[1], sys.argv[2]))

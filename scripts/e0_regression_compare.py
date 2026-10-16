"""E0 exact-regression comparator.

    python scripts/e0_regression_compare.py <frozen_run_dir> <e0_run_dir>

Compares a frozen run (e.g. runs/probe_oct2025_reference) with an E0 re-run of the SAME data,
window, engine and configuration. E0 is observation-only, so these must be IDENTICAL:
  events.jsonl, trades.jsonl, statistics_final.json, statistics_robustness.json, plausibility_report.json
and, in run_metadata.json, everything except the documented wall-clock/telemetry keys.
Exit code 0 only if every check passes. Extra files in the E0 dir (candidates.jsonl,
h4_transitions.jsonl, funnel_counters.json, telemetry_manifest.json) are the documented additions.
"""
import hashlib, json, sys
from pathlib import Path

COMPARATOR_VERSION = "1.1"
BYTE_IDENTICAL = ["events.jsonl", "trades.jsonl", "statistics_final.json", "statistics_robustness.json", "plausibility_report.json", "trade_ledger.csv"]
META_IGNORE = {"started_at_utc", "finished_at_utc"}
# Reported but NOT failing: they cannot change any decision. Everything else in run_metadata.json is strict
# (config hash, engine, window, warm-up, costs, sizing, data file name AND size, result summary, account).
META_INFORMATIONAL = {"python", "data_file.path"}
DOCUMENTED_ADDITIONS = ["candidates.jsonl", "h4_transitions.jsonl", "funnel_counters.json", "telemetry_manifest.json"]

def sha(p): return hashlib.sha256(Path(p).read_bytes()).hexdigest()

def main(a, b):
    a, b = Path(a), Path(b); ok = True; report = {"comparator_version": COMPARATOR_VERSION, "checks": []}
    for f in BYTE_IDENTICAL:
        pa, pb = a / f, b / f
        if not pa.exists() and not pb.exists():
            report["checks"].append({"file": f, "result": "ABSENT_IN_BOTH"}); continue
        if not (pa.exists() and pb.exists()):
            ok = False; report["checks"].append({"file": f, "result": "MISSING_IN_ONE", "frozen": pa.exists(), "e0": pb.exists()}); continue
        same = sha(pa) == sha(pb)
        ok &= same
        n = sum(1 for _ in open(pa, "rb"))
        res = {"file": f, "lines": n, "result": "IDENTICAL" if same else "DIFFERENT", "sha256_frozen": sha(pa)[:16], "sha256_e0": sha(pb)[:16]}
        if not same and f.endswith(".jsonl"):
            la, lb = pa.read_text(encoding="utf-8").splitlines(), pb.read_text(encoding="utf-8").splitlines()
            for i, (x, y) in enumerate(zip(la, lb)):
                if x != y:
                    res["first_difference_line"] = i + 1; res["frozen"] = x[:300]; res["e0"] = y[:300]; break
            else:
                res["length_mismatch"] = [len(la), len(lb)]
        report["checks"].append(res)
    ma = json.loads((a / "run_metadata.json").read_text()); mb = json.loads((b / "run_metadata.json").read_text())
    diffs, info = [], []
    # result_summary.account etc. are compared; wall-clock fields and telemetry additions are not
    def flat(d, pre=""):
        for k, v in d.items():
            if isinstance(v, dict): yield from flat(v, f"{pre}{k}.")
            else: yield f"{pre}{k}", v
    fa, fb = dict(flat(ma)), dict(flat(mb))
    for k in sorted(set(fa) | set(fb)):
        if k.split(".")[-1] in META_IGNORE: continue
        if fa.get(k) != fb.get(k):
            (info if k in META_INFORMATIONAL else diffs).append({"key": k, "frozen": fa.get(k), "e0": fb.get(k)})
    ok &= not diffs
    report["checks"].append({"file": "run_metadata.json", "result": "IDENTICAL_EXCEPT_WALLCLOCK" if not diffs else "DIFFERENT", "differences": diffs, "informational_differences_not_failing": info})
    report["documented_additions_present_in_e0"] = {f: (b / f).exists() for f in DOCUMENTED_ADDITIONS}
    tm = (b / "telemetry_manifest.json")
    if tm.exists():
        m = json.loads(tm.read_text()); report["telemetry_error_count"] = m.get("telemetry_error_count"); ok &= (m.get("telemetry_error_count") == 0)
        fc = json.loads((b / "funnel_counters.json").read_text()); report["shadow_gate_mismatch"] = fc.get("shadow_gate_mismatch", 0); ok &= (fc.get("shadow_gate_mismatch", 0) == 0)
    report["OVERALL"] = "PASS: exact regression" if ok else "FAIL"
    print(json.dumps(report, indent=1, default=str))
    return 0 if ok else 1

if __name__ == "__main__":
    if len(sys.argv) != 3: print(__doc__); sys.exit(2)
    sys.exit(main(sys.argv[1], sys.argv[2]))

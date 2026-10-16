"""E0 funnel diagnostics from candidates.jsonl (stdlib only).

    python scripts/e0_funnel_report.py <run_dir> [<other_run_dir>]

Reports funnel COUNTS and GEOMETRY as diagnostics. Nothing here is evidence of profitability, and a larger
count is never treated as an improvement. With two run dirs it also prints a candidate-level transition matrix
(joined on setup_id) so a later version can be compared to this one.
"""
import json, sys, statistics as st
from collections import Counter, defaultdict
from decimal import Decimal as D
from pathlib import Path

GATES = ["HTF_CONTEXT_INVALID", "STOP_BELOW_MINIMUM", "STOP_TOO_WIDE", "TP2_NOT_BEYOND_TP1", "RR_BELOW_MINIMUM", "SCORE_BELOW_THRESHOLD", "RISK_GATES"]

def load(d):
    rows = [json.loads(l) for l in (Path(d) / "candidates.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]
    fc = json.loads((Path(d) / "funnel_counters.json").read_text())
    return rows, fc

def q(xs, p):
    xs = sorted(xs); return xs[min(len(xs) - 1, int((len(xs) - 1) * p + 0.5))] if xs else None

def dist(xs):
    xs = [float(x) for x in xs if x is not None]
    return None if not xs else {"n": len(xs), "min": round(min(xs), 3), "p25": round(q(xs, .25), 3), "median": round(st.median(xs), 3), "p75": round(q(xs, .75), 3), "max": round(max(xs), 3)}

def report(d):
    rows, fc = load(d)
    decided = [r for r in rows if r.get("gates")]
    out = {"run": str(d), "funnel_counters": fc, "setups_logged": len(rows), "decided_with_geometry": len(decided)}
    out["terminal_states"] = dict(Counter((r.get("terminal") or {}).get("state", "NONE") for r in rows))
    out["first_failed_gate"] = dict(Counter(r["gates"]["first_failed_gate"] or "APPROVED" for r in decided))
    indep = Counter()
    for r in decided:
        for g in r["gates"]["all_failed_gates"]: indep[g] += 1
    out["independent_gate_failures"] = dict(indep)
    out["shadow_matches_actual"] = f"{sum(1 for r in decided if r.get('shadow_matches_actual'))}/{len(decided)}"
    # RR thresholds on geometry-valid candidates (pass HTF, stop bounds, TP2 order) -- COUNTS ONLY
    valid = [r for r in decided if r["rr"] and not ({"HTF_CONTEXT_INVALID", "STOP_BELOW_MINIMUM", "STOP_TOO_WIDE", "TP2_NOT_BEYOND_TP1"} & set(r["gates"]["all_failed_gates"]))]
    out["geometry_valid"] = len(valid)
    out["geometry_valid_would_pass_rr"] = {t: sum(1 for r in valid if r["rr"]["would_pass_rr"].get(t)) for t in ["1.0", "1.5", "2.0", "2.5", "3.8"]}
    out["stop_distance_atr"] = dist(r.get("stop_distance_atr") for r in decided)
    out["zone_to_entry_atr"] = dist(r.get("zone_to_entry_atr") for r in decided)
    out["stop_inside_zone_range_share"] = (round(sum(1 for r in decided if r.get("stop_inside_zone_range")) / len(decided), 3) if decided else None)
    out["tp1_distance_atr"] = dist(r.get("tp1_distance_atr") for r in decided); out["tp2_distance_atr"] = dist(r.get("tp2_distance_atr") for r in decided)
    out["blended_rr"] = dist(r["rr"].get("blended_rr") for r in decided if r.get("rr"))
    # freshness / validity
    conf = [r for r in rows if r.get("confirmation_step_at")]
    out["confirmed"] = len(conf)
    out["confirmed_retroactively"] = sum(1 for r in conf if r.get("retroactive_confirmation"))
    out["decided_after_confirmation_step"] = sum(1 for r in decided if r.get("decision_after_break_step"))
    out["confirmed_never_decided"] = sum(1 for r in conf if not r.get("decision_step_at") and not (r.get("terminal") or {}))
    out["parked_behind_open_position_setups"] = sum(1 for r in rows if r.get("parked_steps", 0) > 0)
    out["skips_by_reason"] = dict(sum((Counter(r.get("skips", {})) for r in rows), Counter()))
    out["construction_lag_h_from_zone"] = dist(r.get("construction_lag_h_from_zone") for r in rows)
    # duplicate groups (pool-free key) and tolerance-linked merge using the recorded ATR
    groups = defaultdict(list)
    for r in rows:
        if r.get("event_key_pool_free"): groups[tuple(r["event_key_pool_free"])].append(r)
    out["event_key_groups"] = len(groups); out["event_key_groups_with_variants"] = sum(1 for g in groups.values() if len(g) > 1)
    merged = 0
    for g in groups.values():
        g = sorted(g, key=lambda r: D(r["pool_price"])); cur = None
        for r in g:
            tol = D("0.05") * D(r["atr_at_setup"]) if r.get("atr_at_setup") else D("0.02")
            if cur is None or D(r["pool_price"]) - cur > tol: merged += 1; cur = D(r["pool_price"])
    out["opportunities_after_equal_level_tolerance_merge"] = merged
    # target pair shadow
    tg = [r["targets"] for r in decided if r.get("targets")]
    out["shadow_strict_pair_available"] = {"yes": sum(1 for t in tg if t.get("shadow_strict_pair_available") is True), "no": sum(1 for t in tg if t.get("shadow_strict_pair_available") is False), "n/a": sum(1 for t in tg if "shadow_strict_pair_available" not in t)}
    out["shadow_tp2_extension_vs_nearest_external_atr"] = dist(t.get("shadow_tp2_extension_vs_nearest_external_atr") for t in tg)
    h4 = [json.loads(l) for l in (Path(d) / "h4_transitions.jsonl").read_text().splitlines() if l.strip()] if (Path(d) / "h4_transitions.jsonl").exists() else []
    out["h4_events_applied"] = len(h4); out["h4_events_applied_out_of_time_order"] = sum(1 for r in h4 if r.get("applied_out_of_time_order"))
    return out, rows

def transition(a_rows, b_rows):
    A = {r["setup_id"]: (r.get("actual_outcome") or (r.get("terminal") or {}).get("state") or "UNDECIDED") for r in a_rows}
    B = {r["setup_id"]: (r.get("actual_outcome") or (r.get("terminal") or {}).get("state") or "UNDECIDED") for r in b_rows}
    m = Counter()
    for k in set(A) | set(B): m[(A.get(k, "ABSENT"), B.get(k, "ABSENT"))] += 1
    return {f"{a} -> {b}": n for (a, b), n in sorted(m.items(), key=lambda kv: -kv[1])}

if __name__ == "__main__":
    if len(sys.argv) not in (2, 3): print(__doc__); sys.exit(2)
    a, ar = report(sys.argv[1]); res = {"A": a}
    if len(sys.argv) == 3:
        b, br = report(sys.argv[2]); res["B"] = b; res["transition_matrix_A_to_B"] = transition(ar, br)
    print(json.dumps(res, indent=1))

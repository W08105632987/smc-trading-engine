"""V4 offline merge mapping (NO engine run, pure Python, no third-party deps).

Replays the V4 rule (backtest/event_dedup.py semantics) over an existing candidate ledger (candidates.jsonl written by E0
telemetry) and writes the candidate-by-candidate mapping: every merged candidate -> exactly one kept candidate.

FIRST-ORDER ESTIMATE ONLY: in the real V4 run, removing duplicates changes admission/displacement (a deduplicated variant no
longer competes for the 3 pending slots), so the set of kept candidates in a real run can differ slightly from this offline replay.
The real run's own `v4_merge_map.jsonl` is the authoritative mapping.

Usage:
  python scripts/v4_merge_mapping.py runs/probe_oct2025_E0/candidates.jsonl --out runs/v4_offline_mapping [--tick-size 0.01] [--tol-atr 0.05]
"""
from __future__ import annotations

import argparse
import csv
import json
from collections import Counter, defaultdict
from decimal import Decimal
from pathlib import Path


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("candidates")
    ap.add_argument("--out", required=True)
    ap.add_argument("--tick-size", default="0.01")
    ap.add_argument("--tol-atr", default="0.05")
    a = ap.parse_args()
    tick, tol_atr = Decimal(a.tick_size), Decimal(a.tol_atr)

    rows = [json.loads(l) for l in open(a.candidates, encoding="utf-8")]
    steps = defaultdict(list)
    for i, r in enumerate(rows):
        steps[r["constructed_at"]].append((i, r))

    registry: dict[tuple, list[dict]] = defaultdict(list)   # key -> anchors (oldest first)
    mapping, kept_ids = [], []
    for at in sorted(steps):                                  # steps in chronological order
        items = steps[at]
        # one item per zone (first occurrence), then ascending pool id inside the step -- same rule as plan_step()
        seen, plan_items = set(), []
        for i, r in items:
            if r["zone_id"] in seen:
                continue
            seen.add(r["zone_id"])
            plan_items.append((i, r))
        step_anchors: dict[tuple, list[dict]] = defaultdict(list)
        for i, r in sorted(plan_items, key=lambda t: (t[1]["pool_id"], t[1]["zone_id"])):
            key = (r["direction"], r["zone_type"], Decimal(r["zone_upper"]), Decimal(r["zone_lower"]),
                   r["sweep_candle_id"], r["displacement_id"], r["pool_type"])
            price = Decimal(r["pool_price"])
            atr = Decimal(r["atr_at_setup"]) if r.get("atr_at_setup") else None
            tol = max(2 * tick, tol_atr * atr) if atr is not None else 2 * tick
            hit = next((x for x in registry[key] + step_anchors[key] if abs(price - x["price"]) <= x["tol"]), None)
            if hit is None:
                step_anchors[key].append({"price": price, "tol": tol, "setup_id": r["setup_id"], "pool_id": r["pool_id"],
                                          "zone_upper": r["zone_upper"], "zone_lower": r["zone_lower"], "score": r["score_at_creation"]})
                kept_ids.append(r["setup_id"])
            else:
                mapping.append({
                    "merged_setup_id": r["setup_id"], "merged_pool_id": r["pool_id"], "merged_pool_price": str(price),
                    "kept_setup_id": hit["setup_id"], "kept_pool_id": hit["pool_id"], "anchor_pool_price": str(hit["price"]),
                    "price_delta": str(abs(price - hit["price"])), "tolerance": str(hit["tol"]),
                    "zone_upper": r["zone_upper"], "zone_lower": r["zone_lower"],
                    "bounds_identical": Decimal(r["zone_upper"]) == Decimal(hit["zone_upper"]) and Decimal(r["zone_lower"]) == Decimal(hit["zone_lower"]),
                    "merged_score": r["score_at_creation"], "kept_score": hit["score"],
                    "merged_constructed_at": r["constructed_at"], "kept_constructed_at": None,
                })
        for k, v in step_anchors.items():
            registry[k].extend(v)

    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    with open(out / "v4_offline_merge_map.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(mapping[0].keys()) if mapping else ["merged_setup_id"])
        w.writeheader()
        w.writerows(mapping)

    per_kept = Counter(m["kept_setup_id"] for m in mapping)
    mapped_once = all(m["merged_setup_id"] not in set() for m in mapping)
    merged_ids = [m["merged_setup_id"] for m in mapping]
    summary = {
        "input_candidates": len(rows), "kept_after_v4": len(kept_ids), "merged_away": len(mapping),
        "each_merged_maps_to_exactly_one_kept": len(merged_ids) == len(set(merged_ids)),
        "merged_with_different_zone_bounds": sum(1 for m in mapping if not m["bounds_identical"]),
        "max_price_delta_over_tolerance": max((float(Decimal(m["price_delta"]) / Decimal(m["tolerance"])) for m in mapping), default=0.0),
        "largest_group_size": (max(per_kept.values()) + 1) if per_kept else 1,
        "merged_with_score_difference": sum(1 for m in mapping if m["merged_score"] != m["kept_score"]),
        "kept_lower_score_than_merged": sum(1 for m in mapping if float(m["kept_score"]) < float(m["merged_score"])),
    }
    (out / "v4_offline_summary.json").write_text(json.dumps(summary, indent=1), encoding="utf-8")
    print(json.dumps(summary, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

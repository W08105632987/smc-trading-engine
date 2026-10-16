"""Throughput diagnostic — run this LOCALLY against the real CSV to
isolate exactly which step of row processing is slow. Prints a timing
breakdown for each stage in isolation, on the SAME rows, so the
bottleneck is identified rather than guessed at.

Usage:
    uv run python scripts/diagnose_ingestion_throughput.py "<path>" --rows 2000000

Reads the first N rows only (bounded, quick) and times, in isolation:
  1. raw line iteration only (no parsing at all)
  2. + comma-split
  3. + timestamp parsing
  4. + Decimal() conversion of all 4 numeric fields
  5. + full Tick object construction (what stream_tickstory_csv does today)
  6. the ACTUAL production stream_tickstory_csv(), with not_before set to
     the far future so every row takes the cheap-skip fast path — this
     is the one stage that tests the REAL function in question, not a
     hand-simulated approximation of it. If stage 6 is not close to
     stage 1's speed, the fast path isn't engaging as intended on this
     file, which is itself the answer we need.

Each stage includes all previous stages' work (cumulative), so the
DELTA between consecutive stages tells you what that specific step
costs. Also reports whether the file is on a local disk or a
network/cloud-synced path (e.g. OneDrive), since that alone can be the
dominant cost on Windows and has nothing to do with this codebase.
"""

from __future__ import annotations

import argparse
import sys
import time
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from market_data.ingestion.tickstory_reader import (
    IngestionReport,
    _parse_tickstory_timestamp,
    stream_tickstory_csv,
)
from market_data.ticks.tick_aggregator import Tick


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("csv_path")
    parser.add_argument("--rows", type=int, default=2_000_000)
    parser.add_argument("--skip", type=int, default=0,
                         help="skip this many rows before starting the timed stages — "
                              "run once with --skip 0 and once with a large --skip "
                              "(e.g. 70000000) on the SAME file to directly test whether "
                              "throughput degrades deeper into the file (a strong sign of "
                              "a cloud-sync placeholder file needing on-demand download, "
                              "vs. a flat, environment-independent parsing cost)")
    args = parser.parse_args()

    path = Path(args.csv_path)
    print(f"File: {path}")
    try:
        size_mb = path.stat().st_size / 1e6
        print(f"Size on disk: {size_mb:.1f} MB")
    except OSError as exc:
        print(f"Could not stat file: {exc}")
    if args.skip:
        print(f"Skipping the first {args.skip:,} rows (timed separately, not counted below), "
              f"then timing the next {args.rows:,} rows...")
        t_skip0 = time.perf_counter()
        with open(path, "r", encoding="utf-8", newline="") as f:
            next(f)  # header
            for i, _ in enumerate(f):
                if i >= args.skip:
                    break
        t_skip1 = time.perf_counter()
        print(f"Skip phase: {args.skip:,} rows in {t_skip1 - t_skip0:.2f}s "
              f"({args.skip / max(t_skip1 - t_skip0, 1e-9):,.0f} rows/s)")
        print("NOTE: each stage below re-does this same skip internally (so timings are "
              "directly comparable across stages), meaning each stage's printed time "
              "INCLUDES roughly this skip cost again. Subtract it if you want the "
              "isolated post-skip processing time.")
        print()
    else:
        print(f"Timing the first {args.rows:,} rows (after header), 5 cumulative stages...")
    print()

    def _open_at_offset():
        f = open(path, "r", encoding="utf-8", newline="")
        next(f)  # header
        if args.skip:
            for i, _ in enumerate(f):
                if i >= args.skip - 1:
                    break
        return f

    # Stage 1: raw line iteration only
    t0 = time.perf_counter()
    n = 0
    with _open_at_offset() as f:
        for line in f:
            n += 1
            if n >= args.rows:
                break
    t1 = time.perf_counter()
    stage1 = t1 - t0
    print(f"[1] raw line iteration only:            {stage1:8.2f}s  ({n / stage1:,.0f} rows/s)")

    # Stage 2: + comma split
    t0 = time.perf_counter()
    n = 0
    with _open_at_offset() as f:
        for line in f:
            fields = line.rstrip("\r\n").split(",")
            n += 1
            if n >= args.rows:
                break
    t1 = time.perf_counter()
    stage2 = t1 - t0
    print(f"[2] + comma split:                      {stage2:8.2f}s  ({n / stage2:,.0f} rows/s)  "
          f"(delta: {stage2 - stage1:+.2f}s)")

    # Stage 3: + timestamp parsing
    t0 = time.perf_counter()
    n = 0
    with _open_at_offset() as f:
        for line in f:
            fields = line.rstrip("\r\n").split(",")
            _ = _parse_tickstory_timestamp(fields[0])
            n += 1
            if n >= args.rows:
                break
    t1 = time.perf_counter()
    stage3 = t1 - t0
    print(f"[3] + timestamp parsing:                {stage3:8.2f}s  ({n / stage3:,.0f} rows/s)  "
          f"(delta: {stage3 - stage2:+.2f}s)")

    # Stage 4: + Decimal() conversion of all 4 numeric fields
    t0 = time.perf_counter()
    n = 0
    with _open_at_offset() as f:
        for line in f:
            fields = line.rstrip("\r\n").split(",")
            _ = _parse_tickstory_timestamp(fields[0])
            _ = Decimal(fields[1])
            _ = Decimal(fields[2])
            _ = Decimal(fields[3])
            _ = Decimal(fields[4])
            n += 1
            if n >= args.rows:
                break
    t1 = time.perf_counter()
    stage4 = t1 - t0
    print(f"[4] + Decimal() x4:                     {stage4:8.2f}s  ({n / stage4:,.0f} rows/s)  "
          f"(delta: {stage4 - stage3:+.2f}s)")

    # Stage 5: full Tick construction (what production code actually does)
    t0 = time.perf_counter()
    n = 0
    with _open_at_offset() as f:
        for line in f:
            fields = line.rstrip("\r\n").split(",")
            ts = _parse_tickstory_timestamp(fields[0])
            _ = Tick(
                symbol="XAUUSD", timestamp=ts, bid=Decimal(fields[1]), ask=Decimal(fields[2]),
                bid_volume=Decimal(fields[3]), ask_volume=Decimal(fields[4]),
            )
            n += 1
            if n >= args.rows:
                break
    t1 = time.perf_counter()
    stage5 = t1 - t0
    print(f"[5] + full Tick construction:           {stage5:8.2f}s  ({n / stage5:,.0f} rows/s)  "
          f"(delta: {stage5 - stage4:+.2f}s)")

    # Stage 6: the ACTUAL fast-path check stream_tickstory_csv() uses
    # internally (_cheap_timestamp_only: find one comma, slice, parse) —
    # not a hand-simulated approximation, the real function, in a loop
    # bounded the same way as stages 1-5 so this stays fast even near
    # the start of a huge file.
    from market_data.ingestion.tickstory_reader import _cheap_timestamp_only
    far_future = datetime(2099, 1, 1, tzinfo=timezone.utc)
    t0 = time.perf_counter()
    n = 0
    skipped = 0
    with _open_at_offset() as f:
        for line in f:
            ts = _cheap_timestamp_only(line)
            if ts is not None and ts < far_future:
                skipped += 1
            n += 1
            if n >= args.rows:
                break
    t1 = time.perf_counter()
    stage6 = t1 - t0
    print(f"[6] ACTUAL _cheap_timestamp_only() fast-path check: {stage6:8.2f}s  "
          f"({n / stage6:,.0f} rows/s)  (delta vs [1]: {stage6 - stage1:+.2f}s)")

    print()
    print("=" * 70)
    print(f"Full-file extrapolation at stage-5 rate ({n / stage5:,.0f} rows/s):")
    print(f"  84,470,000 rows -> {84_470_000 / (n / stage5) / 3600:.2f} hours")
    print(f"Full-file extrapolation at stage-6 (ACTUAL fast-path) rate ({n / stage6:,.0f} rows/s):")
    print(f"  84,470,000 rows -> {84_470_000 / (n / stage6) / 3600:.2f} hours")
    print()
    print("Compare [1] to [6] directly: they do conceptually similar amounts")
    print("of work (find/parse one short timestamp per row and move on).")
    print("If [6] is dramatically slower than [1], the discrepancy is inside")
    print("_cheap_timestamp_only()/_parse_tickstory_timestamp() itself on")
    print("this specific machine, not disk I/O and not generator overhead.")
    print("If [1] and [6] are close here (near the start of the file) but")
    print("the real preflight run was still slow reading DEEP into the")
    print("file, re-run this script with --skip 70000000 to test the same")
    print("comparison at that position instead.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

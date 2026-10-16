"""Sustained-read trend diagnostic — ONE continuous, uninterrupted pass
through the file from the start, printing a live rolling throughput
reading every --checkpoint-rows rows. Unlike the region-content
diagnostic (which seeks directly to a byte offset — near-instant, and
does not reproduce a genuine sequential walk), this reproduces exactly
what the real ingestion run does: a single ordinary `for line in f`
loop, nothing more.

The region-content diagnostic already ruled out file content/position
as the cause (identical structure and identical short-burst read speed
at row ~0 and row ~70,000,000). What's left is duration: does
throughput hold steady over ONE long continuous read, or does it
degrade the longer the same read has been running -- which would point
at something time-dependent (antivirus escalating scrutiny of a
long-lived read, thermal/power throttling under sustained I/O, or a
Windows-specific effect on long-held file handles) rather than anything
about the file itself or this codebase's parsing logic.

Usage:
    uv run python scripts/diagnose_sustained_read_trend.py "<path>" --checkpoint-rows 2000000 --max-rows 30000000

Ctrl+C at any time is safe -- it just stops early and prints what was
observed so far; you do not need to let it run to completion to get a
useful trend.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("csv_path")
    parser.add_argument("--checkpoint-rows", type=int, default=2_000_000,
                         help="print a rolling throughput reading every this many rows")
    parser.add_argument("--max-rows", type=int, default=None,
                         help="stop after this many rows (default: read the whole file)")
    args = parser.parse_args()

    path = Path(args.csv_path)
    print(f"File: {path}")
    print(f"Size on disk: {path.stat().st_size / 1e6:.1f} MB")
    print(f"Checkpoint every {args.checkpoint_rows:,} rows"
          + (f", stopping after {args.max_rows:,} rows" if args.max_rows else ", reading the whole file"))
    print("(Ctrl+C is safe at any point -- partial results below are still useful)")
    print()
    print(f"{'rows':>14}  {'elapsed(s)':>10}  {'since-start rows/s':>19}  "
          f"{'since-last-checkpoint rows/s':>29}  {'MB/s (since-start)':>18}")

    t_start = time.perf_counter()
    t_last_checkpoint = t_start
    n = 0
    n_at_last_checkpoint = 0
    bytes_read = 0

    try:
        with open(path, "r", encoding="utf-8", newline="") as f:
            next(f)  # header
            for line in f:
                n += 1
                bytes_read += len(line)
                if n % args.checkpoint_rows == 0:
                    now = time.perf_counter()
                    elapsed_total = now - t_start
                    elapsed_since_last = now - t_last_checkpoint
                    rows_since_last = n - n_at_last_checkpoint
                    rate_since_start = n / elapsed_total
                    rate_since_last = rows_since_last / elapsed_since_last if elapsed_since_last > 0 else 0
                    mb_rate_since_start = (bytes_read / 1e6) / elapsed_total
                    print(f"{n:>14,}  {elapsed_total:>10.1f}  {rate_since_start:>19,.0f}  "
                          f"{rate_since_last:>29,.0f}  {mb_rate_since_start:>18.2f}")
                    sys.stdout.flush()
                    t_last_checkpoint = now
                    n_at_last_checkpoint = n
                if args.max_rows is not None and n >= args.max_rows:
                    break
    except KeyboardInterrupt:
        print("\n(stopped early by Ctrl+C)")

    elapsed_total = time.perf_counter() - t_start
    print()
    print("=" * 70)
    print(f"Total rows read: {n:,} in {elapsed_total:.1f}s ({n / max(elapsed_total,1e-9):,.0f} rows/s overall)")
    print()
    print("Look at the 'since-last-checkpoint rows/s' column: if it stays roughly")
    print("flat across the whole run, throughput is NOT degrading over time/distance")
    print("-- whatever caused the earlier slow runs isn't reproduced here at all,")
    print("and the earlier full-preflight slowness may have had a different, one-off")
    print("cause (worth just re-running the preflight once more to check).")
    print("If that column trends steadily DOWNWARD the longer the read continues,")
    print("that is direct, timestamped evidence of a duration-dependent slowdown --")
    print("at that point checking Resource Monitor's Disk and CPU (esp. antivirus")
    print("process CPU%) during a live run, at the point where it visibly slows,")
    print("is the most direct way to catch the cause in the act.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

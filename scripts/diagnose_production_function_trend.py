"""Sustained-read trend diagnostic, PRODUCTION FUNCTION variant. Same
idea as diagnose_sustained_read_trend.py (one continuous pass, live
rolling throughput) but drives the ACTUAL stream_tickstory_csv()
generator instead of a bare `for line in f` loop -- the one combination
not yet tested cleanly: the real function, in a genuine single
uninterrupted pass, with live progress.

Bounded by WALL-CLOCK TIME (--max-seconds), not row count: with
not_before set to the far future, every row takes the cheap-skip fast
path and NOTHING is ever yielded back to the calling loop until true
EOF -- meaning the consuming loop cannot be interrupted mid-stream from
the same thread (there is no yield point to resume from). This runs the
consumption in a background daemon thread instead and simply ends the
process once --max-seconds elapses (a daemon thread doing blocking file
I/O has no clean way to cancel mid-read(); letting the process exit
underneath it is the standard, correct way to bound this, not a hack).

Usage:
    uv run python scripts/diagnose_production_function_trend.py "<path>" --max-seconds 60

Ctrl+C also works and prints progress before exiting.
"""

from __future__ import annotations

import argparse
import os
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from market_data.ingestion.tickstory_reader import IngestionReport, stream_tickstory_csv


def _consume(path: Path, symbol: str, report: IngestionReport) -> None:
    far_future = datetime(2099, 1, 1, tzinfo=timezone.utc)
    gen = stream_tickstory_csv(path, symbol, report=report, not_before=far_future)
    for _ in gen:
        pass  # nothing is ever yielded; this line is unreachable but harmless


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("csv_path")
    parser.add_argument("--symbol", default="XAUUSD")
    parser.add_argument("--progress-seconds", type=float, default=5.0)
    parser.add_argument("--max-seconds", type=float, default=60.0,
                         help="stop and print results after this many wall-clock seconds "
                              "(default 60 -- raise it if you want a longer sample)")
    args = parser.parse_args()

    path = Path(args.csv_path)
    print(f"File: {path}")
    print(f"Size on disk: {path.stat().st_size / 1e6:.1f} MB")
    print(f"Driving the ACTUAL stream_tickstory_csv() function for up to {args.max_seconds:.0f}s")
    print("(fast path engaged via a far-future not_before; Ctrl+C also stops early)")
    print()
    print(f"{'elapsed(s)':>10}  {'rows':>14}  {'since-start rows/s':>19}  "
          f"{'since-last-checkpoint rows/s':>29}")

    report = IngestionReport()
    t_start = time.perf_counter()

    worker = threading.Thread(target=_consume, args=(path, args.symbol, report), daemon=True)
    worker.start()

    n_at_last = 0
    t_last = t_start
    try:
        while True:
            time.sleep(args.progress_seconds)
            now = time.perf_counter()
            n = report.rows_skipped_before_window
            elapsed_total = now - t_start
            elapsed_since_last = now - t_last
            rate_since_start = n / elapsed_total if elapsed_total > 0 else 0
            rate_since_last = (n - n_at_last) / elapsed_since_last if elapsed_since_last > 0 else 0
            print(f"{elapsed_total:>10.1f}  {n:>14,}  {rate_since_start:>19,.0f}  {rate_since_last:>29,.0f}")
            sys.stdout.flush()
            n_at_last, t_last = n, now
            if not worker.is_alive():
                print("\n(reached end of file before the time limit)")
                break
            if elapsed_total >= args.max_seconds:
                break
    except KeyboardInterrupt:
        print("\n(stopped early by Ctrl+C)")

    elapsed_total = time.perf_counter() - t_start
    n = report.rows_skipped_before_window
    print()
    print("=" * 70)
    print(f"Rows processed (real fast-path function) in {elapsed_total:.1f}s: {n:,} "
          f"({n / max(elapsed_total,1e-9):,.0f} rows/s overall)")
    print()
    print("Compare the 'since-last-checkpoint rows/s' trend above directly against")
    print("diagnose_sustained_read_trend.py's equivalent column for the SAME file and")
    print("a similar elapsed time. If they track closely, the real function is fine")
    print("over a genuine single pass, and the earlier slow full-preflight run likely")
    print("had a one-off/transient cause worth re-checking with a fresh run. If this")
    print("trend is dramatically lower, or degrades while the bare-iteration version")
    print("stayed flat over the same elapsed time, that is direct proof something")
    print("specific to stream_tickstory_csv's code path -- not just 'a lot of file' --")
    print("is the bottleneck.")
    os._exit(0)  # the daemon worker thread may be blocked in a syscall; end cleanly here


if __name__ == "__main__":
    main()

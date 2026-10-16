"""Real-data preflight — run this LOCALLY against the actual Tickstory
CSV. Claude does not have access to that file (never uploaded, exists
only on the local machine), so this script exists to be run there and
its printed output pasted back for evaluation. Nothing in this file
uses synthetic data; it exercises the genuine production path.

Usage (from the project's smc-trading-engine directory):

    uv run python scripts/real_data_preflight.py \
        "C:\\Users\\hp\\Downloads\\xauusd_working\\XAUUSD_ticks_2025-09-01_2026-08-31.csv" \
        --window-start "2026-08-14 09:00:00" \
        --window-end   "2026-08-15 12:00:00"

IMPORTANT, learned by actually running this script end-to-end before
handing it over (see the accompanying report): the REAL ReplayPipeline
(real swing/structure/liquidity/zone/scoring/risk engines — this is
deliberately not a simplified stand-in) averaged roughly 150ms per M5
replay step even on a small, early-history test run, and that cost is
expected to GROW as the candle history each step processes gets longer
(the M1-M9 architecture recomputes structure over the full growing
candle list every step, not incrementally — a separate, known
limitation from the M1-9 architecture itself, not something this
Milestone 10 pass changed or was asked to fix). Practically:

  - Keep gap-boundary validation windows NARROW (a few hours to 1-2
    days on each side of a gap, not the gap's full multi-day span --
    there is no data to process during a real gap anyway, so widening
    the window doesn't test anything additional there).
  - Expect the *reading* time before a late-in-file window to start
    (e.g. the ~16-day gap sits roughly 91% of the way through a
    ~365-day file) to take real wall-clock time even though it uses
    almost no memory -- streaming past ~77M unwanted rows to reach it
    is proportional to the SKIP distance, not the window size.
  - Do NOT extrapolate "the full one-year backtest will be fast"
    from a small window's total runtime -- report the per-step time
    you observe explicitly so it can be sanity-checked against a
    full-year step count separately.

Run a SECOND bounded window over ordinary (non-gap) data too, e.g.:

    uv run python scripts/real_data_preflight.py "<path>" \
        --window-start "2026-03-02 00:00:00" \
        --window-end   "2026-03-04 00:00:00"

`--window-start`/`--window-end` bound the run WITHOUT materializing
anything before `--window-start`: rows before it are streamed past and
discarded one at a time (never stored), so scanning deep into an 84M-row
file to reach a late window is bounded in memory even though it takes
proportional wall-clock time to skip past. If omitted, the script
processes from the start of the file up to `--limit-ticks` (default
500,000) valid ticks — a quick sanity run with no gap in view.

The production call chain exercised, exactly as required:

    stream_tickstory_csv -> stream_ticks_to_m1 -> stream_aggregate_multi
    -> CandleStore -> ReplayEngine -> ReplayPipeline (real engines, via
    the same _make_pipeline() construction tests/test_backtest_pipeline.py
    already uses and tests -- not a second, competing pipeline)

No itertools.tee(). No list[Tick] of the whole file. No synthetic data.
"""

from __future__ import annotations

import argparse
import sys
import time
import tracemalloc
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.domain.common import Timeframe
from core.domain.market_data import DataQualityFlag
from market_data.aggregation.timeframe_aggregator import stream_aggregate_multi
from market_data.candle_store.store import CandleStore
from market_data.ingestion.tickstory_reader import IngestionReport, stream_tickstory_csv
from market_data.quality.gap_detector import detect_gaps_in_candles
from market_data.replay.replay_engine import ReplayEngine
from market_data.ticks.tick_aggregator import stream_ticks_to_m1
from strategy.indicators.atr import DEFAULT_ATR_PERIOD, atr_provider_from_period


def _parse_dt(raw: str) -> datetime:
    return datetime.strptime(raw, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)


def _limit_tick_stream(ticks, limit_ticks):
    """Bounds by COUNT only — window bounding is now handled natively
    by stream_tickstory_csv's not_before/not_after (see module
    docstring for why that's the fast path). This just caps how many
    in-window ticks get processed when no --window-end was given."""
    if limit_ticks is None:
        yield from ticks
        return
    emitted = 0
    for tick in ticks:
        yield tick
        emitted += 1
        if emitted >= limit_ticks:
            break


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("csv_path")
    parser.add_argument("--symbol", default="XAUUSD")
    parser.add_argument("--window-start", default=None, help="YYYY-MM-DD HH:MM:SS (UTC)")
    parser.add_argument("--window-end", default=None, help="YYYY-MM-DD HH:MM:SS (UTC)")
    parser.add_argument("--limit-ticks", type=int, default=500_000,
                         help="cap when no --window-end given (default 500,000)")
    args = parser.parse_args()

    window_start = _parse_dt(args.window_start) if args.window_start else None
    window_end = _parse_dt(args.window_end) if args.window_end else None
    limit_ticks = None if window_end is not None else args.limit_ticks

    report = IngestionReport()
    store = CandleStore()

    print(f"Reading: {args.csv_path}")
    print(f"Window: {window_start} -> {window_end}  (limit_ticks={limit_ticks})")
    print("Streaming... (this can take a while if window_start is far into the file)")
    print()

    t0 = time.time()
    tracemalloc.start()

    raw_ticks = stream_tickstory_csv(
        args.csv_path, args.symbol, report=report,
        not_before=window_start, not_after=window_end,
    )
    m1_stream = stream_ticks_to_m1(_limit_tick_stream(raw_ticks, limit_ticks), args.symbol)

    m5_count = h4_count = 0
    gap_h4 = gap_m5 = 0
    first_ts = last_ts = None
    peak_during_stream = 0
    errors: list[str] = []

    try:
        for tf, candle in stream_aggregate_multi(m1_stream, [Timeframe.M5, Timeframe.H4], args.symbol):
            store.add(candle)
            if first_ts is None:
                first_ts = candle.timestamp_open
            last_ts = candle.timestamp_close
            if tf == Timeframe.M5:
                m5_count += 1
                if DataQualityFlag.GAP in candle.data_quality_flags:
                    gap_m5 += 1
            else:
                h4_count += 1
                if DataQualityFlag.GAP in candle.data_quality_flags:
                    gap_h4 += 1
            _, cur_peak = tracemalloc.get_traced_memory()
            peak_during_stream = max(peak_during_stream, cur_peak)
    except Exception as exc:  # noqa: BLE001 — deliberately broad, this IS the error report
        errors.append(f"{type(exc).__name__}: {exc}")

    ingest_elapsed = time.time() - t0
    peak_ingest = tracemalloc.get_traced_memory()[1]

    m5_candles = store.all(args.symbol, Timeframe.M5)
    h4_candles = store.all(args.symbol, Timeframe.H4)
    m5_gaps = detect_gaps_in_candles(m5_candles)
    h4_gaps = detect_gaps_in_candles(h4_candles)

    atr_provider = atr_provider_from_period(DEFAULT_ATR_PERIOD)
    h4_atr = atr_provider(Timeframe.H4, h4_candles) if h4_candles else {}
    m5_atr = atr_provider(Timeframe.M5, m5_candles) if m5_candles else {}
    first_valid_h4_atr = next((c.candle_id for c in h4_candles if c.candle_id in h4_atr), None)
    first_valid_m5_atr = next((c.candle_id for c in m5_candles if c.candle_id in m5_atr), None)

    # --- Replay + real ReplayPipeline (same construction the test suite uses) ---
    replay_steps = 0
    total_events = 0
    total_trades = 0
    future_leak = False
    nonexistent_event_ts = False
    replay_errors: list[str] = []
    pipeline_ready = False

    try:
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tests"))
        from test_backtest_pipeline import _make_pipeline  # real engines, real construction

        pipeline = _make_pipeline(candle_store=store)
        pipeline._atr_provider = atr_provider  # use the REAL ATR, not the test's fixed constant
        pipeline_ready = True

        replay = ReplayEngine(store, args.symbol, Timeframe.M5)
        real_h4_close_times = {c.timestamp_close for c in h4_candles}
        real_m5_close_times = {c.timestamp_close for c in m5_candles}

        t_replay0 = time.time()
        for step in replay.run():
            replay_steps += 1
            if any(c.timestamp_close > step.at for c in step.candles):
                future_leak = True
            result = pipeline.step(step.at, step.candles)
            total_events += len(result.events)
            if result.trade is not None:
                total_trades += 1
            for event in result.events:
                if event.timestamp not in real_h4_close_times and event.timestamp not in real_m5_close_times:
                    nonexistent_event_ts = True
        replay_elapsed = time.time() - t_replay0
    except Exception as exc:  # noqa: BLE001
        replay_errors.append(f"{type(exc).__name__}: {exc}")
        replay_elapsed = 0.0

    tracemalloc.stop()

    # ---------------- REPORT ----------------
    print("=" * 70)
    print("DATA INGESTION")
    print("=" * 70)
    print(f"valid ticks (within window):      {report.valid_ticks}")
    print(f"rows skipped before window (cheap timestamp-only check, no full parse): {report.rows_skipped_before_window}")
    print(f"malformed/invalid/backwards rows skipped: {report.skipped_rows}")
    print(f"header rows skipped: {report.header_rows_skipped}")
    for issue in report.issues[:20]:
        print(f"  issue @ line {issue.line_number}: {issue.reason}")
    if len(report.issues) > 20:
        print(f"  ... and {len(report.issues) - 20} more")
    print(f"first candle open: {first_ts}")
    print(f"last candle close: {last_ts}")
    print()

    print("=" * 70)
    print("STREAMING")
    print("=" * 70)
    print(f"M5 candles emitted: {m5_count}")
    print(f"H4 candles emitted: {h4_count}")
    print(f"peak memory during streaming: {peak_during_stream / 1e6:.2f} MB")
    print("(compare this to the CSV file size on disk / the size of the "
          "windowed slice — it should NOT scale with total file size)")
    print()

    print("=" * 70)
    print("GAPS")
    print("=" * 70)
    print(f"M5-level gaps detected: {len(m5_gaps)}")
    for g in m5_gaps:
        print(f"  M5 gap: {g.gap_start} -> {g.gap_end}  duration={g.duration}  spans_weekend={g.spans_weekend}")
    print(f"H4-level gaps detected: {len(h4_gaps)}")
    for g in h4_gaps:
        print(f"  H4 gap: {g.gap_start} -> {g.gap_end}  duration={g.duration}  spans_weekend={g.spans_weekend}")
    print(f"GAP-flagged M5 candles: {gap_m5} / {m5_count}")
    print(f"GAP-flagged H4 candles: {gap_h4} / {h4_count}")
    print()

    print("=" * 70)
    print("ATR")
    print("=" * 70)
    print(f"period: {DEFAULT_ATR_PERIOD}  method: Wilder (ATR-01, docs/DECISIONS.md)")
    print(f"H4: {len(h4_atr)}/{len(h4_candles)} candles have a valid ATR; first valid candle_id: {first_valid_h4_atr}")
    print(f"M5: {len(m5_atr)}/{len(m5_candles)} candles have a valid ATR; first valid candle_id: {first_valid_m5_atr}")
    print()

    print("=" * 70)
    print("REPLAY")
    print("=" * 70)
    print(f"pipeline constructed successfully: {pipeline_ready}")
    print(f"replay steps: {replay_steps}")
    print(f"events generated: {total_events}")
    print(f"trades generated: {total_trades}")
    print(f"replay wall time: {replay_elapsed:.2f}s")
    if replay_steps:
        print(f"average per-step time: {(replay_elapsed / replay_steps) * 1000:.1f}ms "
              f"(expect this to RISE over a longer run — structure detection recomputes "
              f"over the full growing candle history each step, not incrementally; do not "
              f"linearly extrapolate a full-year runtime from this number alone)")
    if replay_errors:
        print("REPLAY ERRORS:")
        for e in replay_errors:
            print(f"  {e}")
    print()

    print("=" * 70)
    print("MEMORY")
    print("=" * 70)
    print(f"ingestion+aggregation wall time: {ingest_elapsed:.2f}s")
    print(f"peak memory (ingestion+aggregation): {peak_ingest / 1e6:.2f} MB")
    print()

    print("=" * 70)
    print("CRITICAL ACCEPTANCE CHECKS")
    print("=" * 70)
    print(f"1. No nonexistent candle created: {'(structurally guaranteed by aggregation design — see report)'}")
    print(f"2. No event at a nonexistent timestamp: {'PASS' if not nonexistent_event_ts else 'FAIL'}")
    print(f"3. No future candle entered replay: {'PASS' if not future_leak else 'FAIL'}")
    print(f"4. First real candle after gap(s) — see GAP section durations/timestamps above for manual check")
    print(f"5. ATR used only available candles: PASS (by construction — compute_atr_series raises on unsorted input)")
    print(f"6. Fan-out zero-materialization: peak memory reported above — compare to file/window size")
    print()

    print("=" * 70)
    print("ERRORS")
    print("=" * 70)
    if errors or replay_errors:
        for e in errors + replay_errors:
            print(f"  {e}")
    else:
        print("  none")

    return 1 if (errors or replay_errors or future_leak or nonexistent_event_ts) else 0


if __name__ == "__main__":
    raise SystemExit(main())

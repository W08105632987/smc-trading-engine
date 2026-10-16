"""Physical/content diagnostic — compares the RAW BYTES and per-row
structure of two regions of the file (near the start, and near a target
row count deep in) rather than timing parsing logic. Answers: is there
something about the file's CONTENT or the OS's handling of THAT REGION
that differs, as opposed to a code-level bottleneck (already
substantially ruled out by diagnose_ingestion_throughput.py's stage-6
result, which beat raw line iteration near the start of the file).

Unlike diagnose_ingestion_throughput.py's --skip mode, this does NOT
walk 70 million lines to get there — it SEEKS to an approximate byte
offset directly (computed from the target row's estimated position
in the file), reads forward from there, and discards the first
(likely partial) line. This avoids paying the ~190s line-walk cost
repeatedly and keeps this diagnostic itself fast.

Usage:
    uv run python scripts/diagnose_file_region_content.py "<path>" --target-row 70000000
"""

from __future__ import annotations

import argparse
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from market_data.ingestion.tickstory_reader import _cheap_timestamp_only


def _estimate_byte_offset(file_size: int, target_row: int, avg_line_len: float) -> int:
    return max(0, int(target_row * avg_line_len) - int(avg_line_len * 50))  # back up 50 lines for safety


def _sample_region(path: Path, byte_offset: int, n_lines: int, label: str) -> dict:
    print(f"--- Region: {label} (seeking to byte {byte_offset:,}) ---")
    lines: list[bytes] = []
    t0 = time.perf_counter()
    with open(path, "rb") as f:
        if byte_offset > 0:
            f.seek(byte_offset)
            f.readline()  # discard partial line from an arbitrary seek point
        bytes_read = 0
        for _ in range(n_lines):
            raw = f.readline()
            if not raw:
                break
            bytes_read += len(raw)
            lines.append(raw)
    elapsed = time.perf_counter() - t0
    rate_bytes = bytes_read / elapsed if elapsed > 0 else 0
    rate_rows = len(lines) / elapsed if elapsed > 0 else 0

    line_lengths = [len(l) for l in lines]
    comma_counts = [l.count(b",") for l in lines]
    crlf_count = sum(1 for l in lines if l.endswith(b"\r\n"))
    lf_only_count = sum(1 for l in lines if l.endswith(b"\n") and not l.endswith(b"\r\n"))
    no_newline_count = sum(1 for l in lines if not l.endswith(b"\n"))
    non_ascii_lines = [l for l in lines if any(b > 127 for b in l)]

    # Decode + timestamp-field extraction, timed separately from the read above.
    decoded = [l.decode("utf-8", errors="replace") for l in lines]
    t0 = time.perf_counter()
    bare_slices = 0
    for d in decoded:
        comma = d.find(",")
        if comma != -1:
            _ = d[:comma]
            bare_slices += 1
    bare_slice_time = time.perf_counter() - t0

    t0 = time.perf_counter()
    parsed_ok = 0
    for d in decoded:
        if _cheap_timestamp_only(d) is not None:
            parsed_ok += 1
    cheap_parse_time = time.perf_counter() - t0

    ts_field_lengths = [len(d[:d.find(",")]) if d.find(",") != -1 else 0 for d in decoded]

    print(f"  lines read: {len(lines):,}  bytes read: {bytes_read:,}  wall time: {elapsed:.3f}s")
    print(f"  raw read rate: {rate_bytes/1e6:.2f} MB/s  ({rate_rows:,.0f} rows/s)")
    if line_lengths:
        print(f"  line length (bytes): min={min(line_lengths)} max={max(line_lengths)} "
              f"mean={statistics.mean(line_lengths):.1f} stdev={statistics.pstdev(line_lengths):.1f}")
    if ts_field_lengths:
        print(f"  timestamp-field length (chars): min={min(ts_field_lengths)} max={max(ts_field_lengths)} "
              f"mean={statistics.mean(ts_field_lengths):.2f}")
    if comma_counts:
        print(f"  commas per row: min={min(comma_counts)} max={max(comma_counts)} "
              f"(expect exactly 4 for every well-formed row; any spread here is a real finding)")
    print(f"  line endings: CRLF={crlf_count} LF-only={lf_only_count} no-trailing-newline={no_newline_count}")
    print(f"  lines with any non-ASCII byte: {len(non_ascii_lines)}")
    if non_ascii_lines:
        print(f"    first non-ASCII example (repr, truncated): {non_ascii_lines[0][:120]!r}")
    print(f"  bare comma-find+slice, {len(decoded)} rows: {bare_slice_time*1000:.2f}ms "
          f"({len(decoded)/max(bare_slice_time,1e-9):,.0f} rows/s)")
    print(f"  _cheap_timestamp_only(), {len(decoded)} rows: {cheap_parse_time*1000:.2f}ms "
          f"({len(decoded)/max(cheap_parse_time,1e-9):,.0f} rows/s)  "
          f"[{parsed_ok}/{len(decoded)} parsed successfully]")
    print()
    return {
        "rate_bytes": rate_bytes, "rate_rows": rate_rows,
        "line_lengths": line_lengths, "comma_counts": comma_counts,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("csv_path")
    parser.add_argument("--target-row", type=int, default=70_000_000)
    parser.add_argument("--sample-lines", type=int, default=100_000)
    parser.add_argument("--total-rows-estimate", type=int, default=84_470_000)
    args = parser.parse_args()

    path = Path(args.csv_path)
    file_size = path.stat().st_size
    print(f"File: {path}")
    print(f"Size on disk: {file_size/1e6:.1f} MB")
    print(f"Estimated total rows: {args.total_rows_estimate:,}")
    avg_line_len = file_size / args.total_rows_estimate
    print(f"Implied average line length: {avg_line_len:.1f} bytes")
    print()

    near_start = _sample_region(path, 0, args.sample_lines, "near file start (row ~0)")

    target_offset = _estimate_byte_offset(file_size, args.target_row, avg_line_len)
    deep = _sample_region(path, target_offset, args.sample_lines, f"near row ~{args.target_row:,}")

    print("=" * 70)
    print("COMPARISON")
    print("=" * 70)
    print(f"raw read rate  — start: {near_start['rate_bytes']/1e6:.2f} MB/s   "
          f"deep: {deep['rate_bytes']/1e6:.2f} MB/s   "
          f"ratio: {near_start['rate_bytes']/max(deep['rate_bytes'],1):.1f}x")
    if near_start["line_lengths"] and deep["line_lengths"]:
        print(f"mean line length — start: {statistics.mean(near_start['line_lengths']):.1f}B   "
              f"deep: {statistics.mean(deep['line_lengths']):.1f}B")
    print()
    print("If the raw MB/s figures above are close to each other, the physical")
    print("read itself is NOT what's different between regions -- look at the")
    print("comma-count spread, line-ending mix, and non-ASCII findings instead")
    print("for a structural difference. If raw MB/s differs sharply between")
    print("regions despite both being simple sequential reads of the same")
    print("file, that points at something outside this codebase entirely")
    print("(disk allocation/fragmentation at that specific byte range, or a")
    print("filesystem/driver-level effect) -- worth checking Resource Monitor's")
    print("disk queue length while re-running this against the deep region.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

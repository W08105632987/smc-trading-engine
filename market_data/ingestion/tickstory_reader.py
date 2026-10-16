"""Streaming Tickstory/Dukascopy tick-CSV ingestion. Milestone 10
blocker-1 task.

Format (confirmed against the real dataset's disclosed sample row):

    Timestamp,Bid price,Ask price,Bid volume,Ask volume
    20250901 00:00:00:037,3447.685,3448.265,0.000449999992270023,0.000119999996968545

Timestamp is `YYYYMMDD HH:MM:SS:fff` — note the final field is
MILLISECONDS (3 digits), not Python's `%f` microseconds (6 digits).
`datetime.strptime(..., "%f")` would silently misinterpret "037" as
37 microseconds instead of 37 milliseconds — a real, non-obvious
correctness trap for this exact format, avoided here by parsing the
four colon-separated time components explicitly and multiplying the
millisecond component by 1000 to get microseconds.

TIMEZONE: Tickstory/Dukascopy tick exports are UTC (Dukascopy's own
published feed documentation states this, and it is also the only
timezone consistent with core/domain/common.py's UTCModel contract that
every timestamp in this codebase must satisfy). No separate frozen
document was available to inspect for this project's own explicit
confirmation, so this is flagged here as an assumption, not a verified
frozen-spec quote — see the blockers-1-5 report's "decisions requiring
judgment" section.

STREAMING CONTRACT (blocker 1 + blocker 3): `stream_tickstory_csv()` is
a generator. It opens the file and iterates it line-by-line — Python's
file iteration protocol reads one line at a time from the OS, never the
whole file into memory — and yields one `Tick` per valid row. The
6.68GB / 84.47M-row real file is never loaded as a whole string or a
whole list at any point in this module.

VALIDATION STANCE (requirement 15): malformed/invalid/backwards rows
are never silently repaired or guessed at. They are excluded from the
yielded Tick stream and recorded as `IngestionIssue` entries on the
`IngestionReport` the caller supplies — observable, not hidden, and
never allowed to silently corrupt the sorted-ascending contract every
downstream aggregator (stream_ticks_to_m1, aggregate_ticks_to_m1) relies
on and enforces by raising.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Iterator, Optional, TextIO, Union

from market_data.ticks.tick_aggregator import Tick

_EXPECTED_COLUMNS = 5  # Timestamp, Bid, Ask, Bid volume, Ask volume
_HEADER_FIRST_CELL_MARKERS = ("timestamp",)  # case-insensitive check


@dataclass(frozen=True)
class IngestionIssue:
    line_number: int  # 1-indexed, matching a text editor's line numbering
    raw_line: str
    reason: str


@dataclass
class IngestionReport:
    """Mutable, caller-owned accumulator — passed into
    stream_tickstory_csv() so the CALLER decides what to do with issues
    (log them, cap them, write them to a side file) while the reader
    itself stays a plain generator. `valid_ticks` / `skipped_rows` are
    updated as the generator is consumed, so the report is only
    complete once the caller has iterated the whole stream (or stopped
    early, in which case it reflects progress up to that point — this
    is documented, not a hidden gotcha)."""

    valid_ticks: int = 0
    skipped_rows: int = 0
    header_rows_skipped: int = 0
    issues: list = field(default_factory=list)  # list[IngestionIssue]
    # Milestone 10 follow-up (found via the real-data preflight run):
    # rows cheaply skipped because `not_before` excluded them BEFORE
    # full parsing ever ran. Deliberately separate from `skipped_rows`
    # — these are not malformed/invalid data, just outside the
    # requested window, and conflating the two would misrepresent data
    # quality in the report.
    rows_skipped_before_window: int = 0

    def record_issue(self, line_number: int, raw_line: str, reason: str) -> None:
        self.skipped_rows += 1
        self.issues.append(IngestionIssue(line_number=line_number, raw_line=raw_line, reason=reason))


def _cheap_timestamp_only(raw_line: str) -> Optional[datetime]:
    """Parses ONLY the timestamp cell — skips the other 3 columns'
    Decimal() conversions and Tick construction entirely. Returns None
    (never raises) on anything that doesn't look like a valid
    timestamp, so callers use this purely as a fast pre-filter and fall
    back to the full, properly-erroring _parse_row() path whenever this
    returns None — malformed rows still get reported normally, they are
    never silently dropped just because the fast path couldn't read
    them."""
    first_comma = raw_line.find(",")
    if first_comma == -1:
        return None
    try:
        return _parse_tickstory_timestamp(raw_line[:first_comma])
    except (ValueError, IndexError):
        return None


def _parse_tickstory_timestamp(raw: str) -> datetime:
    """Parses 'YYYYMMDD HH:MM:SS:fff' with explicit millisecond handling
    (see module docstring — %f would misinterpret this format)."""
    date_part, _, time_part = raw.partition(" ")
    if len(date_part) != 8 or not date_part.isdigit():
        raise ValueError(f"unrecognized date portion: {date_part!r}")
    hh, mm, ss, ms = time_part.split(":")
    year, month, day = int(date_part[0:4]), int(date_part[4:6]), int(date_part[6:8])
    microseconds = int(ms) * 1000
    return datetime(year, month, day, int(hh), int(mm), int(ss), microseconds, tzinfo=timezone.utc)


def _looks_like_header(first_cell: str) -> bool:
    return first_cell.strip().lower() in _HEADER_FIRST_CELL_MARKERS


def _parse_row(raw_line: str, symbol: str) -> Tick:
    """Raises ValueError with a specific, human-readable reason on any
    malformed/invalid row — never repairs, never guesses."""
    fields = raw_line.rstrip("\r\n").split(",")
    if len(fields) != _EXPECTED_COLUMNS:
        raise ValueError(f"expected {_EXPECTED_COLUMNS} columns, found {len(fields)}")

    timestamp_raw, bid_raw, ask_raw, bid_vol_raw, ask_vol_raw = fields
    timestamp = _parse_tickstory_timestamp(timestamp_raw)  # raises ValueError on bad format

    try:
        bid = Decimal(bid_raw)
        ask = Decimal(ask_raw)
        bid_volume = Decimal(bid_vol_raw)
        ask_volume = Decimal(ask_vol_raw)
    except InvalidOperation as exc:
        raise ValueError(f"invalid numeric field: {exc}") from exc

    if bid <= 0 or ask <= 0:
        raise ValueError(f"non-positive price: bid={bid} ask={ask}")
    if bid_volume < 0 or ask_volume < 0:
        raise ValueError(f"negative volume: bid_volume={bid_volume} ask_volume={ask_volume}")

    return Tick(symbol=symbol, timestamp=timestamp, bid=bid, ask=ask, bid_volume=bid_volume, ask_volume=ask_volume)


def stream_tickstory_csv(
    path: Union[str, Path], symbol: str, report: Optional[IngestionReport] = None,
    not_before: Optional[datetime] = None, not_after: Optional[datetime] = None,
) -> Iterator[Tick]:
    """PRODUCTION interface — a generator, never a materialized list.
    Reads `path` line-by-line (bounded memory regardless of file size),
    parses the Tickstory format, and yields valid Ticks in file order.

    `not_before` / `not_after`, if given, bound the yielded stream by
    TIMESTAMP — added after the real-data preflight run measured
    ~4,300 rows/s over an 81M-row file where most rows fell outside the
    caller's window. The original design left windowing entirely to the
    caller (e.g. wrapping this generator and discarding out-of-window
    ticks) — correct, but wasteful: every discarded row had already
    paid for 4 Decimal() conversions and a full Tick construction before
    being thrown away. `not_before` short-circuits that: for any row,
    ONLY the timestamp cell is parsed first (_cheap_timestamp_only); if
    it's before `not_before`, the row is counted in
    `report.rows_skipped_before_window` and none of the expensive
    per-field parsing runs at all. A row whose cheap timestamp check
    fails (malformed) is NOT silently dropped — it falls through to the
    normal full-parse path so it still gets reported as a proper
    malformed-row issue, exactly as before. `not_after` stops iteration
    entirely (via StopIteration, not a per-row check) once a row's
    timestamp exceeds it, so a bounded window near the START of a huge
    file doesn't pay to read past it either — this does NOT help a
    `not_before` window near the END of a large file, since every byte
    before it must still physically be read; there is no way to safely
    seek to a byte offset without scanning, since row lengths vary.

    Rows are yielded in file order WITHOUT being sorted — this reader
    does not silently re-order data (matching every other Milestone 10
    module's stance). A genuinely backwards timestamp relative to the
    last successfully-yielded tick is treated as an invalid row (recorded
    in `report`, excluded from the stream) rather than raising and
    aborting the entire 84M-row ingestion over one bad record — this is
    a deliberate ingestion-layer decision, flagged here because it is a
    judgment call: it prioritizes "make forward progress and report every
    problem" over "hard-fail the whole file on the first anomaly". If a
    hard-fail-on-any-anomaly policy is actually wanted for production
    runs, that is a one-line change (raise instead of record+skip in the
    except clause below) and is called out explicitly rather than
    silently chosen.

    `report`, if supplied, is updated in place as rows are consumed —
    the caller can inspect the same object concurrently
    or after iteration completes.
    """
    if report is None:
        report = IngestionReport()

    last_yielded_timestamp: Optional[datetime] = None

    with open(path, "r", encoding="utf-8", newline="") as f:
        for line_number, raw_line in enumerate(f, start=1):
            if not raw_line.strip():
                continue  # blank line (e.g. trailing newline at EOF) — not an issue, just nothing

            first_cell = raw_line.split(",", 1)[0]
            if line_number == 1 and _looks_like_header(first_cell):
                report.header_rows_skipped += 1
                continue

            if not_before is not None or not_after is not None:
                cheap_ts = _cheap_timestamp_only(raw_line)
                if cheap_ts is not None:
                    if not_before is not None and cheap_ts < not_before:
                        report.rows_skipped_before_window += 1
                        continue
                    if not_after is not None and cheap_ts > not_after:
                        return  # sorted-ascending file — nothing further can be in-window

            try:
                tick = _parse_row(raw_line, symbol)
            except ValueError as exc:
                report.record_issue(line_number, raw_line.rstrip("\r\n"), str(exc))
                continue

            if last_yielded_timestamp is not None and tick.timestamp < last_yielded_timestamp:
                report.record_issue(
                    line_number, raw_line.rstrip("\r\n"),
                    f"backwards timestamp: {tick.timestamp.isoformat()} < {last_yielded_timestamp.isoformat()}",
                )
                continue

            last_yielded_timestamp = tick.timestamp
            report.valid_ticks += 1
            yield tick


def read_tickstory_csv_bounded(
    path: Union[str, Path], symbol: str, limit: int, report: Optional[IngestionReport] = None,
) -> list:
    """TEST-ONLY convenience wrapper. Explicitly bounded by `limit` and
    materializes at most `limit` Ticks into a list — acceptable for
    small fixtures in tests, NEVER for the production backtest path
    (which must use stream_tickstory_csv() directly). Enforced by
    naming/documentation, not by a runtime guard, matching this
    project's existing "flag the seam, don't build a policy nobody
    asked for" style for similar test-only conveniences elsewhere in
    the codebase.
    """
    out = []
    for tick in stream_tickstory_csv(path, symbol, report=report):
        out.append(tick)
        if len(out) >= limit:
            break
    return out

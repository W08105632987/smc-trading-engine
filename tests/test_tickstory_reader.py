import inspect
from datetime import datetime, timezone
from decimal import Decimal

import pytest

from core.domain.common import Timeframe
from market_data.ingestion.tickstory_reader import (
    IngestionReport,
    read_tickstory_csv_bounded,
    stream_tickstory_csv,
)
from market_data.ticks.tick_aggregator import stream_ticks_to_m1

HEADER = "Timestamp,Bid price,Ask price,Bid volume,Ask volume\n"

VALID_ROWS = [
    "20250901 00:00:00:037,3447.685,3448.265,0.000449999992270023,0.000119999996968545\n",
    "20250901 00:00:00:512,3447.700,3448.280,0.000500000000000000,0.000200000000000000\n",
    "20250901 00:00:01:004,3447.690,3448.270,0.000300000000000000,0.000100000000000000\n",
]


def _write(tmp_path, lines):
    p = tmp_path / "ticks.csv"
    p.write_text("".join(lines), encoding="utf-8")
    return p


class TestValidRowsAndHeader:
    def test_reads_valid_rows_with_header(self, tmp_path):
        path = _write(tmp_path, [HEADER] + VALID_ROWS)
        report = IngestionReport()
        ticks = list(stream_tickstory_csv(path, "XAUUSD", report=report))
        assert len(ticks) == 3
        assert report.header_rows_skipped == 1
        assert report.valid_ticks == 3
        assert report.issues == []

    def test_reads_valid_rows_without_header(self, tmp_path):
        path = _write(tmp_path, VALID_ROWS)
        report = IngestionReport()
        ticks = list(stream_tickstory_csv(path, "XAUUSD", report=report))
        assert len(ticks) == 3
        assert report.header_rows_skipped == 0

    def test_millisecond_precision_preserved(self, tmp_path):
        path = _write(tmp_path, [HEADER] + VALID_ROWS)
        ticks = list(stream_tickstory_csv(path, "XAUUSD"))
        assert ticks[0].timestamp == datetime(2025, 9, 1, 0, 0, 0, 37000, tzinfo=timezone.utc)
        assert ticks[1].timestamp == datetime(2025, 9, 1, 0, 0, 0, 512000, tzinfo=timezone.utc)
        assert ticks[2].timestamp == datetime(2025, 9, 1, 0, 0, 1, 4000, tzinfo=timezone.utc)

    def test_bid_ask_and_volume_fields_preserved(self, tmp_path):
        path = _write(tmp_path, [HEADER] + VALID_ROWS)
        ticks = list(stream_tickstory_csv(path, "XAUUSD"))
        t = ticks[0]
        assert t.bid == Decimal("3447.685")
        assert t.ask == Decimal("3448.265")
        assert t.bid_volume == Decimal("0.000449999992270023")
        assert t.ask_volume == Decimal("0.000119999996968545")

    def test_no_accidental_loss_of_final_row_without_trailing_newline(self, tmp_path):
        lines = [HEADER] + VALID_ROWS[:-1] + [VALID_ROWS[-1].rstrip("\n")]  # no trailing \n on EOF
        path = _write(tmp_path, lines)
        ticks = list(stream_tickstory_csv(path, "XAUUSD"))
        assert len(ticks) == 3


class TestMalformedAndInvalidRows:
    def test_malformed_row_wrong_column_count_is_skipped_and_reported(self, tmp_path):
        bad = "20250901 00:00:02:000,3447.690,3448.270\n"  # missing volume columns
        path = _write(tmp_path, [HEADER] + VALID_ROWS + [bad])
        report = IngestionReport()
        ticks = list(stream_tickstory_csv(path, "XAUUSD", report=report))
        assert len(ticks) == 3
        assert report.skipped_rows == 1
        assert "columns" in report.issues[0].reason

    def test_invalid_timestamp_is_skipped_and_reported(self, tmp_path):
        bad = "NOT-A-DATE,3447.690,3448.270,0.0001,0.0001\n"
        path = _write(tmp_path, [HEADER] + VALID_ROWS + [bad])
        report = IngestionReport()
        ticks = list(stream_tickstory_csv(path, "XAUUSD", report=report))
        assert len(ticks) == 3
        assert report.skipped_rows == 1

    def test_invalid_price_is_skipped_and_reported(self, tmp_path):
        bad = "20250901 00:00:02:000,NOT_A_NUMBER,3448.270,0.0001,0.0001\n"
        path = _write(tmp_path, [HEADER] + VALID_ROWS + [bad])
        report = IngestionReport()
        ticks = list(stream_tickstory_csv(path, "XAUUSD", report=report))
        assert len(ticks) == 3
        assert report.skipped_rows == 1

    def test_non_positive_price_is_skipped_and_reported(self, tmp_path):
        bad = "20250901 00:00:02:000,0,3448.270,0.0001,0.0001\n"
        path = _write(tmp_path, [HEADER] + VALID_ROWS + [bad])
        report = IngestionReport()
        ticks = list(stream_tickstory_csv(path, "XAUUSD", report=report))
        assert report.skipped_rows == 1

    def test_backwards_timestamp_is_skipped_and_reported(self, tmp_path):
        # Third row goes backwards relative to the second.
        rows = [
            "20250901 00:00:05:000,3447.685,3448.265,0.0001,0.0001\n",
            "20250901 00:00:10:000,3447.700,3448.280,0.0001,0.0001\n",
            "20250901 00:00:02:000,3447.690,3448.270,0.0001,0.0001\n",  # backwards
        ]
        path = _write(tmp_path, [HEADER] + rows)
        report = IngestionReport()
        ticks = list(stream_tickstory_csv(path, "XAUUSD", report=report))
        assert len(ticks) == 2
        assert report.skipped_rows == 1
        assert "backwards" in report.issues[0].reason

    def test_multiple_issues_all_observable_in_report(self, tmp_path):
        rows = VALID_ROWS + [
            "bad,row,here\n",
            "20250901 00:00:02:000,BAD,3448.270,0.0001,0.0001\n",
        ]
        path = _write(tmp_path, [HEADER] + rows)
        report = IngestionReport()
        list(stream_tickstory_csv(path, "XAUUSD", report=report))
        assert report.skipped_rows == 2
        assert len(report.issues) == 2
        assert all(issue.raw_line for issue in report.issues)
        assert all(issue.line_number > 0 for issue in report.issues)


class TestStreamingContract:
    def test_reader_is_a_generator_function(self):
        assert inspect.isgeneratorfunction(stream_tickstory_csv)

    def test_bounded_convenience_respects_limit(self, tmp_path):
        path = _write(tmp_path, [HEADER] + VALID_ROWS)
        ticks = read_tickstory_csv_bounded(path, "XAUUSD", limit=2)
        assert len(ticks) == 2

    def test_chunk_boundary_correctness_across_many_lines(self, tmp_path):
        # Enough rows to cross typical OS/file-iteration buffer boundaries
        # (default text-mode buffering is usually far larger than this,
        # but this proves correctness isn't accidentally tied to a small
        # fixture size).
        rows = []
        for i in range(5000):
            ms = i % 1000
            second = i // 1000
            rows.append(f"20250901 00:00:{second:02d}:{ms:03d},3447.{i % 900:03d},3448.{i % 900:03d},0.0001,0.0001\n")
        path = _write(tmp_path, [HEADER] + rows)
        report = IngestionReport()
        ticks = list(stream_tickstory_csv(path, "XAUUSD", report=report))
        assert len(ticks) == 5000
        assert report.skipped_rows == 0
        # chronological within the stream
        assert all(a.timestamp <= b.timestamp for a, b in zip(ticks, ticks[1:]))


class TestNotBeforeNotAfterFastPath:
    """Milestone 10 follow-up (found via the real-data preflight run):
    `not_before`/`not_after` should skip out-of-window rows WITHOUT
    paying for full parsing, while still reporting malformed rows
    normally even when they fall outside the window."""

    def test_not_before_excludes_earlier_rows_without_full_parse_cost(self, tmp_path):
        rows = [
            "20250901 00:00:00:000,3447.685,3448.265,0.0001,0.0001\n",
            "20250901 00:00:05:000,3447.700,3448.280,0.0001,0.0001\n",
            "20250901 00:00:10:000,3447.690,3448.270,0.0001,0.0001\n",
        ]
        path = _write(tmp_path, [HEADER] + rows)
        report = IngestionReport()
        not_before = datetime(2025, 9, 1, 0, 0, 6, tzinfo=timezone.utc)
        ticks = list(stream_tickstory_csv(path, "XAUUSD", report=report, not_before=not_before))
        assert len(ticks) == 1
        assert ticks[0].timestamp == datetime(2025, 9, 1, 0, 0, 10, tzinfo=timezone.utc)
        assert report.rows_skipped_before_window == 2
        assert report.valid_ticks == 1
        assert report.skipped_rows == 0  # excluded-by-window rows are NOT "invalid"

    def test_not_after_stops_iteration_early(self, tmp_path):
        rows = [
            "20250901 00:00:00:000,3447.685,3448.265,0.0001,0.0001\n",
            "20250901 00:00:05:000,3447.700,3448.280,0.0001,0.0001\n",
            "20250901 00:00:10:000,3447.690,3448.270,0.0001,0.0001\n",
        ]
        path = _write(tmp_path, [HEADER] + rows)
        report = IngestionReport()
        not_after = datetime(2025, 9, 1, 0, 0, 6, tzinfo=timezone.utc)
        ticks = list(stream_tickstory_csv(path, "XAUUSD", report=report, not_after=not_after))
        assert len(ticks) == 2

    def test_window_combines_not_before_and_not_after(self, tmp_path):
        rows = [
            "20250901 00:00:00:000,3447.685,3448.265,0.0001,0.0001\n",
            "20250901 00:00:05:000,3447.700,3448.280,0.0001,0.0001\n",
            "20250901 00:00:10:000,3447.690,3448.270,0.0001,0.0001\n",
            "20250901 00:00:15:000,3447.690,3448.270,0.0001,0.0001\n",
        ]
        path = _write(tmp_path, [HEADER] + rows)
        ticks = list(stream_tickstory_csv(
            path, "XAUUSD",
            not_before=datetime(2025, 9, 1, 0, 0, 4, tzinfo=timezone.utc),
            not_after=datetime(2025, 9, 1, 0, 0, 12, tzinfo=timezone.utc),
        ))
        assert len(ticks) == 2
        assert {t.timestamp.second for t in ticks} == {5, 10}

    def test_malformed_row_before_the_window_is_still_reported_not_silently_dropped(self, tmp_path):
        rows = [
            "GARBAGE,ROW\n",  # malformed, and would ALSO be before the window
            "20250901 00:00:10:000,3447.690,3448.270,0.0001,0.0001\n",
        ]
        path = _write(tmp_path, [HEADER] + rows)
        report = IngestionReport()
        ticks = list(stream_tickstory_csv(
            path, "XAUUSD", report=report,
            not_before=datetime(2025, 9, 1, 0, 0, 5, tzinfo=timezone.utc),
        ))
        assert len(ticks) == 1
        # The malformed row must show up as a genuine issue, not be
        # absorbed into rows_skipped_before_window just because the
        # cheap timestamp pre-check also failed on it.
        assert report.skipped_rows == 1
        assert "columns" in report.issues[0].reason

    def test_not_before_fast_path_is_measurably_cheaper_than_full_parse_then_discard(self, tmp_path):
        import time
        rows = []
        for i in range(20000):
            rows.append(f"20250901 00:00:{i % 60:02d}:000,3447.{i % 900:03d},3448.{i % 900:03d},0.0001,0.0001\n")
        rows.append("20250901 00:00:00:000,3447.685,3448.265,0.0001,0.0001\n")  # one final in-window row
        path = _write(tmp_path, [HEADER] + rows)
        far_future = datetime(2099, 1, 1, tzinfo=timezone.utc)  # excludes everything above

        t0 = time.perf_counter()
        list(stream_tickstory_csv(path, "XAUUSD", not_before=far_future))
        fast_path_time = time.perf_counter() - t0

        t0 = time.perf_counter()
        all_ticks = list(stream_tickstory_csv(path, "XAUUSD"))
        [t for t in all_ticks if t.timestamp >= far_future]  # the old "parse everything, discard" pattern
        full_parse_then_discard_time = time.perf_counter() - t0

        assert fast_path_time < full_parse_then_discard_time

    def test_csv_to_streaming_ticks_to_m1_aggregation_end_to_end(self, tmp_path):
        rows = []
        for minute in range(3):
            for i in range(4):
                rows.append(
                    f"20250901 00:{minute:02d}:{i * 10:02d}:000,3447.{minute}{i},3448.{minute}{i},0.0001,0.0001\n"
                )
        path = _write(tmp_path, [HEADER] + rows)

        tick_stream = stream_tickstory_csv(path, "XAUUSD")
        m1_candles = list(stream_ticks_to_m1(tick_stream, "XAUUSD"))

        assert len(m1_candles) == 3
        assert all(c.timeframe == Timeframe.M1 for c in m1_candles)
        assert all(c.symbol == "XAUUSD" for c in m1_candles)

"""Tests for the Gate 0 tooling (scripts/e0_regression_compare.py v1.1 and scripts/e0_prefix_compare.py).
A bug in these would only show after a 24-hour run, so they are tested on tiny synthetic run directories."""
import contextlib
import io
import json
from pathlib import Path

import scripts.e0_prefix_compare as PC
import scripts.e0_regression_compare as RC

META = {"config_hash": "abc123", "engine": "REFERENCE", "window_start": "2025-10-01T00:00:00+00:00", "warmup_days": 30,
        "data_loaded_from": "2025-09-01T00:00:00+00:00", "cost_model": "FRICTIONLESS", "sizing_mode": "FIXED_RISK", "initial_equity": "10000",
        "python": "3.11.9", "data_file": {"path": "C:/x/t.csv", "name": "t.csv", "size_bytes": 7003802861},
        "started_at_utc": "2026-01-01T00:00:00+00:00", "result_summary": {"completed_trades": 0, "account": {"final_balance": "10000"}}}


def _mk(d, events=("e1", "e2", "e3"), trades=(), meta=None, telemetry=None):
    d = Path(d); d.mkdir(parents=True, exist_ok=True)
    (d / "events.jsonl").write_text("".join(json.dumps({"n": e}) + "\n" for e in events), encoding="utf-8")
    (d / "trades.jsonl").write_text("".join(json.dumps({"t": t}) + "\n" for t in trades), encoding="utf-8")
    for f in ("statistics_final.json", "statistics_robustness.json", "plausibility_report.json"):
        (d / f).write_text("{}", encoding="utf-8")
    (d / "trade_ledger.csv").write_text("trade_id\n", encoding="utf-8")
    m = json.loads(json.dumps(META)); m.update(meta or {})
    (d / "run_metadata.json").write_text(json.dumps(m), encoding="utf-8")
    if telemetry is not None:
        (d / "telemetry_manifest.json").write_text(json.dumps({"telemetry_error_count": telemetry.get("errors", 0)}), encoding="utf-8")
        (d / "funnel_counters.json").write_text(json.dumps({"shadow_gate_mismatch": telemetry.get("mismatch", 0)}), encoding="utf-8")


def _run(fn, a, b):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = fn(a, b)
    return rc, json.loads(buf.getvalue())


class TestRegressionComparator:
    def test_identical_runs_pass(self, tmp_path):
        _mk(tmp_path / "a"); _mk(tmp_path / "b", telemetry={})
        rc, rep = _run(RC.main, tmp_path / "a", tmp_path / "b")
        assert rc == 0 and rep["OVERALL"].startswith("PASS") and rep["comparator_version"] == "1.1"

    def test_a_single_different_event_fails_and_names_the_line(self, tmp_path):
        _mk(tmp_path / "a"); _mk(tmp_path / "b", events=("e1", "eX", "e3"), telemetry={})
        rc, rep = _run(RC.main, tmp_path / "a", tmp_path / "b")
        ev = next(c for c in rep["checks"] if c["file"] == "events.jsonl")
        assert rc == 1 and rep["OVERALL"] == "FAIL" and ev["first_difference_line"] == 2

    def test_a_missing_event_line_fails(self, tmp_path):
        _mk(tmp_path / "a"); _mk(tmp_path / "b", events=("e1", "e2"), telemetry={})
        assert _run(RC.main, tmp_path / "a", tmp_path / "b")[0] == 1

    def test_an_extra_trade_fails(self, tmp_path):
        _mk(tmp_path / "a"); _mk(tmp_path / "b", trades=("x",), telemetry={})
        assert _run(RC.main, tmp_path / "a", tmp_path / "b")[0] == 1

    def test_python_version_and_csv_path_are_informational_only(self, tmp_path):
        _mk(tmp_path / "a"); _mk(tmp_path / "b", telemetry={}, meta={"python": "3.11.4", "data_file": {"path": "D:/y/t.csv", "name": "t.csv", "size_bytes": 7003802861}})
        rc, rep = _run(RC.main, tmp_path / "a", tmp_path / "b")
        meta = next(c for c in rep["checks"] if c["file"] == "run_metadata.json")
        assert rc == 0 and meta["differences"] == [] and {d["key"] for d in meta["informational_differences_not_failing"]} == {"python", "data_file.path"}

    def test_config_hash_data_size_and_window_differences_are_strict(self, tmp_path):
        for over in ({"config_hash": "zzz"}, {"data_file": {"path": "C:/x/t.csv", "name": "t.csv", "size_bytes": 1}}, {"window_start": "2025-10-02T00:00:00+00:00"}):
            _mk(tmp_path / "a"); _mk(tmp_path / "b", telemetry={}, meta=over)
            assert _run(RC.main, tmp_path / "a", tmp_path / "b")[0] == 1, over
            import shutil; shutil.rmtree(tmp_path / "a"); shutil.rmtree(tmp_path / "b")

    def test_telemetry_errors_or_gate_mismatches_fail_even_if_behaviour_matches(self, tmp_path):
        for tel in ({"errors": 1}, {"mismatch": 1}):
            _mk(tmp_path / "a"); _mk(tmp_path / "b", telemetry=tel)
            assert _run(RC.main, tmp_path / "a", tmp_path / "b")[0] == 1, tel
            import shutil; shutil.rmtree(tmp_path / "a"); shutil.rmtree(tmp_path / "b")


class TestPrefixComparator:
    def test_true_prefix_passes(self, tmp_path):
        _mk(tmp_path / "f", events=("e1", "e2", "e3", "e4")); _mk(tmp_path / "s", events=("e1", "e2"), telemetry={})
        rc, rep = _run(PC.main, tmp_path / "f", tmp_path / "s")
        assert rc == 0 and rep["OVERALL"].startswith("PASS")

    def test_a_different_line_inside_the_prefix_fails(self, tmp_path):
        _mk(tmp_path / "f", events=("e1", "e2", "e3", "e4")); _mk(tmp_path / "s", events=("e1", "eX"), telemetry={})
        rc, rep = _run(PC.main, tmp_path / "f", tmp_path / "s")
        ev = next(c for c in rep["checks"] if c["check"].startswith("events.jsonl"))
        assert rc == 1 and ev["first_difference_line"] == 2

    def test_config_or_window_start_mismatch_fails(self, tmp_path):
        _mk(tmp_path / "f", events=("e1", "e2", "e3")); _mk(tmp_path / "s", events=("e1",), telemetry={}, meta={"config_hash": "other"})
        assert _run(PC.main, tmp_path / "f", tmp_path / "s")[0] == 1

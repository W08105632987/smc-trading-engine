"""Tests for scripts/run_backtest.py (PHASE A: single config authority P0.5,
ledger wiring P0.3, metadata, warm-up). The end-to-end test uses a SYNTHETIC
tick CSV purely to exercise the software path -- it is NOT market evidence."""

import json
import random
from datetime import datetime, timedelta, timezone
from decimal import Decimal as D

import scripts.run_backtest as R
from core.config.registry import EngineConfig


def _args(argv):
    return R.build_arg_parser().parse_args(["dummy.csv"] + argv)


class TestSingleConfigAuthority:
    def test_default_engine_is_the_canonical_reference_engine(self):
        assert _args([]).engine == "REFERENCE"

    def test_frictionless_with_nonzero_costs_is_refused_not_silently_ignored(self, tmp_path):
        csv = tmp_path / "t.csv"; _write_synthetic_csv(csv, 1)
        assert R.main([str(csv), "--spread", "0.3", "--output-dir", str(tmp_path / "o")]) == 2

    def test_cli_has_no_trading_rule_defaults(self):
        a = _args([])
        for dest in R.OVERRIDABLE:
            assert getattr(a, dest) is None, f"--{dest} must default to None (= registry value)"

    def test_no_flags_means_exactly_the_registry_config(self):
        cfg = EngineConfig()
        reference_hash = EngineConfig().config_hash()
        overrides = R.apply_cli_overrides(cfg, _args([]))
        assert overrides == {}
        assert cfg.strategy.minimum_rr.value == D("3.8")   # NOT the old CLI default of 1.5
        assert cfg.strategy.minimum_score.value == 70
        assert cfg.config_hash() == reference_hash

    def test_explicit_override_is_applied_recorded_and_changes_the_hash(self):
        cfg = EngineConfig()
        h0 = cfg.config_hash()
        overrides = R.apply_cli_overrides(cfg, _args(["--minimum-rr", "1.5"]))
        assert overrides == {"strategy.minimum_rr": {"registry": "3.8", "override": "1.5"}}
        assert cfg.strategy.minimum_rr.value == D("1.5")
        assert cfg.config_hash() != h0

    def test_sizing_mode_override_validated(self):
        cfg = EngineConfig()
        R.apply_cli_overrides(cfg, _args(["--sizing-mode", "COMPOUNDING"]))
        assert cfg.risk.sizing_mode.value == "COMPOUNDING"
        assert R.make_ledger(cfg, D("10000")).sizing_mode.value == "COMPOUNDING"

    def test_default_ledger_is_fixed_risk_with_utc_reset(self):
        led = R.make_ledger(EngineConfig(), D("10000"))
        assert led.sizing_mode.value == "FIXED_RISK" and led.reset_consecutive_losses_at_utc_midnight is True

    def test_pipeline_is_built_from_the_given_config_only(self):
        cfg = EngineConfig()
        cfg.strategy.minimum_rr.value = D("2.25")
        p = R.build_pipeline(R.CandleStore(), "XAUUSD", cfg, R.make_ledger(cfg, D("10000")))
        assert p._decision_generator._minimum_rr == D("2.25")
        assert p._decision_generator._min_stop_atr == cfg.strategy.minimum_stop_atr.value
        assert p._decision_generator._config_hash == cfg.config_hash()

    def test_metadata_records_hash_snapshot_and_validation_status(self):
        cfg = EngineConfig()
        a = _args([])
        md = R.build_run_metadata(cfg, {}, a, None, None, None, None, "dummy.csv")
        assert md["config_hash"] == cfg.config_hash()
        assert md["effective_config"]["strategy"]["minimum_rr"]["value"] == "3.8"
        assert "NOT VALIDATED" in md["validation_status"]


def _write_synthetic_csv(path, days, seed=3):
    random.seed(seed)
    t = datetime(2026, 1, 5, tzinfo=timezone.utc)
    end = t + timedelta(days=days)
    price, lines = 2400.0, ["Timestamp,Bid price,Ask price,Bid volume,Ask volume"]
    while t < end:
        price += random.uniform(-0.25, 0.25) + (0.6 if random.random() < 0.004 else 0) - (0.6 if random.random() < 0.004 else 0)
        lines.append(f"{t:%Y%m%d %H:%M:%S}:000,{price:.3f},{price + 0.3:.3f},0.1,0.1")
        t += timedelta(seconds=20)
    path.write_text("\n".join(lines), encoding="utf-8")


class TestEndToEndSyntheticSmoke:
    def _run(self, tmp_path, extra, days=2):
        csv = tmp_path / "ticks.csv"
        _write_synthetic_csv(csv, days)
        out = tmp_path / "out"
        rc = R.main([str(csv), "--window-start", "2026-01-06 00:00:00", "--window-end", "2026-01-07 00:00:00",
                     "--warmup-days", "1", "--engine", "FAST", "--output-dir", str(out)] + extra)
        assert rc == 0
        return out, json.loads((out / "run_metadata.json").read_text())

    def test_runs_end_to_end_and_records_metadata(self, tmp_path):
        out, md = self._run(tmp_path, [])
        assert md["cli_overrides_applied"] == {}
        assert md["config_hash"] == EngineConfig().config_hash()
        assert md["trading_start"] == "2026-01-06T00:00:00+00:00"
        assert md["data_loaded_from"] == "2026-01-05T00:00:00+00:00"   # 1 warm-up day before the window
        assert md["result_summary"]["m5_candles_loaded"] > 200
        assert (out / "statistics_final.json").exists() and (out / "run_log.txt").exists()
        assert "EFFECTIVE CONFIG HASH" in (out / "run_log.txt").read_text()

    def test_no_trade_opens_before_the_window_start(self, tmp_path):
        out, _ = self._run(tmp_path, ["--minimum-rr", "0.01", "--minimum-score", "0"])
        start = datetime(2026, 1, 6, tzinfo=timezone.utc)
        trades_file = out / "trades.jsonl"
        for line in (trades_file.read_text().splitlines() if trades_file.exists() else []):
            assert datetime.fromisoformat(json.loads(line)["entry_timestamp"]) >= start
        for line in (out / "events.jsonl").read_text().splitlines():
            e = json.loads(line)
            if e["event_type"] in ("TRADE_APPROVED", "ORDER_FILLED"):
                assert datetime.fromisoformat(e["timestamp"]) >= start


import scripts.baseline_report as BR
import scripts.compare_runs as CR


class TestReproducibilityAndReportTooling:
    """SYNTHETIC data: proves the tooling and determinism of the software path
    only. These are NOT baseline results and say nothing about the strategy."""

    def _run(self, tmp_path, name, engine, extra=()):
        csv = tmp_path / "ticks.csv"
        if not csv.exists():
            _write_synthetic_csv(csv, 1)
        out = tmp_path / name
        rc = R.main([str(csv), "--window-start", "2026-01-05 12:00:00", "--window-end", "2026-01-05 18:00:00",
                     "--warmup-days", "0", "--engine", engine, "--minimum-rr", "0.01", "--minimum-score", "0",
                     "--output-dir", str(out)] + list(extra))
        assert rc == 0
        return out

    def test_reference_engine_run_twice_is_identical(self, tmp_path):
        a = self._run(tmp_path, "a", "REFERENCE"); b = self._run(tmp_path, "b", "REFERENCE")
        res = CR.compare(a, b)
        assert res["IDENTICAL"], res
        assert res["engine_a"] == "REFERENCE"
        md = json.loads((a / "run_metadata.json").read_text())
        assert md["engine_status"].startswith("CANONICAL") and "ReplayPipeline" in md["research_path"]

    def test_compare_detects_a_difference(self, tmp_path):
        a = self._run(tmp_path, "a", "FAST"); b = self._run(tmp_path, "b", "FAST", ["--minimum-rr", "3.8"]) if False else None
        # tamper with a copy: a changed config hash must be reported as a mismatch
        import shutil
        shutil.copytree(a, tmp_path / "c")
        md = json.loads((tmp_path / "c" / "run_metadata.json").read_text()); md["config_hash"] = "x"
        (tmp_path / "c" / "run_metadata.json").write_text(json.dumps(md))
        assert CR.compare(a, tmp_path / "c")["IDENTICAL"] is False

    def test_report_and_ledger_csv_are_produced_even_with_zero_trades(self, tmp_path):
        out = self._run(tmp_path, "z", "FAST")
        assert BR.main([str(out)]) == 0
        rep = json.loads((out / "baseline_report.json").read_text())
        assert set(rep) == {"DATA", "CONFIG", "TRADING", "RISK", "EXECUTION"}
        assert rep["CONFIG"]["engine"] == "FAST" and "NON-CANONICAL" in rep["CONFIG"]["engine_status"]
        assert (out / "trade_ledger.csv").read_text().splitlines()[0].startswith("trade_id,engine,config_hash")


class TestReportMathOnKnownTrades:
    def _t(self, i, direction, r, pnl, month="01", reason="SL_HIT"):
        return {"trade_id": f"t{i}", "direction": direction, "realized_r": str(r), "net_pnl": str(pnl),
                "entry_timestamp": f"2026-{month}-0{i}T10:00:00+00:00", "exit_reason": reason,
                "tp1_executed": None, "position_size": "0.05"}

    def test_hand_computed_statistics(self):
        trades = [self._t(1, "BULLISH", 3, 150, reason="TP2_HIT"), self._t(2, "BEARISH", -1, -50),
                  self._t(3, "BULLISH", -1, -50), self._t(4, "BEARISH", 1, 50, "02", "TRAILING_STOP_HIT")]
        rep = BR.compute_report(trades, {})
        assert (rep["trade_count"], rep["long_trades"], rep["short_trades"]) == (4, 2, 2)
        assert (rep["winners"], rep["losers"]) == (2, 2)
        assert D(rep["expectancy_R_per_trade"]) == D("0.5")          # (3-1-1+1)/4
        assert D(rep["profit_factor"]) == D("2")                      # 200/100
        assert D(rep["net_pnl"]) == D("100")
        assert D(rep["top_1_trades_R"]) == D("3") and D(rep["total_R_without_top_1"]) == D("-1")
        assert rep["max_consecutive_losses_observed"] == 2
        assert D(rep["max_drawdown_R_closed_trade_curve"]) == D("2")
        assert rep["by_month"]["2026-02"]["trades"] == 1
        assert rep["exit_reason_counts"]["SL_HIT"] == 2

    def test_zero_trades_states_no_statistics(self):
        assert "ZERO completed trades" in BR.compute_report([], {})["note"]

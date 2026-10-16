"""E0 telemetry tests: the instrumentation must be OBSERVATION ONLY.

These tests do not claim anything about strategy quality; they prove that adding telemetry
(1) changes no event, decision, trade or accounting value, (2) never raises into the pipeline,
(3) adds no configuration parameter (config hash unchanged), (4) mirrors the real gate and
target-selection logic, and (5) records the fields promised by the redesign report."""

import json
import random
from datetime import datetime, timezone
from decimal import Decimal as D

import tests.test_backtest_pipeline as T
import tests.test_phase_a_correctness as P
from backtest.accounting import AccountLedger
from backtest.telemetry import Telemetry, describe_targets, shadow_gates, SHADOW_RR_THRESHOLDS
from core.config.registry import EngineConfig
from core.domain.common import Direction
from core.domain.liquidity import LiquidityPoolType as LT
from strategy.targets.target_selector import select_targets
from tests.test_target_selector import _pool, T as T0

ALL = len(T.CANDLES)


def _run(min_rr="1", telemetry=None, ledger=None, **kw):
    p = P._setup_pipeline(min_rr=min_rr, ledger=ledger, **kw)
    p._setup_zone_created["s1"] = T._REAL_ZONE.creation_timestamp
    if telemetry is not None:
        p.telemetry = telemetry
    results = T._run_through(p, T.CANDLES, ALL)
    return p, results


def _sig(results):
    return [(e.event_id, e.event_type.value, e.timestamp, e.entity_id, e.previous_state, e.new_state,
             sorted((k, str(v)) for k, v in e.payload.items())) for r in results for e in r.events]


class TestBehaviourNeutral:
    def test_events_trades_state_identical_with_and_without_telemetry(self):
        p0, r0 = _run(ledger=AccountLedger(D("10000")))
        tel = Telemetry(strict=True)
        p1, r1 = _run(telemetry=tel, ledger=AccountLedger(D("10000")))
        assert _sig(r0) == _sig(r1) and len(_sig(r0)) >= 10
        assert p0.state.completed_trades == p1.state.completed_trades and len(p0.state.completed_trades) == 1
        assert p0.state.emitted_event_ids == p1.state.emitted_event_ids
        assert (p0._ledger.balance, p0._ledger.trades_today, p0._ledger.consecutive_losses) == \
               (p1._ledger.balance, p1._ledger.trades_today, p1._ledger.consecutive_losses)
        assert tel.errors == [] and tel.counters["telemetry_errors"] == 0

    def test_identical_across_rejecting_scenarios_too(self):
        for kw in ({"min_stop_atr": "10"}, {"min_rr": "50"}):
            a = _run(**kw)[1]
            b = _run(telemetry=Telemetry(strict=True), **kw)[1]
            assert _sig(a) == _sig(b)

    def test_telemetry_never_raises_into_the_pipeline(self):
        tel = Telemetry(strict=False)
        tel.on_decision({"garbage": True})            # missing keys -> guarded
        tel.on_candidate(None, None, None, None, None, None, None, None, True, None, 0)
        assert tel.counters["telemetry_errors"] == 2 and len(tel.errors) == 2

    def test_strict_mode_surfaces_hook_errors(self):
        import pytest
        with pytest.raises(Exception):
            Telemetry(strict=True).on_decision({"garbage": True})

    def test_no_new_configuration_parameters(self):
        """E0 must not alter the config hash: the set of parameter paths equals the pre-E0 set."""
        def paths(d, pre=""):
            for k, v in d.items():
                if isinstance(v, dict) and "value" not in v:
                    yield from paths(v, f"{pre}{k}.")
                else:
                    yield f"{pre}{k}"
        got = sorted(paths(EngineConfig().model_dump(mode="json")))
        assert got == EXPECTED_PARAMETER_PATHS


class TestLedgerFields:
    @property
    def row(self):
        return self._state()[2]

    @property
    def tel(self):
        return self._state()[0]

    @property
    def results(self):
        return self._state()[1]

    def _state(self):
        if not hasattr(self, "_cache"):
            tel = Telemetry(strict=True)
            p, results = _run(telemetry=tel, ledger=AccountLedger(D("10000")))
            self._cache = (tel, results, tel.rows["s1"])
        return self._cache

    def test_row_has_identity_timing_geometry_gates_and_outcome(self):
        r = self.row
        for k in ("decision_step_at", "entry", "stop_loss", "atr_at_decision", "stop_distance_atr", "tp1", "tp2", "rr",
                  "gates", "actual_outcome", "shadow_matches_actual", "targets", "risk_state", "sizing", "break_candle_id",
                  "break_candle_close_ts", "touch_started_at", "micro_swings", "h4_state_at_decision", "htf_valid_at_decision"):
            assert k in r, k
        assert r["actual_outcome"] == "APPROVED" and r["shadow_matches_actual"] is True
        assert r["trade"]["exit_reason"] == "TRAILING_STOP_HIT"
        assert set(r["rr"]["would_pass_rr"]) == {str(t) for t in SHADOW_RR_THRESHOLDS}

    def test_true_entry_in_zone_is_recorded_separately_from_the_mislabelled_payload_field(self):
        """The pipeline's event payload field 'entry_position_in_zone' is |entry-stop|/zone_width (NOT the entry's
        position). E0 leaves that payload untouched and records both quantities under distinct, correct names."""
        r = self.row
        payload = next(e.payload for rr in self.results for e in rr.events if e.event_type.value == "TRADE_APPROVED")
        assert D(payload["entry_position_in_zone"]) == D(r["stop_over_zone_width"])
        zone = T._REAL_ZONE
        assert D(r["true_entry_position_in_zone"]) == (D(r["entry"]) - zone.lower) / (zone.upper - zone.lower)

    def test_zero_balance_state_snapshot_matches_ledger(self):
        assert self.row["risk_state"]["account_equity"] == "10000" and self.row["risk_state"]["trades_today"] == 0

    def test_funnel_counters(self):
        c = self.tel.counters
        assert c["setups_confirmed"] == 1 and c["decision_approved"] == 1 and c["trades_closed"] == 1


class TestShadowGatesMirrorTheGenerator:
    """first_failed_gate must equal the generator's reason; independent results show ALL failing gates."""

    def _row(self, **kw):
        tel = Telemetry(strict=True)
        p, _ = _run(telemetry=tel, **kw)
        return tel.rows["s1"], tel

    def test_min_stop_rejection(self):
        r, tel = self._row(min_stop_atr="10")
        assert r["actual_outcome"] == "STOP_BELOW_MINIMUM" and r["gates"]["first_failed_gate"] == "STOP_BELOW_MINIMUM"
        assert tel.counters["shadow_gate_mismatch"] == 0

    def test_rr_rejection_and_other_gates_still_evaluated(self):
        r, tel = self._row(min_rr="50")
        g = r["gates"]
        assert r["actual_outcome"] == "RR_BELOW_MINIMUM" and g["first_failed_gate"] == "RR_BELOW_MINIMUM"
        assert g["results"]["STOP_TOO_WIDE"] in ("PASS", "NA") and "SCORE_BELOW_THRESHOLD" in g["results"]
        assert tel.counters["shadow_gate_mismatch"] == 0

    def test_daily_loss_rejection(self):
        led = AccountLedger(D("10000"))
        led.roll_day(P.FIXTURE_DAY); led.on_realized(P.FIXTURE_DAY, D("-250"))
        r, tel = self._row(ledger=led)
        assert r["actual_outcome"] == "DAILY_LOSS_LIMIT" and r["gates"]["first_failed_gate"] == "DAILY_LOSS_LIMIT"
        assert tel.counters["shadow_gate_mismatch"] == 0

    def test_all_failing_gates_are_listed_not_just_the_first(self):
        r, _ = self._row(min_rr="50", min_stop_atr="10")
        assert r["gates"]["first_failed_gate"] == "STOP_BELOW_MINIMUM"
        assert {"STOP_BELOW_MINIMUM", "RR_BELOW_MINIMUM"} <= set(r["gates"]["all_failed_gates"])


class TestDescribeTargetsMirrorsSelectTargets:
    def test_random_pool_sets_agree_with_the_real_selector(self):
        rnd = random.Random(5)
        types_hi = [LT.PDH, LT.PWH, LT.SWING_HIGH, LT.EQUAL_HIGH, LT.SESSION_HIGH]
        for trial in range(200):
            pools = []
            for i in range(rnd.randint(0, 9)):
                t = rnd.choice(types_hi + [LT.PDL, LT.ROUND_NUMBER])
                maj = rnd.choice([True, False, None]) if t == LT.SWING_HIGH else None
                pools.append(_pool(f"p{trial}-{i}", t, 2400 + rnd.uniform(-5, 40), is_major=maj))
            consumed = frozenset(p.pool_id for p in pools if rnd.random() < 0.2)
            atr = D("2.0")
            sel = select_targets(Direction.BULLISH, D("2400"), pools, consumed, T0, atr=atr, min_target_atr_mult=D("1.0"))
            d = describe_targets("BULLISH", D("2400"), pools, consumed, T0, atr, D("1.0"), sel)
            if sel is None:
                assert not (d["eligible_internal_count"] and d["eligible_external_count"])
            else:
                assert d["nearest_internal"][0]["pool_id"] == sel.tp1_pool_id
                assert d["nearest_external"][0]["pool_id"] == sel.tp2_pool_id

    def test_shadow_strict_pair_never_changes_the_real_selection(self):
        pools = [_pool("i1", LT.EQUAL_HIGH, 2412), _pool("e1", LT.PDH, 2409), _pool("e2", LT.PWH, 2440)]
        sel = select_targets(Direction.BULLISH, D("2400"), pools, frozenset(), T0, atr=D("2"), min_target_atr_mult=D("1"))
        assert (sel.tp1_pool_id, sel.tp2_pool_id) == ("i1", "e1")        # real selector: TP2 (2409) closer than TP1 (2412)
        d = describe_targets("BULLISH", D("2400"), pools, frozenset(), T0, D("2"), D("1"), sel)
        assert d["shadow_strict_pair_available"] is True and d["shadow_tp2_beyond_tp1"]["pool_id"] == "e2"
        assert D(d["shadow_tp2_extension_vs_nearest_external_atr"]) == D("15.5")   # (2440-2409)/2 ATR


class TestH4Telemetry:
    def test_structure_events_recorded_with_application_order_flag(self):
        tel = Telemetry(strict=True)
        class Ev:  # minimal duck-typed structure event
            def __init__(self, i, ts): self.event_id, self.confirmed_at, self.type, self.direction = i, ts, "BOS", "BULLISH"
        t = lambda h: datetime(2026, 1, 5, h, tzinfo=timezone.utc)
        tel.on_h4_structure_event(t(12), Ev("a", t(8)), "BULLISH", "BULLISH", None)
        tel.on_h4_structure_event(t(12), Ev("b", t(4)), "BULLISH", "BULLISH", None)    # older than 'a' -> out of order
        assert [r["applied_out_of_time_order"] for r in tel.h4_log] == [False, True]
        assert tel.counters["h4_events_applied_out_of_time_order"] == 1


# Golden list of configuration parameter paths of the frozen Phase-A-v2 EngineConfig (generated from the ORIGINAL package;
# E0 must not add or remove any, otherwise the config hash would change and exact regression would be impossible).
EXPECTED_PARAMETER_PATHS = [
 "data.symbol",
 "execution.decision_expiry_seconds",
 "execution.max_acceptable_slippage_atr",
 "execution.reconciliation_heartbeat_seconds",
 "execution.retry_max_attempts",
 "execution.sl_attach_max_retries",
 "risk.daily_loss_limit_pct",
 "risk.max_consecutive_losses",
 "risk.max_trades_per_day",
 "risk.reset_consecutive_losses_at_utc_midnight",
 "risk.risk_per_trade_pct",
 "risk.sizing_mode",
 "strategy.choch_warning_max_candles",
 "strategy.discount_zone_pct",
 "strategy.displacement_body_median_multiple",
 "strategy.displacement_range_atr",
 "strategy.equal_level_tolerance_atr",
 "strategy.fractal_left",
 "strategy.fractal_right",
 "strategy.initial_sl_buffer_atr",
 "strategy.major_swing_atr",
 "strategy.max_pending_setups",
 "strategy.max_stop_atr",
 "strategy.micro_swing_min_retracement_atr",
 "strategy.min_target_atr",
 "strategy.minimum_rr",
 "strategy.minimum_score",
 "strategy.minimum_stop_atr",
 "strategy.premium_zone_pct",
 "strategy.scoring_weights.d1_h4_alignment",
 "strategy.scoring_weights.displacement",
 "strategy.scoring_weights.fvg",
 "strategy.scoring_weights.h1_alignment",
 "strategy.scoring_weights.liquidity_sweep",
 "strategy.scoring_weights.mss",
 "strategy.scoring_weights.order_block",
 "strategy.scoring_weights.ote",
 "strategy.scoring_weights.premium_discount",
 "strategy.scoring_weights.session_alignment",
 "strategy.scoring_weights.target_liquidity",
 "strategy.setup_expiry_m15_candles",
 "strategy.sweep_max_penetration_atr",
 "strategy.sweep_min_penetration_atr",
 "strategy.sweep_reversal_window",
 "strategy.sweep_to_displacement_max_age_candles",
 "strategy.touch_confirmation_timeout_minutes",
 "strategy.tp1_allocation_pct",
 "strategy.trailing_sl_buffer_atr"
]

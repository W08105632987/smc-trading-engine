"""PHASE A correctness tests (audit items P0.1, P0.2, P0.3, P0.5, min-stop,
sizing modes). Every expected number below was derived by hand from the
fixture in tests/test_backtest_pipeline.py before being asserted:

    entry 2401.5 (break candle c5), initial stop 2395  -> risk 6.5/unit
    volume 0.07 lots (floor(10000*0.5% / (650 ticks*$1)) = floor(0.0769))
    TP1 2415 (+13.5), TP2 2500, trailing-stop exit 2409.9 (+8.4) at c13
"""

from datetime import datetime, timedelta, timezone
from decimal import Decimal as D

from backtest.accounting import AccountLedger, SizingMode
from core.config.registry import EngineConfig
from core.domain.common import Direction, Timeframe
from core.domain.market_data import Candle, DataSource
from core.domain.zones import Zone, ZoneType
from strategy.confirmation.confirmation_engine import ConfirmationEngineImpl

import tests.test_backtest_pipeline as T

ENTRY_STEPS = T.ENTRY_STEPS
ALL = len(T.CANDLES)


def _setup_pipeline(allocation=None, min_rr=None, ledger=None, min_stop_atr=None):
    p = T._make_pipeline()
    p.state.active_setups["s1"] = T._make_setup()
    if allocation is not None:
        p._config.strategy.tp1_allocation_pct.value = D(allocation)
    if min_rr is not None:
        p._decision_generator._minimum_rr = D(min_rr)
    if ledger is not None:
        p._ledger = ledger
    if min_stop_atr is not None:
        p._decision_generator._min_stop_atr = D(min_stop_atr)
    return p


def _events(results):
    return [e for r in results for e in r.events]


# ======================================================================
# P0.1 — causality
# ======================================================================

def _m5(i, o, h, l, c, base=datetime(2026, 1, 5, tzinfo=timezone.utc)):
    return Candle(
        candle_id=f"k{i}", symbol="XAUUSD", timeframe=Timeframe.M5,
        open=D(str(o)), high=D(str(h)), low=D(str(l)), close=D(str(c)),
        timestamp_open=base + timedelta(minutes=5 * i), timestamp_close=base + timedelta(minutes=5 * i + 5),
        source=DataSource.BROKER_NATIVE, confirmed=True,
    )


def _zone(creation_minute, upper="2400", lower="2398"):
    base = datetime(2026, 1, 5, tzinfo=timezone.utc)
    return Zone(
        zone_id="z", zone_type=ZoneType.FVG, direction=Direction.BULLISH, upper=D(upper), lower=D(lower),
        origin_candle_id="k0", creation_timestamp=base + timedelta(minutes=creation_minute),
    )


# k0..k2 overlap the zone and contain a perfect (but pre-zone) pivot at k1;
# the zone only becomes knowable at minute 15 (== k3's open).
PRE_ZONE = [
    _m5(0, 2399, 2399.5, 2398.5, 2399.0),
    _m5(1, 2399, 2401.0, 2398.0, 2400.5),   # pivot-high candidate (pre-zone)
    _m5(2, 2399.5, 2400.0, 2398.5, 2399.0),  # would "confirm" it (pre-zone)
]
POST_ZONE = [
    _m5(3, 2399, 2399.5, 2398.5, 2399.0),   # first usable candle: touch starts here
    _m5(4, 2399, 2399.0, 2397.0, 2397.5),
    _m5(5, 2398, 2400.2, 2398.0, 2399.5),   # pivot high 2400.2
    _m5(6, 2399.5, 2400.0, 2398.5, 2399.0),  # confirms pivot
    _m5(7, 2399.5, 2400.0, 2399.0, 2399.8),
    _m5(8, 2400.0, 2402.0, 2399.9, 2401.5),  # break (> 2400.2)
]
ATR1 = {c.candle_id: D("1.0") for c in PRE_ZONE + POST_ZONE}


class TestConfirmationCausality:
    def engine(self):
        return ConfirmationEngineImpl(min_retracement_atr_mult=D("0.1"))

    def test_candles_before_zone_availability_cannot_form_touch_or_pivot(self):
        touch = self.engine().process_touch(
            "t", "s", _zone(15), Direction.BULLISH, PRE_ZONE + POST_ZONE, ATR1,
        )
        assert touch is not None
        # started at k3 (the first candle opening at/after minute 15) -- NOT at k0 (origin) ...
        assert touch.reaction_leg_start_candle_id == "k3"
        # ... the pre-zone pivot (k1) must not appear among micro-swings ...
        assert all(ms.pivot_candle_id not in ("k0", "k1", "k2") for ms in touch.micro_swings)
        # ... and the selected pivot/break come from post-zone candles only.
        assert touch.selected_micro_swing_id == "ms:t:k5"
        assert touch.break_candle_id == "k8"

    def test_old_behaviour_would_have_used_pre_zone_pivot(self):
        # Same data with the zone known from the start (creation at minute 0):
        # the pre-zone-looking pivot at k1 IS legitimately usable, proving the
        # difference above is entirely the availability boundary.
        touch = self.engine().process_touch(
            "t", "s", _zone(0), Direction.BULLISH, PRE_ZONE + POST_ZONE, ATR1,
        )
        assert touch.reaction_leg_start_candle_id == "k0"
        assert any(ms.pivot_candle_id == "k1" for ms in touch.micro_swings)

    def test_candle_opening_exactly_at_boundary_is_included(self):
        touch = self.engine().process_touch(
            "t", "s", _zone(15), Direction.BULLISH, PRE_ZONE + POST_ZONE, ATR1,
        )
        assert touch.reaction_leg_start_candle_id == "k3"  # opens at minute 15 == boundary

    def test_candle_straddling_boundary_is_excluded(self):
        # boundary at minute 17: k3 (15->20) opened before it, so it is excluded
        touch = self.engine().process_touch(
            "t", "s", _zone(17), Direction.BULLISH, PRE_ZONE + POST_ZONE, ATR1,
        )
        assert touch.reaction_leg_start_candle_id == "k4"

    def test_explicit_available_at_overrides_earlier_zone_creation(self):
        base = datetime(2026, 1, 5, tzinfo=timezone.utc)
        touch = self.engine().process_touch(
            "t", "s", _zone(0), Direction.BULLISH, PRE_ZONE + POST_ZONE, ATR1,
            available_at=base + timedelta(minutes=15),
        )
        assert touch.reaction_leg_start_candle_id == "k3"

    def test_earlier_available_at_never_moves_boundary_before_zone_creation(self):
        base = datetime(2026, 1, 5, tzinfo=timezone.utc)
        touch = self.engine().process_touch(
            "t", "s", _zone(15), Direction.BULLISH, PRE_ZONE + POST_ZONE, ATR1,
            available_at=base,  # earlier than zone creation: ignored (max rule)
        )
        assert touch.reaction_leg_start_candle_id == "k3"

    def test_no_touch_when_only_pre_zone_candles_overlap(self):
        assert self.engine().process_touch(
            "t", "s", _zone(15), Direction.BULLISH, PRE_ZONE, ATR1,
        ) is None


class TestReplayCausality:
    """Replay-level (not just detector-level) causality assertion: nothing in
    the emitted event stream or the final setup may be dated before the
    earliest instant at which all of its prerequisites were knowable."""

    def test_no_event_precedes_its_prerequisites(self):
        p = _setup_pipeline()
        results = T._run_through(p, T.CANDLES, ALL)
        zone, setup0 = T._REAL_ZONE, T._make_setup()
        boundary = max(zone.creation_timestamp, setup0.created_at)

        by_type = {}
        for e in _events(results):
            by_type.setdefault(e.event_type.value, []).append(e)

        for et in ("MICRO_SWING_CONFIRMED", "TOUCH_EVENT", "SETUP_CONFIRMED", "TRADE_APPROVED", "ORDER_FILLED"):
            assert by_type.get(et), f"expected {et} in fixture"
            for e in by_type[et]:
                assert e.timestamp >= boundary, f"{et} dated {e.timestamp} precedes availability {boundary}"
                assert e.timestamp > zone.creation_timestamp

        # micro-swing pivots/confirmations and the break candle all OPEN at/after the boundary
        candle_by_id = {c.candle_id: c for c in T.CANDLES}
        touch = p.state.active_setups["s1"].touch_events[-1]
        assert touch.started_at > zone.creation_timestamp
        for ms in touch.micro_swings:
            assert candle_by_id[ms.pivot_candle_id].timestamp_open >= boundary
            assert ms.confirmed_at > boundary
        assert candle_by_id[touch.break_candle_id].timestamp_open >= boundary

        # ordering: micro-swing confirmation <= break/touch <= decision <= fill <= position close
        ms_time = min(e.timestamp for e in by_type["MICRO_SWING_CONFIRMED"])
        touch_time = by_type["TOUCH_EVENT"][0].timestamp
        approved_time = by_type["TRADE_APPROVED"][0].timestamp
        assert ms_time <= touch_time <= approved_time

    def test_every_event_timestamp_is_not_after_the_step_that_emitted_it(self):
        p = _setup_pipeline()
        results = T._run_through(p, T.CANDLES, ALL)
        for r in results:
            for e in r.events:
                assert e.timestamp <= r.at, f"{e.event_type.value} at {e.timestamp} is from the future of step {r.at}"

    def test_extending_history_never_changes_earlier_decisions(self):
        short = _setup_pipeline(); rs = T._run_through(short, T.CANDLES, ENTRY_STEPS)
        full = _setup_pipeline(); rf = T._run_through(full, T.CANDLES, ALL)
        for a, b in zip(rs, rf[:ENTRY_STEPS]):
            assert [(e.event_id, e.timestamp) for e in a.events] == [(e.event_id, e.timestamp) for e in b.events]


# ======================================================================
# P0.2 — real partial close
# ======================================================================

class TestPartialCloseThroughPipeline:
    ENTRY, STOP = D("2401.5"), D("2395")
    RISK_USD = D("45.5")          # 650 ticks * $1 * 0.07 lots
    TP1_MOVE, TRAIL_MOVE = D("13.5"), D("8.4")

    def _trade(self, allocation, **kw):
        p = _setup_pipeline(allocation=allocation, min_rr="1", **kw)
        T._run_through(p, T.CANDLES, ALL)
        assert len(p.state.completed_trades) == 1
        return p, p.state.completed_trades[0]

    def test_30_70(self):
        _, t = self._trade("30")
        assert (t.position_size, t.tp1_volume, t.runner_volume) == (D("0.07"), D("0.02"), D("0.05"))
        assert t.tp1_executed and t.tp1_split_reason == "OK"
        assert t.exit_reason == "TRAILING_STOP_HIT"
        expected = (D("0.02") * 1350 + D("0.05") * 840) / self.RISK_USD
        assert t.realized_r == expected
        assert t.net_pnl == D("69")

    def test_50_50(self):
        _, t = self._trade("50")
        assert (t.tp1_volume, t.runner_volume) == (D("0.03"), D("0.04"))  # floor(0.035)=0.03
        assert t.realized_r == (D("0.03") * 1350 + D("0.04") * 840) / self.RISK_USD

    def test_100_0_closes_whole_position_at_tp1(self):
        p, t = self._trade("100")
        assert t.exit_reason == "TP1_HIT"
        assert t.tp1_volume == D("0.07") and t.runner_volume is None
        assert t.exit_price == D("2415")
        assert t.realized_r == (D("0.07") * 1350) / self.RISK_USD
        assert p.state.open_position is None

    def test_0_100_has_no_partial_and_runner_carries_full_volume(self):
        _, t = self._trade("0")
        assert t.tp1_executed is False and t.tp1_split_reason == "ALLOCATION_ZERO"
        assert t.tp1_volume is None and t.runner_volume == D("0.07")
        assert t.realized_r == (D("0.07") * 840) / self.RISK_USD

    def test_remaining_volume_is_reduced_on_the_open_position_after_tp1(self):
        p = _setup_pipeline(allocation="30", min_rr="1")
        T._run_through(p, T.CANDLES, ENTRY_STEPS + 1)  # through the TP1 candle (c6)
        assert p.state.open_position.volume == D("0.05")
        assert p.state.original_volume == D("0.07")
        assert p.state.tp1_leg.volume == D("0.02")

    def test_split_below_min_volume_is_disclosed_not_silent(self):
        p = _setup_pipeline(allocation="30", min_rr="1")
        p._run_config = T.RunConfig(
            tick_size=D("0.01"), tick_value=D("1.00"), volume_step=D("0.01"),
            min_volume=D("0.03"), max_volume=D("100"),   # 30% of 0.07 = 0.02 < 0.03 minimum
        )
        T._run_through(p, T.CANDLES, ALL)
        t = p.state.completed_trades[0]
        assert t.tp1_executed is False and t.tp1_split_reason == "SPLIT_BELOW_MIN_VOLUME"
        assert t.runner_volume == D("0.07")

    def test_commission_charged_on_each_leg_and_sums_to_round_turn_total(self):
        p = _setup_pipeline(allocation="30", min_rr="1")
        p._execution_adapter = T.BacktestExecutionAdapter(commission_per_lot=D("3.5"))
        T._run_through(p, T.CANDLES, ALL)
        t = p.state.completed_trades[0]
        assert t.commission == D("3.5") * D("0.07") * 2
        assert t.net_pnl == D("69") - t.commission


# ======================================================================
# P0.3 — account ledger drives risk gates and sizing
# ======================================================================

def _fresh_ledger(mode=SizingMode.FIXED_RISK):
    return AccountLedger(D("10000"), mode)


FIXTURE_DAY = datetime(2026, 1, 5, 0, 30, tzinfo=timezone.utc)


class TestLedgerDrivenRisk:
    def test_ledger_updates_after_a_full_trade(self):
        led = _fresh_ledger()
        p = _setup_pipeline(allocation="30", min_rr="1", ledger=led)
        T._run_through(p, T.CANDLES, ALL)
        assert led.balance == D("10069")
        assert led.daily_realized_pnl == D("69")
        assert led.trades_today == 1 and led.consecutive_losses == 0 and led.closed_trade_count == 1

    def test_partial_close_is_booked_when_it_happens_not_only_at_the_end(self):
        led = _fresh_ledger()
        p = _setup_pipeline(allocation="30", min_rr="1", ledger=led)
        T._run_through(p, T.CANDLES, ENTRY_STEPS + 1)  # through TP1 only
        assert led.balance == D("10027")  # 0.02 lots * 1350 ticks
        assert led.closed_trade_count == 0

    def test_daily_loss_limit_now_actually_trips(self):
        led = _fresh_ledger()
        led.roll_day(FIXTURE_DAY)
        led.on_realized(FIXTURE_DAY, D("-250"))  # -2.5% of 10,000 > 2% limit
        p = _setup_pipeline(min_rr="1", ledger=led)
        results = T._run_through(p, T.CANDLES, ENTRY_STEPS)
        rej = [e for e in _events(results) if e.event_type.value == "TRADE_REJECTED"]
        assert len(rej) == 1 and rej[0].payload["reason_code"] == "DAILY_LOSS_LIMIT"
        assert p.state.open_position is None

    def test_daily_loss_just_under_limit_still_trades(self):
        led = _fresh_ledger()
        led.roll_day(FIXTURE_DAY)
        led.on_realized(FIXTURE_DAY, D("-199"))
        p = _setup_pipeline(min_rr="1", ledger=led)
        T._run_through(p, T.CANDLES, ENTRY_STEPS)
        assert p.state.open_position is not None

    def test_consecutive_loss_lock_blocks_within_the_day(self):
        led = _fresh_ledger()
        led.roll_day(FIXTURE_DAY)
        led.consecutive_losses = 3
        p = _setup_pipeline(min_rr="1", ledger=led)
        results = T._run_through(p, T.CANDLES, ENTRY_STEPS)
        rej = [e for e in _events(results) if e.event_type.value == "TRADE_REJECTED"]
        assert rej and rej[0].payload["reason_code"] == "CONSECUTIVE_LOSS_LOCK"

    def test_consecutive_loss_lock_resets_at_utc_midnight(self):
        led = _fresh_ledger()
        led.roll_day(datetime(2026, 1, 4, 22, 0, tzinfo=timezone.utc))  # previous UTC day
        led.consecutive_losses = 3
        p = _setup_pipeline(min_rr="1", ledger=led)
        T._run_through(p, T.CANDLES, ENTRY_STEPS)  # decision at 2026-01-05 00:30 UTC
        assert p.state.open_position is not None  # lock was reset by the UTC day change

    def test_daily_trade_cap_counts_opened_trades(self):
        led = _fresh_ledger()
        led.roll_day(FIXTURE_DAY)
        led.trades_today = 3
        p = _setup_pipeline(min_rr="1", ledger=led)
        results = T._run_through(p, T.CANDLES, ENTRY_STEPS)
        rej = [e for e in _events(results) if e.event_type.value == "TRADE_REJECTED"]
        assert rej and rej[0].payload["reason_code"] == "DAILY_TRADE_LIMIT"

    def test_fixed_risk_sizes_from_initial_balance_even_after_gains(self):
        led = _fresh_ledger(SizingMode.FIXED_RISK)
        led.roll_day(FIXTURE_DAY); led.on_realized(FIXTURE_DAY, D("2000"))
        p = _setup_pipeline(min_rr="1", ledger=led)
        T._run_through(p, T.CANDLES, ENTRY_STEPS)
        assert p.state.open_position.volume == D("0.07")  # floor(10000*0.5%/650)

    def test_compounding_sizes_from_current_equity(self):
        led = _fresh_ledger(SizingMode.COMPOUNDING)
        led.roll_day(FIXTURE_DAY); led.on_realized(FIXTURE_DAY, D("2000"))
        p = _setup_pipeline(min_rr="1", ledger=led)
        T._run_through(p, T.CANDLES, ENTRY_STEPS)
        assert p.state.open_position.volume == D("0.09")  # floor(12000*0.5%/650)=floor(0.0923)

    def test_pipeline_requires_a_ledger_or_a_provider(self):
        import pytest
        with pytest.raises(ValueError):
            T.ReplayPipeline(
                symbol="XAUUSD", candle_store=T.CandleStore(), config=EngineConfig(),
                run_config=T.RunConfig(tick_size=D("0.01"), tick_value=D("1"), volume_step=D("0.01"),
                                       min_volume=D("0.01"), max_volume=D("100")),
                swing_detector=None, structure_detector=None, h4_warning_engine=None,
                liquidity_engine=None, displacement_engine=None, zone_engine=None,
                setup_admission_engine=None, confirmation_engine=None, scoring_engine=None,
                risk_engine=None, decision_generator=None, trailing_engine=None,
                execution_adapter=None, atr_provider=None, genesis_at=FIXTURE_DAY,
            )


# ======================================================================
# Minimum stop (enforced by REJECTION, never clamped)
# ======================================================================

class TestMinimumStopEnforcement:
    def test_stop_below_minimum_is_rejected_not_clamped(self):
        # stop distance 6.5 vs ATR 1.0 -> 6.5 ATR; require 10 ATR to force rejection
        p = _setup_pipeline(min_rr="1", min_stop_atr="10")
        results = T._run_through(p, T.CANDLES, ENTRY_STEPS)
        rej = [e for e in _events(results) if e.event_type.value == "TRADE_REJECTED"]
        assert len(rej) == 1
        assert rej[0].payload["reason_code"] == "STOP_BELOW_MINIMUM"
        assert D(rej[0].payload["min_stop_distance"]) == D("10")
        assert p.state.open_position is None  # no trade with a silently widened stop

    def test_stop_exactly_at_minimum_is_accepted(self):
        p = _setup_pipeline(min_rr="1", min_stop_atr="6.5")
        T._run_through(p, T.CANDLES, ENTRY_STEPS)
        assert p.state.open_position is not None
        assert p.state.open_position.stop_loss == D("2395")  # unchanged

    def test_registry_default_minimum_is_the_configured_0_25_atr(self):
        assert EngineConfig().strategy.minimum_stop_atr.value == D("0.25")


# ======================================================================
# P0.5 / config — single source of truth, baseline timing params disabled
# ======================================================================

class TestConfigDefaults:
    def test_registry_minimum_rr_is_3_8(self):
        assert EngineConfig().strategy.minimum_rr.value == D("3.8")

    def test_phase_a_defaults(self):
        c = EngineConfig()
        assert c.risk.sizing_mode.value == "FIXED_RISK"
        assert c.risk.reset_consecutive_losses_at_utc_midnight.value is True
        assert c.strategy.touch_confirmation_timeout_minutes.value == 0      # disabled
        assert c.strategy.sweep_to_displacement_max_age_candles.value == 0   # disabled

    def test_config_hash_changes_when_a_trading_parameter_changes(self):
        a, b = EngineConfig(), EngineConfig()
        assert a.config_hash() == b.config_hash()
        b.strategy.minimum_rr.value = D("1.5")
        assert a.config_hash() != b.config_hash()


# ======================================================================
# Timing parameters (audit 5.1 / 5.2): configurable, DISABLED in Phase A
# ======================================================================

from core.domain.liquidity import DisplacementEvent, SweepEvent
from strategy.displacement.displacement_engine import DisplacementEngineImpl

T0 = datetime(2026, 1, 5, tzinfo=timezone.utc)


def _sweep(minute_confirmed):
    return SweepEvent(
        sweep_id="sw", liquidity_pool_id="p", direction=Direction.BULLISH, penetration_atr=D("0.2"),
        sweep_candle_id="a", close_back_candle_id="b",
        reversal_confirmed_at=T0 + timedelta(minutes=minute_confirmed),
        expiry_at=T0 + timedelta(minutes=minute_confirmed + 15), valid=True,
    )


def _disp(minute):
    return DisplacementEvent(
        displacement_id="d", direction=Direction.BULLISH, candle_id="c", body_ratio=D("2"),
        range_atr_ratio=D("2"), broken_swing_id="bs", valid=True, confirmed_at=T0 + timedelta(minutes=minute),
    )


class TestSweepToDisplacementMaxAge:
    eng = DisplacementEngineImpl(body_median_multiple=D("1.5"), range_atr_multiple=D("1.2"))

    def test_disabled_by_default_pairs_arbitrarily_distant_events(self):
        assert len(self.eng.detect_mss([_sweep(10)], [_disp(10 + 5 * 500)])) == 1  # ~2 days later

    def test_enabled_rejects_displacement_older_than_window(self):
        far = [_disp(10 + 5 * 500)]
        assert self.eng.detect_mss([_sweep(10)], far, max_sweep_to_displacement_age=timedelta(minutes=5 * 20)) == []

    def test_enabled_keeps_displacement_inside_window_and_at_boundary(self):
        w = timedelta(minutes=100)
        assert len(self.eng.detect_mss([_sweep(10)], [_disp(110)], max_sweep_to_displacement_age=w)) == 1  # == window
        assert self.eng.detect_mss([_sweep(10)], [_disp(111)], max_sweep_to_displacement_age=w) == []

    def test_pipeline_converts_candles_to_timedelta_and_default_is_none(self):
        p = _setup_pipeline()
        assert p._mss_max_sweep_age() is None
        p._config.strategy.sweep_to_displacement_max_age_candles.value = 24
        assert p._mss_max_sweep_age() == timedelta(minutes=120)


class TestTouchConfirmationTimeout:
    def _setup_with_untriggerable_break(self, timeout):
        """Fixture where the touch starts at c0 but the break only comes at c5."""
        p = _setup_pipeline(min_rr="1")
        p._config.strategy.touch_confirmation_timeout_minutes.value = timeout
        return p

    def test_disabled_by_default_setup_stays_alive_and_trades(self):
        p = self._setup_with_untriggerable_break(0)
        T._run_through(p, T.CANDLES, ENTRY_STEPS)
        assert p.state.open_position is not None

    def test_enabled_expires_a_stale_touch_before_it_confirms(self):
        # touch starts at c0 (opens 00:00) -> started_at = c0 close 00:05... timeout 10 min
        p = self._setup_with_untriggerable_break(10)
        results = T._run_through(p, T.CANDLES, ENTRY_STEPS)
        exp = [e for e in _events(results) if e.event_type.value == "SETUP_EXPIRED"]
        assert len(exp) == 1 and exp[0].payload["reason_code"] == "TOUCH_CONFIRMATION_TIMEOUT"
        assert p.state.active_setups["s1"].state.value == "EXPIRED"
        assert p.state.open_position is None

    def test_enabled_with_generous_timeout_changes_nothing(self):
        p = self._setup_with_untriggerable_break(24 * 60)
        T._run_through(p, T.CANDLES, ENTRY_STEPS)
        assert p.state.open_position is not None

    def test_whole_phase_a_baseline_event_stream_unchanged_when_disabled(self):
        a = _setup_pipeline(min_rr="1"); ra = T._run_through(a, T.CANDLES, ALL)
        b = _setup_pipeline(min_rr="1")
        b._config.strategy.touch_confirmation_timeout_minutes.value = 0
        b._config.strategy.sweep_to_displacement_max_age_candles.value = 0
        rb = T._run_through(b, T.CANDLES, ALL)
        assert [e.event_id for e in _events(ra)] == [e.event_id for e in _events(rb)]


# ======================================================================
# Audit 3.2 — chronological warm-up: no trades before trading_start
# ======================================================================

class TestWarmupTradingGate:
    def test_confirmed_setup_during_warmup_is_rejected_not_left_pending(self):
        p = _setup_pipeline(min_rr="1")
        p._trading_start = datetime(2026, 1, 5, 12, 0, tzinfo=timezone.utc)  # after the whole fixture
        results = T._run_through(p, T.CANDLES, ALL)
        rej = [e for e in _events(results) if e.event_type.value == "TRADE_REJECTED"]
        assert len(rej) == 1 and rej[0].payload["reason_code"] == "WARMUP_PERIOD"
        assert p.state.open_position is None and not p.state.completed_trades
        assert p.state.active_setups["s1"].state.value == "REJECTED"  # cannot trade stale later

    def test_trading_allowed_once_trading_start_has_passed(self):
        p = _setup_pipeline(min_rr="1")
        p._trading_start = datetime(2026, 1, 5, 0, 0, tzinfo=timezone.utc)  # before the entry at 00:30
        T._run_through(p, T.CANDLES, ENTRY_STEPS)
        assert p.state.open_position is not None

    def test_no_trading_start_means_original_behaviour(self):
        p = _setup_pipeline(min_rr="1")
        assert p._trading_start is None
        T._run_through(p, T.CANDLES, ENTRY_STEPS)
        assert p.state.open_position is not None


# ======================================================================
# TP2 guard + exit-side costs + audit-ledger fields
# ======================================================================

from backtest.execution_adapter import find_first_exit
from decision.trade_decision_generator import TradeDecisionRejection
from tests.test_decision_trade_decision_generator import make_approved_risk_decision, make_generator, make_setup, _dt as _ddt
from core.domain.common import Environment


def _decide(tp1, tp2, direction_long=True):
    setup = make_setup()
    if not direction_long:
        setup = setup.model_copy(update={"direction": Direction.BEARISH})
    entry, stop = (D("2400"), D("2395")) if direction_long else (D("2400"), D("2405"))
    return make_generator(minimum_rr=D("0.1")).generate(
        setup=setup, symbol="XAUUSD", entry=entry, stop_loss=stop, take_profit_1=D(tp1), take_profit_2=D(tp2),
        tp1_allocation_pct=D("30"), htf_context_still_valid=True, risk_decision=make_approved_risk_decision(),
        position_size=D("0.10"), risk_amount=D("50"), risk_percentage=D("0.5"),
        decision_timestamp=_ddt(100), environment=Environment.BACKTEST,
    )


class TestTP2NotCloserThanTP1:
    def test_long_tp2_closer_is_rejected(self):
        r = _decide("2420", "2410")
        assert isinstance(r, TradeDecisionRejection) and r.reason_code == "TP2_NOT_BEYOND_TP1"

    def test_short_tp2_closer_is_rejected(self):
        r = _decide("2380", "2390", direction_long=False)
        assert isinstance(r, TradeDecisionRejection) and r.reason_code == "TP2_NOT_BEYOND_TP1"

    def test_tp2_beyond_is_accepted_and_equal_is_allowed(self):
        assert not isinstance(_decide("2410", "2420"), TradeDecisionRejection)
        assert not isinstance(_decide("2410", "2410"), TradeDecisionRejection)


class TestExitSideCosts:
    """What is modelled: entry spread+slippage (adapter) AND, now, exit
    spread/slippage on the OHLC path. Candle data is treated as BID."""
    L = dict(direction=Direction.BULLISH, stop_loss=D("2395"), take_profit_1=D("2415"), take_profit_2=None)

    def _c(self, hi, lo):
        return T._c(0, hi, lo, lo)

    def test_find_first_exit_long_stop_fills_worse_by_slippage(self):
        r = find_first_exit(candles=[self._c(2400, 2394)], slippage=D("0.2"), **self.L)
        assert (r.reason, r.exit_price) == ("SL_HIT", D("2394.8"))

    def test_pipeline_now_passes_exit_costs(self):
        p = _setup_pipeline(allocation="30", min_rr="1")
        p._execution_adapter = T.BacktestExecutionAdapter(cost_model="REALISTIC", spread=D("0.3"), slippage=D("0.2"))
        T._run_through(p, T.CANDLES, ALL)
        t = p.state.completed_trades[0]
        assert t.cost_model == "REALISTIC"
        # entry = break close 2401.5 + spread 0.3 + slippage 0.2 = 2402.0
        assert t.entry_price == D("2402.0")
        # trailing-stop exit: stop level - slippage (0.2) below the stop level that was hit
        free = _setup_pipeline(allocation="30", min_rr="1"); T._run_through(free, T.CANDLES, ALL)
        assert t.exit_price == free.state.completed_trades[0].exit_price - D("0.2")

    def test_frictionless_unchanged(self):
        p = _setup_pipeline(allocation="30", min_rr="1"); T._run_through(p, T.CANDLES, ALL)
        t = p.state.completed_trades[0]
        assert t.cost_model == "FRICTIONLESS" and t.net_pnl == D("69")


class TestAuditLedgerFields:
    def test_trade_record_carries_reconstruction_fields(self):
        led = _fresh_ledger()
        p = _setup_pipeline(allocation="30", min_rr="1", ledger=led)
        # The fixture injects its setup directly, bypassing _build_setup (which is
        # what records the zone creation time in real replays), so seed that map.
        p._setup_zone_created["s1"] = T._REAL_ZONE.creation_timestamp
        T._run_through(p, T.CANDLES, ALL)
        t = p.state.completed_trades[0]
        assert t.engine == "FAST"  # the fixture pipeline default; the runner sets REFERENCE explicitly
        assert t.zone_creation_timestamp == T._REAL_ZONE.creation_timestamp
        assert t.setup_created_at == T._make_setup().created_at
        assert t.setup_expiry_at > t.setup_created_at
        assert t.zone_creation_timestamp < t.touch_started_at <= t.confirmation_timestamp <= t.entry_timestamp
        assert (t.balance_before, t.balance_after) == (D("10000"), D("10069"))
        assert (t.spread_assumption, t.slippage_assumption) == (D("0"), D("0"))
        assert t.tp1_exit_timestamp is not None and t.tp1_exit_timestamp <= t.exit_timestamp

"""PHASE A — REFERENCE vs FAST engine equivalence (audit 3.1, owner decision 6).

The REFERENCE engine (market_data/replay/reference_m5_layer.py) is the
canonical, exact, non-truncated engine. The FAST engine
(incremental_m5_layer.py) may be faster but must not silently change strategy
semantics. These tests compare, on the same data and step by step:

  swings (incl. major flag + major confirmation time), liquidity pools,
  sweeps, displacement, MSS, FVG zones, order-block zones, setup
  timestamps, touch/confirmation timestamps, entry decisions, stop/target
  levels and trade lifecycle (event stream + TradeRecords).

INTENTIONAL, DOCUMENTED DIFFERENCES (FAST only; not in REFERENCE):
  D1. equal-level pools only consider the last `equal_level_lookback_candles`
      candles (class default 100). Equivalence below is asserted with the
      lookback set >= history length, i.e. non-binding;
      TestDocumentedCutoffDifference shows the difference when it binds and
      that it is confined to equal-level pools.
  D2. unswept-pool give-up after 5,000 candles and D3. pending-major-swing
      give-up after 3,000 candles. These cannot be reached by test-sized
      series; they are NOT validated here and remain a pending item for the
      long-window run on real data (see CHANGELOG_PHASE_A.md).
"""

from datetime import timedelta
from decimal import Decimal

from core.domain.common import Timeframe
from market_data.replay.incremental_m5_layer import IncrementalM5Layer
from market_data.replay.reference_m5_layer import ReferenceM5Layer
from strategy.displacement.displacement_engine import DisplacementEngineImpl
from strategy.indicators.atr import atr_provider_from_period
from strategy.liquidity.liquidity_engine import LiquidityEngineImpl
from strategy.swings.swing_detector import SwingDetectorImpl
from strategy.zones.zone_engine import ZoneEngineImpl

import tests.test_backtest_pipeline as TP
from tests.test_incremental_m5_layer import (
    EQUAL_LEVEL_TOLERANCE, SWEEP_MAX_PEN, SWEEP_MIN_PEN, SWEEP_REVERSAL_WINDOW, _make_candles,
)

MAJOR = Decimal("1.0")
NAMES = ("swings", "pools", "sweeps", "displacements", "mss", "fvgs", "order_blocks")


def _engines(lookback):
    sd, le = SwingDetectorImpl(major_swing_atr_multiple=MAJOR), LiquidityEngineImpl()
    de = DisplacementEngineImpl(body_median_multiple=Decimal("1.5"), range_atr_multiple=Decimal("1.2"))
    ze = ZoneEngineImpl()
    fast = IncrementalM5Layer(sd, le, de, ze, equal_level_lookback_candles=lookback)
    ref = ReferenceM5Layer(sd, le, de, ze)
    return fast, ref


def _run_checkpoints(n, seed, lookback, checkpoints, step=7):
    candles = _make_candles(n, seed)
    atr_full = atr_provider_from_period(14)(Timeframe.M5, candles)
    fast, ref = _engines(lookback)
    out = []
    for k in range(step, n + 1, step):
        sub = candles[:k]
        atr = {c.candle_id: atr_full[c.candle_id] for c in sub if c.candle_id in atr_full}
        tol = EQUAL_LEVEL_TOLERANCE * atr.get(sub[-1].candle_id, Decimal("1"))
        args = (sub, atr, tol, SWEEP_MIN_PEN, SWEEP_MAX_PEN, SWEEP_REVERSAL_WINDOW)
        got = fast.update(*args)
        if k in checkpoints:
            out.append((k, got, ref.update(*args)))
    return out


def _dump(items):
    return sorted((repr(i.model_dump(mode="json")) for i in items))


class TestFastMatchesReferenceExactly:
    CHECKPOINTS = {252, 497, 700}

    def test_all_detector_outputs_identical_full_attributes(self):
        n = 700
        results = _run_checkpoints(n, seed=7, lookback=n, checkpoints=self.CHECKPOINTS)
        assert {k for k, _, _ in results} == self.CHECKPOINTS
        total = 0
        for k, fast, ref in results:
            for name, f, r in zip(NAMES, fast, ref):
                assert _dump(f) == _dump(r), f"{name} differ between FAST and REFERENCE at k={k}"
                total += len(r)
        assert total > 0  # non-vacuous: the series really produced structures

    def test_equivalence_on_a_second_seed(self):
        n = 500
        for k, fast, ref in _run_checkpoints(n, seed=21, lookback=n, checkpoints={248, 500}):
            for name, f, r in zip(NAMES, fast, ref):
                assert _dump(f) == _dump(r), f"{name} differ at k={k} (seed 21)"


class TestDocumentedCutoffDifference:
    def test_binding_equal_level_lookback_only_affects_equal_level_pools(self):
        n = 700
        res = _run_checkpoints(n, seed=7, lookback=100, checkpoints={700})
        (k, fast, ref), = res
        f_swings, f_pools, *_ = fast
        r_swings, r_pools, *_ = ref
        assert _dump(f_swings) == _dump(r_swings)          # swings never affected by the cutoff
        stable = lambda ps: [p for p in ps if "EQUAL" not in p.type.value]
        assert _dump(stable(f_pools)) == _dump(stable(r_pools))
        # The cutoff is NOT behaviour-neutral: it changes the equal-level pool
        # set (windowed grouping can both drop pools and re-form groups with
        # different ids), which then changes sweeps/MSS downstream. Measured on
        # this synthetic series (seed 7, k=700, lookback=100): FAST 46 equal-level
        # pools vs REFERENCE 299; sweeps 163 vs 319; MSS 82 vs 153. Real-data
        # magnitude is UNKNOWN until the long-window comparison is run on the
        # owner's tick data. This test pins that the difference exists and is
        # confined to equal-level pools + their downstream effects.
        f_eq = {p.pool_id for p in f_pools if "EQUAL" in p.type.value}
        r_eq = {p.pool_id for p in r_pools if "EQUAL" in p.type.value}
        assert f_eq != r_eq
        assert len(f_eq) < len(r_eq)


class TestPipelineLevelEquivalence:
    """Entry decisions, stop/target levels, setup/confirmation timestamps and
    the whole trade lifecycle: identical event stream and TradeRecord."""

    def _run(self, engine):
        p = TP._make_pipeline()
        p._m5_engine = engine  # rebuilt below
        p = TP.ReplayPipeline(**{**_pipeline_kwargs(), "m5_engine": engine})
        p.state.active_setups["s1"] = TP._make_setup()
        results = TP._run_through(p, TP.CANDLES, len(TP.CANDLES))
        return p, results

    def test_fixture_event_stream_and_trade_identical(self):
        pf, rf = self._run("FAST")
        pr, rr = self._run("REFERENCE")
        sig = lambda rs: [(e.event_id, e.event_type.value, e.timestamp, e.entity_id, e.new_state,
                           sorted((k, str(v)) for k, v in e.payload.items())) for r in rs for e in r.events]
        assert sig(rf) == sig(rr) and len(sig(rf)) > 0
        import dataclasses
        strip = lambda ts: [{k: v for k, v in dataclasses.asdict(t).items() if k != "engine"} for t in ts]
        # every field EXCEPT the engine identifier (which differs by design) is identical
        assert strip(pf.state.completed_trades) == strip(pr.state.completed_trades)
        assert (pf.state.completed_trades[0].engine, pr.state.completed_trades[0].engine) == ("FAST", "REFERENCE")
        assert len(pf.state.completed_trades) == 1
        a = pf.state.active_setups["s1"]; b = pr.state.active_setups["s1"]
        assert (a.state, a.created_at, a.invalidation_level, a.touch_events) == \
               (b.state, b.created_at, b.invalidation_level, b.touch_events)

    def test_invalid_engine_name_rejected(self):
        import pytest
        with pytest.raises(ValueError):
            TP.ReplayPipeline(**{**_pipeline_kwargs(), "m5_engine": "TURBO"})


def _pipeline_kwargs():
    """Reuse the exact wiring of tests/test_backtest_pipeline._make_pipeline
    by capturing the constructed pipeline's collaborators."""
    base = TP._make_pipeline()
    return dict(
        symbol="XAUUSD", candle_store=base._candle_store, config=base._config, run_config=base._run_config,
        swing_detector=base._swing_detector, structure_detector=base._structure_detector,
        h4_warning_engine=base._h4_warning_engine, liquidity_engine=base._liquidity_engine,
        displacement_engine=base._displacement_engine, zone_engine=base._zone_engine,
        setup_admission_engine=base._setup_admission_engine, confirmation_engine=base._confirmation_engine,
        scoring_engine=base._scoring_engine, risk_engine=base._risk_engine,
        decision_generator=base._decision_generator, trailing_engine=base._trailing_engine,
        execution_adapter=base._execution_adapter, atr_provider=base._atr_provider,
        risk_snapshot_provider=base._risk_snapshot_provider, tp_provider=base._tp_provider,
        genesis_at=TP._dt(-3) - timedelta(days=1), initial_h4_bias=TP.H4BiasStateValue.BULLISH,
    )


class TestReferenceDeterminism:
    """Same inputs twice through the REFERENCE engine -> identical events,
    trade record and ledger (fixture produces a full 30/70 trade)."""

    def _go(self):
        from backtest.accounting import AccountLedger
        kw = {**_pipeline_kwargs(), "m5_engine": "REFERENCE"}
        kw.pop("risk_snapshot_provider"); kw["account_ledger"] = AccountLedger(Decimal("10000"))
        p = TP.ReplayPipeline(**kw)
        p.state.active_setups["s1"] = TP._make_setup()
        p._decision_generator._minimum_rr = Decimal("1")
        rs = TP._run_through(p, TP.CANDLES, len(TP.CANDLES))
        ev = [(e.event_id, e.event_type.value, e.timestamp, e.new_state, sorted((k, str(v)) for k, v in e.payload.items()))
              for r in rs for e in r.events]
        return ev, p.state.completed_trades, (p._ledger.balance, p._ledger.max_drawdown_abs, p._ledger.closed_trade_count)

    def test_two_runs_identical_and_nontrivial(self):
        a, b = self._go(), self._go()
        assert a == b
        assert len(a[1]) == 1 and a[1][0].engine == "REFERENCE" and a[2][2] == 1

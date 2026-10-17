"""V4 patch tests (default suite). Complements tests_validity/test_validity_v4_duplicate_events.py (which holds the defect test and the
8 'never merge distinct opportunities' controls and must NOT be edited).

Part 1: pure logic of backtest/event_dedup.py (no pipeline).
Part 2: pipeline behaviour -- cross-step dedup, order independence, anchor (no chaining), persistence after the kept setup dies,
        telemetry merge map, and that non-duplicates are untouched.
"""
from datetime import timedelta
from decimal import Decimal as D

import pytest

from backtest.event_dedup import EventDedupRegistry, EventItem, event_key, plan_step

KEY = ("BULLISH", "ORDER_BLOCK", D("3830.0"), D("3825.0"), "c-sweep", "disp-1", "EQUAL_LOW")
TOL = D("0.10")


def _item(i, price, key=KEY, tol=TOL):
    return EventItem(zone_id=f"ob:mss-{i}", pool_id=f"pool-{i}", pool_price=D(price), tolerance=tol, key=key)


# ------------------------------------------------------------------ Part 1: pure logic
class TestPlanStepPure:
    def test_variants_within_tolerance_merge_into_lowest_pool_id(self):
        d = plan_step([_item(2, "3827.95"), _item(0, "3827.86"), _item(1, "3827.90")], EventDedupRegistry())
        assert d["ob:mss-0"].keep and not d["ob:mss-1"].keep and not d["ob:mss-2"].keep
        assert d["ob:mss-1"].anchor.kept_pool_id == "pool-0"

    def test_result_is_independent_of_input_order(self):
        items = [_item(0, "3827.86"), _item(1, "3827.90"), _item(2, "3828.30"), _item(3, "3828.35")]
        import itertools
        outs = {tuple(sorted((z, x.keep) for z, x in plan_step(list(p), EventDedupRegistry()).items())) for p in itertools.permutations(items)}
        assert len(outs) == 1

    def test_anchor_rule_prevents_chaining(self):
        # 3827.86 -> .94 (0.08 from anchor, merges) -> 3828.02 (0.16 from anchor, although 0.08 from the previous member): must stay separate
        d = plan_step([_item(0, "3827.86"), _item(1, "3827.94"), _item(2, "3828.02")], EventDedupRegistry())
        assert [d[f"ob:mss-{i}"].keep for i in range(3)] == [True, False, True]

    def test_boundary_equal_to_tolerance_merges_just_beyond_does_not(self):
        d = plan_step([_item(0, "3827.86"), _item(1, "3827.96"), _item(2, "3827.97")], EventDedupRegistry())
        assert d["ob:mss-1"].keep is False and d["ob:mss-2"].keep is True

    def test_different_keys_never_merge(self):
        other = KEY[:4] + ("c-sweep-B",) + KEY[5:]
        d = plan_step([_item(0, "3827.86"), _item(1, "3827.86", key=other)], EventDedupRegistry())
        assert d["ob:mss-0"].keep and d["ob:mss-1"].keep

    def test_earlier_step_always_wins_over_later_step(self):
        reg = EventDedupRegistry()
        first = plan_step([_item(5, "3827.86")], reg)
        reg.register(KEY, first["ob:mss-5"].new_anchor)
        later = plan_step([_item(0, "3827.88")], reg)   # lower pool id, but constructed later
        assert later["ob:mss-0"].keep is False and later["ob:mss-0"].anchor.kept_pool_id == "pool-5"

    def test_plan_step_does_not_mutate_registry(self):
        reg = EventDedupRegistry()
        plan_step([_item(0, "3827.86"), _item(1, "3827.90")], reg)
        assert len(reg) == 0

    def test_event_key_normalises_enums_and_ignores_price(self):
        class E:  # enum-like
            value = "BULLISH"
        assert event_key(E(), E(), D("1.0"), D("0.5"), "s", "d", E())[0] == "BULLISH"
        assert event_key("BULLISH", "OB", D("1.0"), D("0.5"), "s", "d", "EQUAL_LOW") == event_key("BULLISH", "OB", D("1.00"), D("0.50"), "s", "d", "EQUAL_LOW")


# ------------------------------------------------------------------ Part 2: pipeline behaviour
def _pipeline_bits():
    import tests_validity.test_validity_v4_duplicate_events as V
    import tests.test_phase_a_correctness as P
    return V, P


def _build(variants_by_step):
    """One pipeline; each element of variants_by_step is constructed in a separate step (hours apart)."""
    V, P = _pipeline_bits()
    from core.domain.market_data import Candle
    from core.domain.common import Timeframe
    p = P._setup_pipeline(min_rr="1")
    p.state.active_setups.clear()
    all_events = []
    for n, variants in enumerate(variants_by_step):
        pools, sweeps, msss, zones = zip(*variants)
        created_at = V.T0 + timedelta(minutes=5)
        atr_key = Candle.build_id("XAUUSD", Timeframe.M5, created_at)
        at = created_at + timedelta(hours=3 + n)
        ev = p._construct_setup_candidates(at, list(msss), {z.zone_id: z for z in zones}, list(sweeps), list(pools), {atr_key: V.ATR})
        all_events.append([e for e in ev if e.event_type.value == "SETUP_CREATED"])
    return p, all_events


class TestPipelineDedup:
    def test_variant_arriving_in_a_later_step_is_merged_not_recreated(self):
        V, _ = _pipeline_bits()
        p, ev = _build([[V._variant(0, "3827.86")], [V._variant(1, "3827.90")]])
        assert len(ev[0]) == 1 and len(ev[1]) == 0

    def test_variant_arriving_after_the_kept_setup_died_is_still_merged(self):
        V, _ = _pipeline_bits()
        p, ev = _build([[V._variant(0, "3827.86")]])
        p.state.active_setups.clear()                      # kept setup expired / displaced / invalidated
        from core.domain.market_data import Candle
        from core.domain.common import Timeframe
        pools, sweeps, msss, zones = zip(V._variant(1, "3827.90"))
        atr_key = Candle.build_id("XAUUSD", Timeframe.M5, V.T0 + timedelta(minutes=5))
        ev2 = p._construct_setup_candidates(V.T0 + timedelta(hours=9), list(msss), {z.zone_id: z for z in zones}, list(sweeps), list(pools), {atr_key: V.ATR})
        assert [e for e in ev2 if e.event_type.value == "SETUP_CREATED"] == []

    def test_same_step_winner_is_lowest_pool_id_regardless_of_input_order(self):
        V, _ = _pipeline_bits()
        for order in ([0, 1, 2], [2, 1, 0], [1, 2, 0]):
            variants = [V._variant(i, p) for i, p in zip(range(3), ["3827.86", "3827.90", "3827.95"])]
            p, ev = _build([[variants[i] for i in order]])
            assert len(ev[0]) == 1
            assert set(p.state.active_setups) == {"setup:ob:mss-0"}

    def test_anchor_spread_never_exceeds_tolerance_in_the_pipeline(self):
        V, _ = _pipeline_bits()
        p, ev = _build([[V._variant(0, "3827.86"), V._variant(1, "3827.94"), V._variant(2, "3828.02")]])
        assert len(ev[0]) == 2                              # 0 and 1 merge (0.08), 2 is 0.16 from the anchor -> separate

    def test_telemetry_records_a_merge_map_with_identical_bounds(self):
        V, P = _pipeline_bits()
        from backtest.telemetry import Telemetry
        from core.domain.market_data import Candle
        from core.domain.common import Timeframe
        p = P._setup_pipeline(min_rr="1")
        p.state.active_setups.clear()
        p.telemetry = Telemetry(strict=True)
        pools, sweeps, msss, zones = zip(*[V._variant(i, x) for i, x in enumerate(["3827.86", "3827.90", "3827.95"])])
        atr_key = Candle.build_id("XAUUSD", Timeframe.M5, V.T0 + timedelta(minutes=5))
        p._construct_setup_candidates(V.T0 + timedelta(hours=3), list(msss), {z.zone_id: z for z in zones}, list(sweeps), list(pools), {atr_key: V.ATR})
        rows = p.telemetry._finalize_v4_merge_map()
        assert p.telemetry.counters["v4_dedup_skipped"] == 2 and len(rows) == 2
        assert all(r["kept_setup_id"] == "setup:ob:mss-0" and r["kept_row_found"] and r["bounds_identical"] for r in rows)
        assert p.telemetry.counters["setups_constructed"] == 1

    def test_non_duplicates_are_constructed_exactly_as_before(self):
        V, _ = _pipeline_bits()
        _, ev = _build([[V._variant(0, "3827.86"), V._variant(1, "3828.30"), V._variant(2, "3827.86", sweep_candle="c-sweep-B")]])
        assert len(ev[0]) == 3

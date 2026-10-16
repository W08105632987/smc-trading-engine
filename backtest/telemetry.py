"""E0 candidate-level telemetry (OBSERVATION ONLY).

Purpose: record, for every setup the pipeline constructs, enough evidence to
compare the original funnel with any later experimental version -- without
changing a single decision. Specified in the Opportunity Funnel Redesign
report (section 9).

Hard rules this module obeys (and tests enforce):
  * It never mutates any object it is handed (it only reads).
  * It never raises into the pipeline: every hook is guarded; a failure is
    counted in ``errors`` and the run continues unchanged.
  * It uses no randomness and no wall-clock time (all timestamps come from
    replay data), so enabling it cannot change event ids or ordering.
  * It adds NO registry/config parameters (that would change the config hash).
  * Output goes to separate files (candidates.jsonl, h4_transitions.jsonl,
    funnel_counters.json, telemetry_manifest.json); events.jsonl,
    trades.jsonl and statistics files are untouched.
  * It is OFF unless a ``Telemetry`` instance is passed to ReplayPipeline.

Pydantic-free on purpose: values are read by duck typing.
"""

from __future__ import annotations

import json
from collections import Counter
from datetime import datetime
from decimal import Decimal
from enum import Enum
from pathlib import Path
from typing import Any, Optional

SCHEMA = "e0_candidate_ledger/v1"
D0 = Decimal("0")

# Shadow RR thresholds recorded per decision (DIAGNOSTIC flags only).
SHADOW_RR_THRESHOLDS = (Decimal("1.0"), Decimal("1.5"), Decimal("2.0"), Decimal("2.5"), Decimal("3.8"))


def _j(x: Any) -> Any:
    """JSON-safe, deterministic conversion."""
    if x is None or isinstance(x, (bool, int, str)):
        return x
    if isinstance(x, Decimal):
        return str(x)
    if isinstance(x, datetime):
        return x.isoformat()
    if isinstance(x, Enum):
        return x.value
    if isinstance(x, (list, tuple)):
        return [_j(v) for v in x]
    if isinstance(x, dict):
        return {str(k): _j(v) for k, v in x.items()}
    if isinstance(x, float):
        return repr(x)
    return str(x)


def _guard(fn):
    def wrapper(self, *a, **kw):
        try:
            return fn(self, *a, **kw)
        except Exception as exc:  # noqa: BLE001 - telemetry must never break a run
            self.errors.append(f"{fn.__name__}: {type(exc).__name__}: {exc}")
            self.counters["telemetry_errors"] += 1
            if self.strict:
                raise
            return None
    wrapper.__name__ = fn.__name__
    return wrapper


def _atr_mult(x: Optional[Decimal], atr: Optional[Decimal]) -> Optional[Decimal]:
    if x is None or atr is None or atr <= D0:
        return None
    return x / atr


class Telemetry:
    def __init__(self, strict: bool = False):
        self.strict = strict
        self.rows: dict[str, dict] = {}
        self.order: list[str] = []
        self.counters: Counter = Counter()
        self.h4_log: list[dict] = []
        self.errors: list[str] = []
        self._h4_max_confirmed_applied: Optional[datetime] = None
        self._decision_to_setup: dict[str, str] = {}

    # ------------------------------------------------------------------ rows
    def _row(self, setup_id: str) -> dict:
        r = self.rows.get(setup_id)
        if r is None:
            r = {"schema": SCHEMA, "setup_id": setup_id, "skips": {}, "parked_steps": 0}
            self.rows[setup_id] = r
            self.order.append(setup_id)
        return r

    # -------------------------------------------------------------- funnel
    @_guard
    def on_zone_considered(self, zone_id: str, already_active: bool) -> None:
        self.counters["zones_considered"] += 1
        if already_active:
            self.counters["zones_skipped_already_has_setup"] += 1

    @_guard
    def on_candidate(self, at, setup, zone, mss, sweep, pool, atr_at_setup, h4_state, admitted: bool,
                     displaced_id: Optional[str], pending_before: int) -> None:
        self.counters["setups_constructed"] += 1
        self.counters["setups_admitted" if admitted else "setups_not_admitted"] += 1
        r = self._row(setup.setup_id)
        pool_type = getattr(getattr(pool, "type", None), "value", None)
        direction = setup.direction.value
        zone_type = getattr(getattr(zone, "zone_type", None), "value", None)
        r.update({
            "direction": direction, "zone_type": zone_type, "zone_id": zone.zone_id, "mss_id": getattr(mss, "mss_id", None),
            "sweep_id": getattr(sweep, "sweep_id", None), "sweep_candle_id": getattr(sweep, "sweep_candle_id", None),
            "displacement_id": getattr(mss, "displacement_id", None),
            "pool_id": getattr(pool, "pool_id", None), "pool_type": pool_type,
            "pool_price": _j(getattr(pool, "price", None)), "pool_created_at": _j(getattr(pool, "created_at", None)),
            # event keys (V4 PROPOSAL is evidence only in E0; nothing consumes it)
            "event_key_pool_free": [direction, zone_type, getattr(sweep, "sweep_candle_id", None),
                                    getattr(mss, "displacement_id", None), pool_type],
            "zone_origin_candle_id": getattr(zone, "origin_candle_id", None),
            "zone_creation_ts": _j(zone.creation_timestamp),
            "zone_upper": _j(zone.upper), "zone_lower": _j(zone.lower),
            "mss_confirmed_at": _j(getattr(mss, "confirmed_at", None)),
            "setup_created_at_logical": _j(setup.created_at), "constructed_at": _j(at),
            "construction_lag_h_from_zone": _hours(at, zone.creation_timestamp),
            "setup_expiry_at": _j(setup.expiry_at), "atr_at_setup": _j(atr_at_setup),
            "invalidation_level": _j(setup.invalidation_level), "score_at_creation": _j(setup.score),
            "score_snapshot": _j(getattr(setup, "score_snapshot", None)),
            "h4_state_at_construction": _j(h4_state),
            "admitted": admitted, "pending_count_before": pending_before, "displaced_setup_id": displaced_id,
        })

    @_guard
    def on_terminal(self, at, setup_id: str, state: str, source: str, by: Optional[str] = None) -> None:
        self.counters[f"terminal_{state}"] += 1
        r = self._row(setup_id)
        if "terminal" not in r:
            r["terminal"] = {"state": state, "at": _j(at), "source": source, "by": by}

    @_guard
    def on_touch_seen(self, at, setup_id: str, touch) -> None:
        r = self._row(setup_id)
        if "touch_first_seen_step" not in r:
            r["touch_first_seen_step"] = _j(at)
            r["touch_started_at"] = _j(touch.started_at)
            r["reaction_leg_start_candle_id"] = touch.reaction_leg_start_candle_id

    @_guard
    def on_confirmed(self, at, setup_id: str, touch, break_candle, selected_ms) -> None:
        self.counters["setups_confirmed"] += 1
        r = self._row(setup_id)
        constructed = r.get("constructed_at")
        r.update({
            "confirmation_step_at": _j(at), "break_candle_id": touch.break_candle_id,
            "break_candle_close_ts": _j(getattr(break_candle, "timestamp_close", None)),
            "touch_started_at": _j(touch.started_at), "touch_ended_at": _j(touch.ended_at),
            "micro_swings": [{"id": m.micro_swing_id, "pivot_candle_id": m.pivot_candle_id, "pivot_price": _j(m.pivot_price),
                              "confirmed_at": _j(m.confirmed_at), "retracement_atr": _j(m.retracement_atr),
                              "status": _j(m.qualification_status)} for m in touch.micro_swings],
            "selected_micro_swing_id": touch.selected_micro_swing_id,
            "selected_micro_swing_price": _j(getattr(selected_ms, "pivot_price", None)),
        })
        # retroactive = the break candle had ALREADY closed before the setup was constructed
        if constructed is not None and getattr(break_candle, "timestamp_close", None) is not None:
            r["retroactive_confirmation"] = break_candle.timestamp_close.isoformat() < constructed
            if r["retroactive_confirmation"]:
                self.counters["confirmed_retroactively"] += 1

    @_guard
    def on_skip(self, at, setup_id: str, reason: str, detail: Optional[dict] = None) -> None:
        r = self._row(setup_id)
        self.counters[f"decision_skipped_{reason}"] += 1
        s = r["skips"]
        s[reason] = s.get(reason, 0) + 1
        r.setdefault("first_skip_at", _j(at))
        r["last_skip_at"] = _j(at)
        if detail is not None and "first_skip_detail" not in r:
            r["first_skip_detail"] = _j(detail)

    @_guard
    def on_parked(self, at, setup_id: str) -> None:
        """Confirmed setup NOT attempted this step because a position is open."""
        r = self._row(setup_id)
        r["parked_steps"] += 1
        r.setdefault("first_parked_at", _j(at))
        self.counters["decision_parked_open_position_steps"] += 1

    # ------------------------------------------------------------- decision
    @_guard
    def on_decision(self, ctx: dict) -> None:
        """ctx keys (all read-only views supplied by the pipeline):
        at, setup, zone, break_candle, entry, stop, atr, tp1, tp2, selection, pools, consumed,
        min_target_atr, htf_valid, h4_state, risk_snapshot, sizing, risk_decision, result,
        generator (for thresholds/formulas), tp1_allocation_pct, trading_start_blocked"""
        setup = ctx["setup"]
        at = ctx["at"]
        r = self._row(setup.setup_id)
        entry, stop, atr = ctx["entry"], ctx["stop"], ctx["atr"]
        tp1, tp2 = ctx["tp1"], ctx["tp2"]
        zone = ctx["zone"]
        bull = setup.direction.value == "BULLISH"
        gen = ctx["generator"]
        alloc = ctx["tp1_allocation_pct"]
        stop_dist = abs(entry - stop)
        stop_atr = _atr_mult(stop_dist, atr)
        r.update({
            "decision_step_at": _j(at), "entry": _j(entry), "stop_loss": _j(stop), "atr_at_decision": _j(atr),
            "stop_distance": _j(stop_dist), "stop_distance_atr": _j(stop_atr),
            "decision_lag_h_from_confirmation": _hours(at, ctx.get("break_candle_close")),
            "decision_after_break_step": (ctx.get("break_candle_close") is not None and at > ctx["break_candle_close"]),
            "tp1": _j(tp1), "tp2": _j(tp2),
            "tp1_distance_atr": _j(_atr_mult(abs(tp1 - entry), atr)) if tp1 is not None else None,
            "tp2_distance_atr": _j(_atr_mult(abs(tp2 - entry), atr)) if tp2 is not None else None,
            "h4_state_at_decision": _j(ctx.get("h4_state")), "htf_valid_at_decision": ctx.get("htf_valid"),
            "min_target_atr_floor": _j(ctx.get("min_target_atr")),
        })
        # --- zone geometry (note: the pipeline's own payload field 'entry_position_in_zone' is |entry-stop|/zone_width; the
        #     TRUE position is recorded here under a different name so nothing old is relabelled)
        if zone is not None and atr is not None and atr > D0:
            width = zone.upper - zone.lower
            r["zone_width_atr"] = _j(width / atr)
            if width > D0:
                r["stop_over_zone_width"] = _j(stop_dist / width)
                r["true_entry_position_in_zone"] = _j((entry - zone.lower) / width)  # 0 = lower edge, 1 = upper edge, >1 above
            near = zone.upper if bull else zone.lower
            r["zone_to_entry_atr"] = _j(((entry - near) if bull else (near - entry)) / atr)
            far = zone.lower if bull else zone.upper
            r["stop_inside_zone_range"] = (stop >= zone.lower and stop <= zone.upper)
        # --- targets
        r["targets"] = describe_targets(setup.direction.value, entry, ctx.get("pools") or (), ctx.get("consumed") or (),
                                        at, atr, ctx.get("min_target_atr"), ctx.get("selection"))
        # --- RR (decision-time arithmetic, mirrors the generator's own functions)
        rr = {}
        if tp1 is not None and tp2 is not None:
            rr_tp1 = gen._compute_rr(setup.direction, entry, stop, tp1)
            rr_tp2 = gen._compute_rr(setup.direction, entry, stop, tp2)
            blended = gen._compute_blended_rr(setup.direction, entry, stop, tp1, tp2, alloc)
            rr = {"rr_tp1": _j(rr_tp1), "rr_tp2": _j(rr_tp2), "blended_rr": _j(blended),
                  "would_pass_rr": {str(t): (blended >= t) for t in SHADOW_RR_THRESHOLDS}}
        r["rr"] = rr
        # --- shadow gates, evaluated INDEPENDENTLY (the real generator reports only the first failure)
        gates = shadow_gates(setup, ctx)
        r["gates"] = gates
        res = ctx["result"]
        actual = getattr(res, "reason_code", None) if not _is_decision(res) else "APPROVED"
        r["actual_outcome"] = actual
        first = gates["first_failed_gate"] or "APPROVED"
        # The real generator reports the first failing gate in this fixed order; any disagreement is an E0 bug.
        agree = (first == actual) or (first == "APPROVED" and actual == "APPROVED")
        r["shadow_matches_actual"] = agree
        if not agree:
            self.counters["shadow_gate_mismatch"] += 1
        self.counters["decisions_evaluated"] += 1
        self.counters["decision_approved" if actual == "APPROVED" else f"decision_rejected_{actual}"] += 1
        # --- risk / sizing / score
        rs = ctx.get("risk_snapshot")
        sz = ctx.get("sizing")
        r["risk_state"] = {
            "account_equity": _j(getattr(rs, "account_equity", None)), "daily_starting_equity": _j(getattr(rs, "daily_starting_equity", None)),
            "daily_realized_pnl": _j(getattr(rs, "daily_realized_pnl", None)),
            "consecutive_losses": getattr(rs, "consecutive_losses_count", None), "trades_today": getattr(rs, "trades_today_count", None),
            "sizing_equity": _j(getattr(rs, "sizing_equity", None)),
        } if rs is not None else None
        r["sizing"] = {"computed_volume": _j(getattr(sz, "computed_volume", None)), "rounded_volume": _j(getattr(sz, "rounded_volume", None)),
                       "risk_amount": _j(getattr(sz, "risk_amount", None)),
                       "risk_percentage_actual": _j(getattr(sz, "risk_percentage_actual", None)),
                       "approved": getattr(sz, "approved", None), "reason": getattr(sz, "reason_code", None)} if sz is not None else None
        r["score_at_decision"] = _j(setup.score)
        if actual == "APPROVED":
            did = getattr(res, "decision_id", None)
            if did:
                self._decision_to_setup[did] = setup.setup_id
            r["decision_id"] = did

    @_guard
    def on_warmup_reject(self, at, setup_id: str) -> None:
        r = self._row(setup_id)
        r["decision_step_at"] = _j(at)
        r["actual_outcome"] = "WARMUP_PERIOD"
        self.counters["decision_rejected_WARMUP_PERIOD"] += 1

    @_guard
    def on_trade_closed(self, trade) -> None:
        sid = getattr(trade, "setup_id", None)
        if not sid:
            return
        r = self._row(sid)
        r["trade"] = {"trade_id": trade.trade_id, "entry_timestamp": _j(trade.entry_timestamp), "entry_price": _j(trade.entry_price),
                      "exit_timestamp": _j(trade.exit_timestamp), "exit_price": _j(trade.exit_price), "exit_reason": trade.exit_reason,
                      "realized_r": _j(trade.realized_r), "net_pnl": _j(trade.net_pnl), "position_size": _j(trade.position_size)}
        self.counters["trades_closed"] += 1

    # ------------------------------------------------------------------- H4
    @_guard
    def on_h4_structure_event(self, at, ev, state_before, state_after, transition, source: str = "STRUCTURE_EVENT") -> None:
        """Every structure event fed to the H4 state machine, in APPLICATION order."""
        confirmed = getattr(ev, "confirmed_at", None)
        regressed = bool(self._h4_max_confirmed_applied is not None and confirmed is not None
                         and confirmed < self._h4_max_confirmed_applied)
        if confirmed is not None and (self._h4_max_confirmed_applied is None or confirmed > self._h4_max_confirmed_applied):
            self._h4_max_confirmed_applied = confirmed
        row = {
            "applied_at_step": _j(at), "structure_event_id": getattr(ev, "event_id", None),
            "structure_event_type": _j(getattr(ev, "type", None)), "direction": _j(getattr(ev, "direction", None)),
            "confirmed_at": _j(confirmed), "state_before": _j(state_before), "state_after": _j(state_after),
            "transition_event_type": _j(getattr(transition, "event_type", None)) if transition is not None else None,
            "source": source, "applied_out_of_time_order": regressed,
            "age_at_application_h": _hours(at, confirmed),
        }
        self.h4_log.append(row)
        self.counters["h4_structure_events_applied"] += 1
        if regressed:
            self.counters["h4_events_applied_out_of_time_order"] += 1
        if state_before != state_after:
            self.counters["h4_state_changes"] += 1

    @_guard
    def on_h4_silent(self, at, state_before, state_after, source: str) -> None:
        self.h4_log.append({"applied_at_step": _j(at), "state_before": _j(state_before), "state_after": _j(state_after),
                            "source": source, "structure_event_id": None, "applied_out_of_time_order": False})
        self.counters["h4_silent_transitions"] += 1

    # --------------------------------------------------------------- output
    def finalize_rows(self) -> list[dict]:
        out = []
        for sid in self.order:
            r = self.rows[sid]
            r.setdefault("terminal", None)
            out.append(r)
        return out

    def write(self, out_dir, extra_manifest: Optional[dict] = None) -> dict:
        d = Path(out_dir)
        d.mkdir(parents=True, exist_ok=True)
        rows = self.finalize_rows()
        with open(d / "candidates.jsonl", "w", encoding="utf-8") as f:
            for r in rows:
                f.write(json.dumps(_j(r), sort_keys=True) + "\n")
        with open(d / "h4_transitions.jsonl", "w", encoding="utf-8") as f:
            for r in self.h4_log:
                f.write(json.dumps(_j(r), sort_keys=True) + "\n")
        funnel = dict(sorted(self.counters.items()))
        (d / "funnel_counters.json").write_text(json.dumps(funnel, indent=1, sort_keys=True), encoding="utf-8")
        manifest = {"schema": SCHEMA, "candidate_rows": len(rows), "h4_log_rows": len(self.h4_log),
                    "telemetry_errors": list(self.errors[:50]), "telemetry_error_count": len(self.errors),
                    "behavior_note": "observation only; events.jsonl/trades.jsonl/statistics are unaffected by this module"}
        if extra_manifest:
            manifest.update(extra_manifest)
        (d / "telemetry_manifest.json").write_text(json.dumps(_j(manifest), indent=1, sort_keys=True), encoding="utf-8")
        return manifest


# ---------------------------------------------------------------------------
# helpers (pure functions; unit-tested)
# ---------------------------------------------------------------------------

def _hours(a, b) -> Optional[str]:
    if a is None or b is None:
        return None
    return str(round((a - b).total_seconds() / 3600, 4))


def _is_decision(res) -> bool:
    return hasattr(res, "decision_id") and not hasattr(res, "reason_code")


def describe_targets(direction: str, entry: Decimal, pools, consumed, at, atr, min_target_atr, selection) -> dict:
    """Describe the target-eligibility situation WITHOUT altering selection.
    Mirrors strategy/targets/target_selector.select_targets' filters and ORDER exactly
    (side type -> consumed -> created_at -> ahead -> noise floor -> classify), reusing the
    selector's own type sets and classify() read-only. Records, per class, the nearest eligible
    pools and the exclusion counts, plus the shadow 'TP2 strictly beyond TP1' candidate used by
    experiment S2 (never fed back into selection)."""
    from strategy.targets.target_selector import _HIGH_TYPES, _LOW_TYPES, classify  # read-only use
    bull = direction == "BULLISH"
    side_types = _HIGH_TYPES if bull else _LOW_TYPES
    out: dict = {"selected_tp1_pool_id": getattr(selection, "tp1_pool_id", None),
                 "selected_tp2_pool_id": getattr(selection, "tp2_pool_id", None)}
    min_distance = None
    if atr is not None and atr > D0 and min_target_atr is not None and min_target_atr > D0:
        min_distance = min_target_atr * atr
    excl = Counter()
    elig = {"INTERNAL": [], "EXTERNAL": []}
    for p in pools:
        if p.type not in side_types:
            excl["wrong_side_type"] += 1
            continue
        if p.pool_id in consumed:
            excl["consumed"] += 1
            continue
        if p.created_at > at:
            excl["not_yet_created"] += 1
            continue
        ahead = (p.price > entry) if bull else (p.price < entry)
        if not ahead:
            excl["not_ahead_of_entry"] += 1
            continue
        dist = abs(p.price - entry)
        if min_distance is not None and dist < min_distance:
            excl["below_min_target_distance"] += 1
            continue
        cat = classify(p)
        if cat is None:
            excl["not_target_eligible_type"] += 1
            continue
        elig[cat].append((dist, p.pool_id, p.type.value, p.price, getattr(p, "is_major_swing", None)))
    out["excluded_counts"] = dict(excl)
    key = lambda t: (t[0], t[1])  # same tie-break as _nearest: (distance, pool_id)
    for cat in ("INTERNAL", "EXTERNAL"):
        lst = sorted(elig[cat], key=key)
        out[f"eligible_{cat.lower()}_count"] = len(lst)
        out[f"nearest_{cat.lower()}"] = [
            {"pool_id": pid, "type": ptype, "price": _j(price), "is_major_swing": maj, "distance_atr": _j(_atr_mult(dist, atr))}
            for dist, pid, ptype, price, maj in lst[:5]]
    ints = sorted(elig["INTERNAL"], key=key)
    exts = sorted(elig["EXTERNAL"], key=key)
    if ints:
        beyond = [e for e in exts if e[0] > ints[0][0]]
        out["shadow_strict_pair_available"] = bool(beyond)
        if beyond:
            e0 = beyond[0]
            out["shadow_tp2_beyond_tp1"] = {"pool_id": e0[1], "type": e0[2], "price": _j(e0[3]), "distance_atr": _j(_atr_mult(e0[0], atr))}
            if exts:
                out["shadow_tp2_extension_vs_nearest_external_atr"] = _j(_atr_mult(e0[0] - exts[0][0], atr))
    return out


def shadow_gates(setup, ctx: dict) -> dict:
    """Evaluate EVERY gate independently, in the generator's fixed order, mirroring its conditions exactly.
    first_failed_gate must equal the generator's reported reason (counted as a mismatch otherwise)."""
    gen = ctx["generator"]
    entry, stop, atr = ctx["entry"], ctx["stop"], ctx["atr"]
    tp1, tp2 = ctx["tp1"], ctx["tp2"]
    bull = setup.direction.value == "BULLISH"
    res = {}
    # 1. HTF
    res["HTF_CONTEXT_INVALID"] = "FAIL" if not ctx.get("htf_valid") else "PASS"
    # 2/3. stop bounds (same guards as the generator)
    stop_dist = abs(entry - stop)
    if gen._min_stop_atr is not None and atr is not None and atr > D0:
        res["STOP_BELOW_MINIMUM"] = "FAIL" if stop_dist < gen._min_stop_atr * atr else "PASS"
    else:
        res["STOP_BELOW_MINIMUM"] = "NA"
    if gen._max_stop_atr is not None and atr is not None and atr > D0:
        res["STOP_TOO_WIDE"] = "FAIL" if stop_dist > gen._max_stop_atr * atr else "PASS"
    else:
        res["STOP_TOO_WIDE"] = "NA"
    # 4. TP2 order
    if tp1 is not None and tp2 is not None:
        closer = (tp2 < tp1) if bull else (tp2 > tp1)
        res["TP2_NOT_BEYOND_TP1"] = "FAIL" if closer else "PASS"
        blended = gen._compute_blended_rr(setup.direction, entry, stop, tp1, tp2, ctx["tp1_allocation_pct"])
        res["RR_BELOW_MINIMUM"] = "FAIL" if blended < gen._minimum_rr else "PASS"
    else:
        res["TP2_NOT_BEYOND_TP1"] = "NA"
        res["RR_BELOW_MINIMUM"] = "NA"
    res["SCORE_BELOW_THRESHOLD"] = "FAIL" if setup.score < gen._minimum_score else "PASS"
    rd = ctx.get("risk_decision")
    if rd is not None:
        res["RISK_GATES"] = "PASS" if getattr(rd, "approved", False) else "FAIL"
        res["risk_reason_code"] = getattr(rd, "reason_code", None)
    else:
        res["RISK_GATES"] = "NA"
    order = ["HTF_CONTEXT_INVALID", "STOP_BELOW_MINIMUM", "STOP_TOO_WIDE", "TP2_NOT_BEYOND_TP1", "RR_BELOW_MINIMUM",
            "SCORE_BELOW_THRESHOLD", "RISK_GATES"]
    failed = [g for g in order if res.get(g) == "FAIL"]
    first = failed[0] if failed else None
    if first == "RISK_GATES":
        first = res.get("risk_reason_code") or "RISK_GATES"
    return {"results": res, "all_failed_gates": failed, "first_failed_gate": first}

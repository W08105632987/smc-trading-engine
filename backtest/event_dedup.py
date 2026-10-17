"""V4 -- event-level de-duplication of setup candidates (pure logic, no pipeline dependencies).

WHY THIS EXISTS (see tests_validity/test_validity_v4_duplicate_events.py and docs/e0/VALIDITY_DEFECTS_EVIDENCE.md, section V4):
one economic event (one sweep + one displacement + one entry zone) was producing many setup candidates, because order-block zones link
per MSS and the MSS id embeds a swept *pool* id; equal-level pools are re-mapped every step with a tolerance taken from the latest
ATR, so the same cluster of lows yields pools with slightly different average prices and ids. Measured on the E0 October ledger:
15,740 constructed candidates collapse to ~3,750 unique events; the 3-slot pending queue was already full for 73% of constructions.

WHAT THIS MODULE DOES (and nothing else):
  * defines the event key  (direction, zone_type, zone upper, zone lower, sweep candle id, displacement id, pool type)
  * clusters pool PRICES inside one key using the strategy's OWN equal-level tolerance
    (max(2 ticks, equal_level_tolerance_atr * ATR at the anchor's setup time)) -- NO new parameter
  * ANCHOR RULE: a variant merges only if |price - anchor_price| <= the ANCHOR's tolerance, so spread to the anchor never exceeds
    the tolerance (no chaining)
  * KEEP RULE: earliest constructed wins; within one step ties -> lowest pool id (deterministic, independent of input order)

It does NOT change thresholds, stop placement, target selection, scoring, admission, sizing or risk. It only decides which
duplicate variants of an already-identified event are NOT constructed as separate setups.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Optional, Sequence


@dataclass(frozen=True)
class EventAnchor:
    """The first (kept) member of a price cluster inside one event key."""

    anchor_pool_price: Decimal
    tolerance: Decimal
    kept_setup_id: str
    kept_pool_id: str
    kept_zone_id: str


@dataclass(frozen=True)
class EventItem:
    """One (zone, mss) pair the pipeline is about to turn into a setup candidate, described for dedup purposes."""

    zone_id: str
    pool_id: str
    pool_price: Decimal
    tolerance: Decimal
    key: tuple


@dataclass(frozen=True)
class DedupDecision:
    keep: bool
    anchor: Optional[EventAnchor] = None      # for keep=False: the kept member this item was merged into
    new_anchor: Optional[EventAnchor] = None  # for keep=True: the anchor to register in the registry


def event_key(direction, zone_type, zone_upper: Decimal, zone_lower: Decimal, sweep_candle_id, displacement_id, pool_type) -> tuple:
    """Everything that defines the entry zone, the trigger, the direction and the kind of level. The pool PRICE is deliberately not
    part of the key; it is handled by the tolerance band."""
    return (_v(direction), _v(zone_type), zone_upper, zone_lower, sweep_candle_id, displacement_id, _v(pool_type))


def _v(x):
    return getattr(x, "value", x)


class EventDedupRegistry:
    """Persistent (per replay run) record of event anchors. Lives in ReplayState. An event stays 'seen' for the whole run, even after
    its kept setup is displaced/expired/invalidated -- a later pool-id variant of the same event must not re-create it."""

    def __init__(self) -> None:
        self._anchors: dict[tuple, list[EventAnchor]] = {}

    def __len__(self) -> int:
        return sum(len(v) for v in self._anchors.values())

    def anchors_for(self, key: tuple) -> Sequence[EventAnchor]:
        return tuple(self._anchors.get(key, ()))

    def match(self, key: tuple, price: Decimal) -> Optional[EventAnchor]:
        """The OLDEST anchor of this key whose tolerance band contains `price` (anchors are stored oldest-first)."""
        for a in self._anchors.get(key, ()):
            if abs(price - a.anchor_pool_price) <= a.tolerance:
                return a
        return None

    def register(self, key: tuple, anchor: EventAnchor) -> None:
        self._anchors.setdefault(key, []).append(anchor)


def plan_step(items: Sequence[EventItem], registry: EventDedupRegistry) -> dict[str, DedupDecision]:
    """Decide, for every item of ONE construction step, whether it is kept or merged. READ-ONLY with respect to `registry`
    (the caller registers anchors as it actually constructs the kept candidates).

    Order-independence: within the step, items of one key are processed in ascending pool id, so the result does not depend on
    the order the detectors happened to emit them. Earlier steps always win over this step (registry is checked first)."""

    decisions: dict[str, DedupDecision] = {}
    step_anchors: dict[tuple, list[EventAnchor]] = {}

    ordered = sorted(items, key=lambda it: (it.pool_id, it.zone_id))
    for it in ordered:
        hit = registry.match(it.key, it.pool_price)
        if hit is None:
            for a in step_anchors.get(it.key, ()):
                if abs(it.pool_price - a.anchor_pool_price) <= a.tolerance:
                    hit = a
                    break
        if hit is not None:
            decisions[it.zone_id] = DedupDecision(keep=False, anchor=hit)
            continue
        anchor = EventAnchor(
            anchor_pool_price=it.pool_price, tolerance=it.tolerance,
            kept_setup_id=f"setup:{it.zone_id}", kept_pool_id=it.pool_id, kept_zone_id=it.zone_id,
        )
        step_anchors.setdefault(it.key, []).append(anchor)
        decisions[it.zone_id] = DedupDecision(keep=True, new_anchor=anchor)
    return decisions

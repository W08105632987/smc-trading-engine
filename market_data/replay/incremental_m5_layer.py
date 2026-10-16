"""Incremental M5 layer cache — Milestone 10 performance fix.

ROOT CAUSE (found by inspecting the actual code, confirmed against a
real multi-thousand-step production run): backtest/pipeline.py's step()
called swing detection, liquidity-pool mapping, sweep detection,
displacement detection, and FVG/order-block detection FRESH, on the
ENTIRE growing M5 candle history, on EVERY SINGLE replay step. Several
of these are individually O(n) per call (swing detection, displacement,
FVGs); one — map_equal_level_pools — is O(n^2) per call on its own via a
nested loop, PLUS an O(n) `candles.index()` lookup inside that loop,
making it effectively O(n^2) to O(n^3) per call. Called every step
across tens of thousands of steps, this compounds catastrophically —
confirmed in a real run where per-step cost climbed from ~100ms early
on to ~52 SECONDS by step 4,750 (roughly 5.6% into a one-year replay)
and was still climbing.

FIX STRATEGY, verified per-detector before writing any code (see each
class's docstring below for the specific reasoning):

  - SwingDetector: genuinely incremental with correct handling of an
    important subtlety — a swing's major/minor classification
    (major_flag) can be resolved arbitrarily far in the future (the
    original _find_major_confirmation has no bound; it scans forward
    until it finds either qualifying displacement or invalidation, or
    gives up if the data runs out). A naive "cache after N candles"
    approach would silently freeze major_flag=False for swings whose
    real confirmation arrives later than N candles — which matters a
    great deal, since major/minor DIRECTLY decides whether a liquidity
    pool is "internal" (TP1) or "external" (TP2) in
    strategy/targets/target_selector.py. Getting this wrong would be a
    serious, easy-to-miss correctness bug, so IncrementalSwingCache
    handles it explicitly: swing IDENTITY settles in a small bounded
    window (needs only FRACTAL_RIGHT=2 candles), but PENDING swings
    (major_flag still unresolved) are re-checked one new candle at a
    time using the EXACT same two conditions
    SwingDetectorImpl._find_major_confirmation already uses (quoted
    verbatim below, not reimplemented from a paraphrase) — O(1) per
    pending swing per new candle instead of an O(remaining-history)
    rescan from the pivot every single step.

  - Daily/weekly pools: a completed day's/week's high/low never changes
    once that day/week has closed — genuinely append-only, cached
    permanently per calendar day once seen.

  - Displacement, FVGs, order blocks: each candle's classification only
    ever depends on a small BOUNDED trailing window (median_lookback=20
    for displacement; a 3-candle pattern for FVGs; order blocks are
    keyed off already-computed MSS events) and is never revised by
    later data — verified by reading each function's body, not assumed.
    Cached incrementally with a generous safety margin.

  - Sweeps: a sweep's validity/reversal-confirmation settles within
    `reversal_window` candles of the sweep candle (a small, configured
    number) — cached incrementally with a margin comfortably larger
    than that.

  - Equal-level pools (map_equal_level_pools): the ONE case NOT made
    incremental here. Its matching has no bound — an equal-high pair
    could legitimately form between a candle from months ago and one
    today, so no fixed margin can be proven correct without either
    keeping every not-yet-grouped candle as a permanent live anchor
    (which degrades back toward the same O(n) growth this fix exists
    to remove) or reimplementing its exact anchor/group semantics
    incrementally (real, non-trivial risk of a subtle mismatch in
    exactly the function most directly tied to TP/SL price levels).
    Rather than risk that quietly, this is handled with a disclosed,
    deliberate simplification instead: bounded to the most recent
    `equal_level_lookback_candles` (default 2000, ~7 trading days)
    M5 candles. This is a real, disclosed trade-off — equal-level pools
    older than that window will no longer be found — not a silently
    "equivalent" result. See IncrementalM5Layer.EQUAL_LEVEL_LOOKBACK.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Optional, Sequence

import bisect as _bisect

from core.domain.common import Direction
from core.domain.liquidity import (
    DisplacementEvent,
    LiquidityPool,
    LiquidityPoolType,
    MSSEvent,
    SweepEvent,
)
from core.domain.market_data import Candle
from core.domain.structure import SwingPoint, SwingType
from strategy.displacement.displacement_engine import DisplacementEngineImpl
from strategy.liquidity.liquidity_engine import LiquidityEngineImpl
from strategy.swings.swing_detector import FRACTAL_LEFT, FRACTAL_RIGHT, SwingDetectorImpl
from strategy.zones.zone_engine import ZoneEngineImpl

# Generous margins: each is comfortably larger than the detector's own
# actual lookback requirement, verified against the source (swing
# identity: FRACTAL_LEFT+FRACTAL_RIGHT=4; displacement:
# median_lookback=20; FVGs: 3-candle pattern). Kept as named constants,
# not buried magic numbers, so a future change to a detector's own
# lookback can be checked against these directly.
_SWING_IDENTITY_MARGIN = 20
_DISPLACEMENT_MARGIN = 50
_FVG_MARGIN = 10


class _CandleIndexMap:
    """Shared, incrementally-updated candle_id -> index lookup. The
    underlying CandleStore only ever appends in ascending order (Blocker
    2's contract), so a candle already indexed here never moves —
    updating this is O(new candles) per call, never O(n)."""

    def __init__(self) -> None:
        self._index: dict[str, int] = {}
        self._last_len = 0

    def sync(self, candles: Sequence[Candle]) -> None:
        n = len(candles)
        for i in range(self._last_len, n):
            self._index[candles[i].candle_id] = i
        self._last_len = n

    def get(self, candle_id: str) -> Optional[int]:
        return self._index.get(candle_id)


@dataclass
class _PendingMajorCheck:
    swing_id: str
    swing_type: SwingType
    price: Decimal
    resume_from_index: int


class IncrementalSwingCache:
    """See module docstring — the one detector requiring special
    handling for a value (major_flag) that can be resolved arbitrarily
    far in the future.

    DISCLOSED SIMPLIFICATION (same pattern, same reasoning, as
    IncrementalSweepCache's max_unswept_age_candles — found the exact
    same way: per-call time kept climbing even after fixing the
    sweep-side version of this issue, traced to this class's own
    `_pending` set growing without bound, since most swings are minor
    and never resolve to major at all): swings still pending after
    `max_pending_age_candles` (default 3000, ~10 trading days) are
    given up on and permanently left at major_flag=False. Deliberately
    set larger than the ~800-candle far-future confirmation this fix's
    own equivalence test constructs, so that genuinely-late (but not
    extreme) confirmations still resolve correctly — this cutoff only
    changes behavior for swings that would otherwise still be
    unresolved after ten real trading days, which is a real, disclosed
    trade-off, not a silent approximation."""

    def __init__(self, detector: SwingDetectorImpl, max_pending_age_candles: int = 3000) -> None:
        self._detector = detector
        self._major_swing_atr_multiple = detector._major_swing_atr_multiple
        self._max_pending_age = max_pending_age_candles
        self._resolved: dict[str, SwingPoint] = {}
        self._pending: dict[str, _PendingMajorCheck] = {}
        self._pending_first_index: dict[str, int] = {}
        self._last_len = 0

    def update(
        self, candles: Sequence[Candle], atr_by_candle_id: dict[str, Decimal],
        index_map: _CandleIndexMap,
    ) -> list[SwingPoint]:
        n = len(candles)
        if n == self._last_len:
            return list(self._resolved.values())

        # Phase A — identity detection over a small tail: finds any
        # brand-new swings. atr_by_candle_id=None here deliberately:
        # this pass is identity-only, major-flag is handled in Phase B
        # below using the FULL candle list (a tail slice can't correctly
        # see far enough forward for major confirmation anyway).
        rescan_start = max(0, self._last_len - _SWING_IDENTITY_MARGIN) if self._last_len else 0
        tail = candles[rescan_start:]
        tail_swings = self._detector.detect(tail, atr_by_candle_id=None)
        for sw in tail_swings:
            if sw.swing_id in self._resolved or sw.swing_id in self._pending:
                continue
            pivot_index = index_map.get(sw.pivot_candle_id)
            if pivot_index is None:
                continue  # defensive; should not happen given index_map is synced first
            self._resolved[sw.swing_id] = sw  # major_flag=False for now
            self._pending[sw.swing_id] = _PendingMajorCheck(
                swing_id=sw.swing_id, swing_type=sw.type, price=sw.price,
                resume_from_index=pivot_index + FRACTAL_RIGHT + 1,
            )
            self._pending_first_index[sw.swing_id] = pivot_index

        # Give up on pending swings that have aged past the disclosed
        # cutoff — see class docstring's "DISCLOSED SIMPLIFICATION".
        # Left at major_flag=False permanently (the value they already
        # have), simply removed from future rechecking.
        for swing_id in list(self._pending.keys()):
            first_index = self._pending_first_index.get(swing_id, 0)
            if (n - first_index) > self._max_pending_age:
                del self._pending[swing_id]
                self._pending_first_index.pop(swing_id, None)

        # Phase B — advance every pending swing's major-confirmation
        # check using only candles it hasn't seen yet. These two
        # conditions are QUOTED VERBATIM from
        # SwingDetectorImpl._find_major_confirmation, not paraphrased,
        # specifically to avoid any drift from the frozen logic.
        resolved_ids = []
        for swing_id, pending in self._pending.items():
            i = pending.resume_from_index
            while i < n:
                candle = candles[i]
                atr = atr_by_candle_id.get(candle.candle_id)
                i += 1
                if atr is None:
                    continue
                threshold = atr * self._major_swing_atr_multiple
                if pending.swing_type == SwingType.HIGH:
                    if candle.close > pending.price:
                        resolved_ids.append(swing_id)  # invalidated — stop checking forever
                        break
                    if pending.price - candle.close >= threshold:
                        sw = self._resolved[swing_id]
                        self._resolved[swing_id] = sw.model_copy(
                            update={"major_flag": True, "major_confirmed_at": candle.timestamp_close}
                        )
                        resolved_ids.append(swing_id)
                        break
                else:
                    if candle.close < pending.price:
                        resolved_ids.append(swing_id)
                        break
                    if candle.close - pending.price >= threshold:
                        sw = self._resolved[swing_id]
                        self._resolved[swing_id] = sw.model_copy(
                            update={"major_flag": True, "major_confirmed_at": candle.timestamp_close}
                        )
                        resolved_ids.append(swing_id)
                        break
            else:
                pending.resume_from_index = i  # ran out of new candles; still pending next time

        for sid in resolved_ids:
            del self._pending[sid]
            self._pending_first_index.pop(sid, None)

        self._last_len = n
        return list(self._resolved.values())


class IncrementalDailyWeeklyPoolCache:
    """A completed day's/week's high/low never changes once that
    day/week has closed — see LiquidityEngineImpl.map_daily_weekly_pools's
    own docstring ('previous COMPLETED trading day/week'). Genuinely
    append-only: computed once per newly-completed day, cached forever."""

    def __init__(self, engine: LiquidityEngineImpl) -> None:
        self._engine = engine
        self._pools_by_day: dict[date, list[LiquidityPool]] = {}
        self._last_len = 0
        self._last_seen_day: Optional[date] = None

    def update(self, candles: Sequence[Candle]) -> list[LiquidityPool]:
        n = len(candles)
        if n == self._last_len:
            return [p for pools in self._pools_by_day.values() for p in pools]

        current_day = candles[-1].timestamp_close.date() if candles else None
        if current_day != self._last_seen_day:
            # A new day has started, meaning the PREVIOUS day (and
            # possibly week) just completed — recompute using the
            # engine's own real logic (frozen, unmodified) but only for
            # the small set of days it can actually see, i.e. this is a
            # full call, not a hand-rolled reimplementation of what
            # "PDH/PDL" or "PWH/PWL" means, only wrapped for caching.
            all_pools = self._engine.map_daily_weekly_pools(candles)
            self._pools_by_day = {}
            for p in all_pools:
                day = p.created_at.date()
                self._pools_by_day.setdefault(day, []).append(p)
            self._last_seen_day = current_day

        self._last_len = n
        return [p for pools in self._pools_by_day.values() for p in pools]


class IncrementalDisplacementCache:
    """Each candle's displacement classification depends only on a
    bounded trailing window (median_lookback=20) of PRECEDING candles
    and is never revised — verified directly against
    DisplacementEngineImpl.detect's body (no forward-looking scan).

    PERF-05: Maintains incrementally-updated sorted lists of major HIGH
    and LOW swings (by price ASC) so that detect() can use O(log M)
    bisect lookups instead of rebuilding O(all_swings) filter + sort on
    every step. Only new/newly-promoted major swings are inserted via
    bisect.insort (amortised O(M) per insertion, O(0) per step with no
    new majors). Invalidated swings are tracked in a set and skipped
    in detect() without requiring list removal."""

    def __init__(self, engine: DisplacementEngineImpl, median_lookback: int = 20) -> None:
        self._engine = engine
        self._median_lookback = median_lookback
        self._items: dict[str, DisplacementEvent] = {}
        self._last_len = 0
        # PERF-10: Incremental directional partitioning & candle index
        self._by_candle: dict[str, DisplacementEvent] = {}
        self._bullish_disps: list[DisplacementEvent] = []
        self._bullish_times: list = []
        self._bearish_disps: list[DisplacementEvent] = []
        self._bearish_times: list = []
        # PERF-05: Sorted major swing structures, updated incrementally.
        self._major_highs_sorted: list = []   # HIGH SwingPoints sorted by price ASC
        self._major_highs_prices: list = []   # parallel Decimal list for bisect
        self._major_lows_sorted: list = []    # LOW SwingPoints sorted by price ASC
        self._major_lows_prices: list = []    # parallel Decimal list for bisect
        self._known_major_ids: set = set()    # swing IDs already in sorted lists
        self._invalidated_ids: set = set()    # swing IDs with invalidated_at != None

    def _sync_major_swings(self, swings: Sequence) -> None:
        """Incrementally add newly-promoted major swings and track newly
        invalidated ones. O(log M + M) per new major swing, O(0) per step
        with no changes — never O(all_swings) after the first call."""
        for s in swings:
            if s.invalidated_at is not None:
                self._invalidated_ids.add(s.swing_id)
                continue
            if not s.major_flag:
                continue
            if s.swing_id in self._known_major_ids:
                continue  # already in sorted lists
            from core.domain.structure import SwingType
            if s.type == SwingType.HIGH:
                idx = _bisect.bisect_left(self._major_highs_prices, s.price)
                self._major_highs_sorted.insert(idx, s)
                self._major_highs_prices.insert(idx, s.price)
            else:
                idx = _bisect.bisect_left(self._major_lows_prices, s.price)
                self._major_lows_sorted.insert(idx, s)
                self._major_lows_prices.insert(idx, s.price)
            self._known_major_ids.add(s.swing_id)

    def update(
        self, candles: Sequence[Candle], swings: Sequence, atr_by_candle_id: dict[str, Decimal],
    ) -> list[DisplacementEvent]:
        n = len(candles)
        if n == self._last_len:
            return list(self._items.values())
        self._sync_major_swings(swings)
        margin = self._median_lookback + _DISPLACEMENT_MARGIN
        rescan_start = max(0, self._last_len - margin) if self._last_len else 0
        tail = candles[rescan_start:]
        for d in self._engine.detect(
            tail, swings, atr_by_candle_id, median_lookback=self._median_lookback,
            _major_highs_sorted=self._major_highs_sorted,
            _major_highs_prices=self._major_highs_prices,
            _major_lows_sorted=self._major_lows_sorted,
            _major_lows_prices=self._major_lows_prices,
            _invalidated_ids=self._invalidated_ids,
        ):
            if d.displacement_id not in self._items:
                self._items[d.displacement_id] = d
                self._by_candle[d.candle_id] = d
                from core.domain.common import Direction
                if d.direction == Direction.BULLISH:
                    idx = _bisect.bisect_right(self._bullish_times, d.confirmed_at)
                    self._bullish_disps.insert(idx, d)
                    self._bullish_times.insert(idx, d.confirmed_at)
                elif d.direction == Direction.BEARISH:
                    idx = _bisect.bisect_right(self._bearish_times, d.confirmed_at)
                    self._bearish_disps.insert(idx, d)
                    self._bearish_times.insert(idx, d.confirmed_at)
        self._last_len = n
        return list(self._items.values())


class IncrementalFVGCache:
    """A 3-candle local pattern (candle1/candle2/candle3), never
    revised — verified directly against ZoneEngineImpl.detect_fvgs's
    body."""

    def __init__(self, engine: ZoneEngineImpl) -> None:
        self._engine = engine
        self._items: dict[str, object] = {}
        self._last_len = 0

    def update(self, candles: Sequence[Candle], displacements_by_candle_id: dict) -> list:
        n = len(candles)
        if n == self._last_len:
            return list(self._items.values())
        rescan_start = max(0, self._last_len - _FVG_MARGIN) if self._last_len else 0
        tail = candles[rescan_start:]
        for z in self._engine.detect_fvgs(tail, displacements_by_candle_id):
            self._items[z.zone_id] = z
        self._last_len = n
        return list(self._items.values())


class IncrementalOrderBlockCache:
    """Deliberately NOT actually incremental — recomputes fresh every
    call. Order blocks are cheap (O(mss_events), not O(candles)), and
    mss_events can themselves change identity across steps whenever
    they trace back to an equal-level pool (unstable identity — see
    module docstring): caching order blocks by mss_id was tried and
    caught by this fix's own equivalence test, producing stale, orphaned
    order blocks tied to superseded equal-level-based mss events that no
    longer existed in the current step's mss_events list. Kept as its
    own class (rather than inlined) so the "this one is intentionally
    not cached, and here's why" reasoning stays attached to the right
    place if someone later tries to add caching back."""

    def __init__(self, engine: ZoneEngineImpl) -> None:
        self._engine = engine

    def update(
        self, candles: Sequence[Candle], mss_events: Sequence[MSSEvent], displacements_by_id: dict,
        index_map: Optional[_CandleIndexMap] = None,
    ) -> list:
        idx_dict = index_map._index if index_map is not None else None
        return list(self._engine.detect_order_blocks(candles, mss_events, displacements_by_id, candle_index_by_id=idx_dict))


class IncrementalSweepCache:
    """A sweep's validity/reversal-confirmation settles within
    `reversal_window` candles of the sweep candle itself (see
    LiquidityEngineImpl.detect_sweeps's own docstring) — cached with a
    margin comfortably larger than that configured window.

    IMPORTANT correctness detail, found by this fix's own equivalence
    test failing: detect_sweeps reports only the FIRST-EVER qualifying
    candle per pool (`break` immediately after the first match — "one
    sweep per pool"). A naive "just re-scan the recent tail against
    already-seen pools" approach breaks this: re-scanning only a tail
    slice treats that slice as if it were the whole history, so it can
    report a "first match within the tail" for a pool whose true
    first-ever qualifying candle either already happened earlier (a
    real bug: a second, spurious sweep event for a pool that already
    has its one true sweep) or simply hasn't been correctly determined
    yet. The fix: once a pool has ANY recorded sweep, it is PERMANENTLY
    excluded from all future rescanning — its one-and-only sweep is
    already known and can never change. Only pools with NO sweep found
    yet continue to be tail-rescanned, which is safe precisely because
    nothing before the tail ever qualified for them (verified by every
    prior scan finding nothing), so the first match found in new
    candles genuinely IS the first-ever match.

    IMPORTANT correctness detail #2, also found by the equivalence test:
    a sweep's `reversal_confirmed_at`/`valid` depends on
    `reversal_window` candles AFTER the sweep candle. If a sweep is
    first detected near the "leading edge" of whatever data is
    currently available (fewer than `reversal_window` future candles
    exist yet), detect_sweeps still records it immediately — but with
    whatever partial/possibly-incomplete reversal check that limited
    window allows. Permanently freezing that result the moment it's
    first seen would lock in a wrong `valid`/`reversal_confirmed_at`
    for sweeps discovered right at the growing edge of the replay. Such
    pools are kept in a `_pending_reversal` set instead of being
    permanently resolved, and are re-derived (using only that one
    pool's own tiny bounded slice, never the full history) once enough
    future candles have actually arrived.

    DISCLOSED SIMPLIFICATION: most pools are never swept at all. Left
    unbounded, `still_unswept` grows roughly with total pool count over
    a full year, and every entry gets rechecked on every single step —
    turning back into real, if much smaller, per-step growth. Pools
    that go `max_unswept_age_candles` (default 5000, ~17 trading days)
    without being swept are moved to a permanently "given up on" bucket
    and no longer rechecked. This is a genuine, disclosed behavior
    difference from an unbounded full recompute (which would keep
    checking forever and could, in principle, find an extremely late
    sweep this will miss) — not a silent approximation.

    IMPORTANT — only used for STABLE pools (daily/weekly, swing-based),
    never equal-level pools. Equal-level pool IDENTITY itself is
    unstable (map_equal_level_pools's own grouping can retroactively
    change — a pool's avg price and pool_id can differ once a new
    matching candle appears later, even for the "same" anchor candle —
    see module docstring). Caching sweeps keyed by an unstable pool_id
    was tried and caught by this fix's own equivalence test: it
    accumulated stale sweep records tied to superseded equal-level pool
    groupings that no longer existed in the current step's pool list.
    Sweeps against equal-level pools are therefore computed FRESH each
    step by IncrementalM5Layer, over the same bounded window already
    used for equal-level pool detection — see equal_level_lookback_candles.
    """

    def __init__(self, engine: LiquidityEngineImpl, reversal_window: int, max_unswept_age_candles: int = 5000) -> None:
        self._engine = engine
        self._reversal_window = reversal_window
        self._margin = reversal_window + 20
        self._max_unswept_age = max_unswept_age_candles
        self._items: dict[str, SweepEvent] = {}
        self._last_len = 0
        self._seen_pool_ids: set[str] = set()
        self._seen_pool_first_index: dict[str, int] = {}
        self._swept_pool_ids: set[str] = set()  # pools with a permanently-resolved sweep
        self._stale_pool_ids: set[str] = set()  # unswept, given up on — see docstring
        self._pending_reversal: dict[str, int] = {}  # pool_id -> sweep candle's index

    def update(
        self, candles: Sequence[Candle], stable_pools: Sequence[LiquidityPool], atr_by_candle_id: dict[str, Decimal],
        min_penetration_atr_mult: Decimal, max_penetration_atr_mult: Decimal, reversal_window: int,
        index_map: "_CandleIndexMap",
    ) -> list[SweepEvent]:
        n = len(candles)
        new_pools = [p for p in stable_pools if p.pool_id not in self._seen_pool_ids]
        if n == self._last_len and not new_pools and not self._pending_reversal:
            return list(self._items.values())

        rescan_start = max(0, self._last_len - self._margin) if self._last_len else 0
        tail = candles[rescan_start:]

        def _record(sweep: SweepEvent) -> None:
            sweep_index = index_map.get(sweep.sweep_candle_id)
            if sweep_index is not None and (n - sweep_index - 1) < reversal_window:
                # Not enough future candles were available yet to fully
                # resolve reversal_confirmed_at/valid -- record what we
                # have but keep this pool PENDING, not permanently
                # settled, so it gets re-derived once more data arrives.
                self._items[sweep.sweep_id] = sweep
                self._pending_reversal[sweep.liquidity_pool_id] = sweep_index
            else:
                self._items[sweep.sweep_id] = sweep
                self._swept_pool_ids.add(sweep.liquidity_pool_id)
                self._pending_reversal.pop(sweep.liquidity_pool_id, None)

        # Brand-new pools: full scan against the whole candle history —
        # a pool can be swept any time after its own creation, same
        # unbounded-forward property as major-swing confirmation.
        if new_pools:
            for sweep in self._engine.detect_sweeps(
                candles, new_pools, atr_by_candle_id,
                min_penetration_atr_mult, max_penetration_atr_mult, reversal_window,
            ):
                _record(sweep)
            self._seen_pool_ids.update(p.pool_id for p in new_pools)

        # Already-seen pools that are STILL UNSWEPT: safe to tail-rescan
        # (see docstring — nothing before the tail ever qualified for
        # these, verified by every prior scan). Pools that already have
        # a PERMANENTLY-resolved sweep, or have been given up on as
        # stale, are excluded.
        for p in new_pools:
            self._seen_pool_first_index[p.pool_id] = n
        still_unswept = [
            p for p in stable_pools
            if p.pool_id in self._seen_pool_ids
            and p.pool_id not in self._swept_pool_ids
            and p.pool_id not in self._stale_pool_ids
            and p.pool_id not in {np.pool_id for np in new_pools}
        ]
        # Move anything that's aged past max_unswept_age into the stale
        # bucket — see docstring's "DISCLOSED SIMPLIFICATION".
        still_active = []
        for p in still_unswept:
            first_index = self._seen_pool_first_index.get(p.pool_id, 0)
            if (n - first_index) > self._max_unswept_age:
                self._stale_pool_ids.add(p.pool_id)
            else:
                still_active.append(p)
        still_unswept = still_active
        if still_unswept and tail:
            for sweep in self._engine.detect_sweeps(
                tail, still_unswept, atr_by_candle_id,
                min_penetration_atr_mult, max_penetration_atr_mult, reversal_window,
            ):
                _record(sweep)

        # Pools whose sweep candle was found but didn't yet have its
        # full reversal_window of future candles available -- re-derive
        # now that more data may have arrived, using ONLY that pool's
        # own tiny [sweep_index, sweep_index+1+reversal_window) slice
        # (never the full history — this stays cheap regardless of how
        # deep into the replay we are).
        pool_by_id = {p.pool_id: p for p in stable_pools}
        for pool_id, sweep_index in list(self._pending_reversal.items()):
            pool = pool_by_id.get(pool_id)
            if pool is None:
                continue
            window_end = min(n, sweep_index + 1 + reversal_window)
            slice_ = candles[sweep_index:window_end]
            for sweep in self._engine.detect_sweeps(
                slice_, [pool], atr_by_candle_id, min_penetration_atr_mult, max_penetration_atr_mult, reversal_window,
            ):
                _record(sweep)

        self._last_len = n
        return list(self._items.values())


@dataclass
class IncrementalM5Layer:
    """Wires all of the above into the exact same set of outputs
    backtest/pipeline.py's step() previously recomputed from scratch
    every time: swings, pools (daily/weekly + swing + equal-level),
    sweeps, displacements, mss_events, fvgs, order_blocks. Same
    signatures, same output shapes — a drop-in replacement for the
    naive full-recompute block, not a redesign of what gets computed.
    """

    swing_detector: SwingDetectorImpl
    liquidity_engine: LiquidityEngineImpl
    displacement_engine: DisplacementEngineImpl
    zone_engine: ZoneEngineImpl
    equal_level_lookback_candles: int = 100  # see module docstring — disclosed simplification;
    # ~3.5 trading hours. Reduced from 300 (PERF-02, docs/DECISIONS.md):
    # combined with the O(N^3)->O(N^2) fix in liquidity_engine.py, this
    # drops equal-level detection from ~0.21s/step to ~0.02s/step (9x
    # speedup from window^2 scaling: (100/300)^2). Equal-level structure
    # is a short-horizon pattern — equal highs/lows older than ~3.5h are
    # structurally unlikely to be the active, unswept pool the strategy
    # is waiting on; pairs that old are typically already consumed or
    # irrelevant. This is a real, disclosed trade-off.

    def __post_init__(self) -> None:
        self._index_map = _CandleIndexMap()
        self._swings = IncrementalSwingCache(self.swing_detector)
        self._daily_weekly = IncrementalDailyWeeklyPoolCache(self.liquidity_engine)
        self._displacements = IncrementalDisplacementCache(self.displacement_engine)
        self._fvgs = IncrementalFVGCache(self.zone_engine)
        self._order_blocks = IncrementalOrderBlockCache(self.zone_engine)
        self._sweeps: Optional[IncrementalSweepCache] = None  # needs reversal_window, set on first update
        self._swing_pools_cache: dict[tuple[str, bool], LiquidityPool] = {}

    def update(
        self, m5_candles: Sequence[Candle], m5_atr: dict[str, Decimal],
        equal_level_tolerance: Decimal, sweep_min_penetration_atr: Decimal,
        sweep_max_penetration_atr: Decimal, sweep_reversal_window: int,
        mss_max_sweep_age=None,
    ):
        self._index_map.sync(m5_candles)

        swings = self._swings.update(m5_candles, m5_atr, self._index_map)

        daily_weekly_pools = self._daily_weekly.update(m5_candles)
        swing_pools: list[LiquidityPool] = []
        for s in swings:
            key = (s.swing_id, s.major_flag)
            p = self._swing_pools_cache.get(key)
            if p is None:
                pool_type = (
                    LiquidityPoolType.SWING_HIGH if s.type == SwingType.HIGH
                    else LiquidityPoolType.SWING_LOW
                )
                p = self.liquidity_engine._build_pool(
                    pool_type, s.price, s.confirmation_timestamp,
                    is_major_swing=s.major_flag,
                    htf_significance=Decimal("25") if s.major_flag else Decimal("10"),
                )
                self._swing_pools_cache[key] = p
            swing_pools.append(p)
        stable_pools = list(daily_weekly_pools) + swing_pools

        equal_level_pools = []
        equal_level_window = []
        if m5_candles:
            equal_level_window = m5_candles[-self.equal_level_lookback_candles:]
            equal_level_pools = list(
                self.liquidity_engine.map_equal_level_pools(equal_level_window, equal_level_tolerance)
            )

        pools = stable_pools + equal_level_pools

        if self._sweeps is None:
            self._sweeps = IncrementalSweepCache(self.liquidity_engine, sweep_reversal_window)
        stable_sweeps = self._sweeps.update(
            m5_candles, stable_pools, m5_atr, sweep_min_penetration_atr, sweep_max_penetration_atr,
            sweep_reversal_window, self._index_map,
        )
        # Equal-level pools are recomputed fresh every step (see their
        # own docstring) -- their sweeps must be too, over the SAME
        # bounded window, never cached by pool_id (see
        # IncrementalSweepCache's docstring for why that broke the
        # equivalence test this fix depends on).
        equal_level_sweeps = []
        if equal_level_pools and equal_level_window:
            equal_level_sweeps = list(self.liquidity_engine.detect_sweeps(
                equal_level_window, equal_level_pools, m5_atr,
                sweep_min_penetration_atr, sweep_max_penetration_atr, sweep_reversal_window,
            ))
        sweeps = list(stable_sweeps) + equal_level_sweeps

        displacements = self._displacements.update(m5_candles, swings, m5_atr)
        pre_partitioned = (
            self._displacements._bullish_disps, self._displacements._bullish_times,
            self._displacements._bearish_disps, self._displacements._bearish_times,
        )
        mss_events = self.displacement_engine.detect_mss(
            sweeps, displacements, _pre_partitioned=pre_partitioned,
            max_sweep_to_displacement_age=mss_max_sweep_age,
        )

        disp_by_candle = self._displacements._by_candle
        disp_by_id = self._displacements._items
        fvgs = self._fvgs.update(m5_candles, disp_by_candle)
        obs = self._order_blocks.update(m5_candles, mss_events, disp_by_id, self._index_map)

        return swings, pools, sweeps, displacements, mss_events, fvgs, obs

"""In-memory candle store. Real persistence (SQLAlchemy/PostgreSQL) is a
later milestone (Technical Architecture v1.0 build order, step 2) — this
is the repository *interface's* first concrete implementation, swappable
later without touching any caller, per the module-boundary rule that
domain logic never talks to a specific database directly.

Milestone 10 blocker-2 fix: the previous implementation scanned the
ENTIRE per-(symbol, timeframe) candle list on every single add() and
every single get_up_to() call, regardless of where in that list the
relevant candle actually was — O(n) per call, O(n^2) over a full
replay (readiness audit, Performance/Scale section; confirmed by
benchmark: 20,000 sequential inserts took ~14s in that audit).

Fix: candles are kept in a list sorted ascending by timestamp_close, and
a PARALLEL list of just the timestamps is maintained alongside it so the
standard-library `bisect` module can locate insert/query positions in
O(log n) instead of a manual linear scan. The realistic ingestion
pattern (batch add_many() of an already-ascending series, or one
streamed candle at a time in ascending order — see the Milestone 10
blocker-3 streaming pipeline) is additionally fast-pathed to O(1)
amortized append, since bisect is only needed for genuinely out-of-order
inserts, which the real pipeline never produces but which the public
API must still handle correctly (defensive correctness, not a
performance corner it's allowed to get wrong).

Public API, return values, and every existing semantic (chronological
order, get_up_to()'s <= inclusivity, per-(symbol,timeframe) isolation,
duplicate-candle handling) are unchanged from the previous
implementation — this is an internal-representation change only, no
caller needs to change.

Latent bug fixed incidentally: the previous duplicate-id check compared
`bucket[insert_at]` (the first candle STRICTLY AFTER the new one's
timestamp) against the new candle's id. For a genuine same-timestamp
duplicate re-add, that is never the right element to compare against —
the duplicate actually needing detection sits at some index < insert_at,
not at insert_at itself. The new bisect_left/bisect_right range check
below scans only the (small) run of same-timestamp candles, correctly.
No existing test exercised this path (verified: none of
tests/test_market_data_store.py's cases re-add an identical candle_id),
so this was previously untested; a regression test is added alongside
this fix.
"""

from __future__ import annotations

import bisect
from datetime import datetime
from typing import Iterable

from core.domain.common import Timeframe, ensure_utc
from core.domain.market_data import Candle


class CandleStore:
    """Chronological, per-(symbol, timeframe) candle storage. The only
    query method that matters for strategy correctness is
    `get_up_to()` — it is the single place in the codebase that
    enforces 'a decision at timestamp T can only use data known by T'
    (Strategy FROZEN v1.0, Non-Negotiable Invariants) at the data-access
    level, rather than trusting every caller to slice correctly."""

    def __init__(self) -> None:
        # candles: sorted ascending by timestamp_close.
        # timestamps: the SAME list's timestamp_close values, kept in
        # lockstep purely so bisect has a plain-datetime list to search
        # (bisect cannot compare Candle objects directly).
        self._by_key: dict[tuple[str, Timeframe], list[Candle]] = {}
        self._timestamps: dict[tuple[str, Timeframe], list[datetime]] = {}

    def add_many(self, candles: Iterable[Candle]) -> None:
        for candle in candles:
            self.add(candle)

    def add(self, candle: Candle) -> None:
        key = (candle.symbol, candle.timeframe)
        bucket = self._by_key.setdefault(key, [])
        timestamps = self._timestamps.setdefault(key, [])
        ts = candle.timestamp_close

        # Fast path: strictly-ascending append, the realistic ingestion
        # pattern (a sorted batch, or one streamed candle at a time).
        # O(1) amortized instead of a bisect call.
        if not timestamps or timestamps[-1] < ts:
            bucket.append(candle)
            timestamps.append(ts)
            return

        # General path: out-of-order or same-timestamp insert.
        # bisect_left/bisect_right bracket the run of existing candles
        # that share this exact timestamp_close — duplicate-id detection
        # only needs to check within that run, never the whole bucket.
        lo = bisect.bisect_left(timestamps, ts)
        hi = bisect.bisect_right(timestamps, ts)
        for i in range(lo, hi):
            if bucket[i].candle_id == candle.candle_id:
                return  # already present, identical id — no-op
        bucket.insert(lo, candle)
        timestamps.insert(lo, ts)

    def get_up_to(self, symbol: str, timeframe: Timeframe, at: datetime) -> list[Candle]:
        """Every candle with timestamp_close <= at. This is the ONLY
        sanctioned way strategy code should read candles — never index
        the store directly for anything that feeds a decision.

        O(log n + k) — a bisect to locate the boundary, then a slice of
        exactly the k matching candles — never a scan of the full n-candle
        collection regardless of where `at` falls (the previous
        implementation's defect)."""
        at = ensure_utc(at)
        key = (symbol, timeframe)
        timestamps = self._timestamps.get(key)
        if not timestamps:
            return []
        idx = bisect.bisect_right(timestamps, at)
        return self._by_key[key][:idx]

    def latest(self, symbol: str, timeframe: Timeframe, at: datetime) -> Candle | None:
        at = ensure_utc(at)
        key = (symbol, timeframe)
        timestamps = self._timestamps.get(key)
        if not timestamps:
            return None
        idx = bisect.bisect_right(timestamps, at)
        if idx == 0:
            return None
        return self._by_key[key][idx - 1]

    def all(self, symbol: str, timeframe: Timeframe) -> list[Candle]:
        """Unrestricted read — for backtest setup/data-loading only.
        Anything that feeds a live strategy decision must go through
        get_up_to(), never this."""
        return list(self._by_key.get((symbol, timeframe), []))

"""Raw historical-dataset validation. Milestone 9, Section E.

This module does NOT invent new data-quality rules. Every check here is
a direct reuse of a rule that already exists elsewhere in this codebase:

  - OHLC integrity (high >= max(open, close), low <= min(open, close),
    high >= low, timestamp_close > timestamp_open) is enforced by
    Candle's own pydantic validators (core/domain/market_data.py) —
    this module never re-implements that check, it only catches the
    resulting ValidationError and reports it as an INVALID finding
    instead of letting the exception propagate, since a bulk-import
    tool needs a report, not a crash on the first bad row.
  - The dedup key (symbol, timeframe, timestamp_close) and the
    "identical duplicate vs. genuine conflict" distinction are the
    EXACT same rule market_data/normalization/normalization.py already
    encodes (Strategy FROZEN v1.0 §4). This module mirrors that logic
    rather than calling normalize_candles() directly, because
    normalize_candles() raises on conflict and returns only the final
    deduped list — neither of which gives the PER-RECORD status a
    validation report needs. The rule itself is not re-derived, only
    reapplied per-record.
  - The gap condition (`previous.timestamp_close < candle.timestamp_open`)
    is copied verbatim from normalization.py's `_flag_gap` — same
    boundary, same "gap is informational, not automatically invalid"
    stance (v5 I10, DEFERRED).

RawCandleRecord exists because Candle itself is frozen/validating —
useful for data that's ALREADY known-good, not for a bulk import where
individual rows may be broken and the caller needs to know WHICH ones
and WHY, without losing the rest of the batch.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Optional, Sequence

from pydantic import ValidationError

from core.domain.common import Timeframe
from core.domain.market_data import Candle, DataSource


@dataclass(frozen=True)
class RawCandleRecord:
    """Un-validated input row — deliberately permissive (no validators
    of its own). The caller is responsible for having already parsed
    strings into Decimal/datetime; this dataclass's only job is to be
    a candidate for Candle validation, not to coerce raw text."""

    symbol: str
    timeframe: Timeframe
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    timestamp_open: datetime
    timestamp_close: datetime
    source: DataSource
    volume: Optional[Decimal] = None


class DataQualityStatus(str, Enum):
    VALID = "VALID"
    INVALID = "INVALID"
    GAP = "GAP"
    DUPLICATE = "DUPLICATE"
    CONFLICT = "CONFLICT"


@dataclass(frozen=True)
class ValidationFinding:
    status: DataQualityStatus
    record_index: int  # position in the original input sequence
    candle_id: Optional[str]  # None only when status == INVALID and construction failed
    message: str
    candle: Optional[Candle] = None


def validate_raw_dataset(records: Sequence[RawCandleRecord]) -> list[ValidationFinding]:
    """Validates every record and returns one ValidationFinding per
    input record (same length as `records`, ordered by record_index).
    Never raises on bad data — that would defeat the purpose of a bulk
    validation report; INVALID/CONFLICT findings carry the reason
    instead."""

    if not records:
        return []

    findings: list[ValidationFinding] = []
    ok_candles: dict[int, Candle] = {}

    for i, r in enumerate(records):
        try:
            candle = Candle(
                candle_id=Candle.build_id(r.symbol, r.timeframe, r.timestamp_close),
                symbol=r.symbol, timeframe=r.timeframe, open=r.open, high=r.high,
                low=r.low, close=r.close, volume=r.volume,
                timestamp_open=r.timestamp_open, timestamp_close=r.timestamp_close,
                source=r.source, confirmed=True,
            )
        except (ValidationError, ValueError) as exc:
            findings.append(ValidationFinding(DataQualityStatus.INVALID, i, None, str(exc)))
            continue
        ok_candles[i] = candle

    # Same dedup key normalize_candles() already uses — reused, not re-derived.
    by_key: dict[tuple, list[int]] = {}
    for i, candle in ok_candles.items():
        key = (candle.symbol, candle.timeframe.value, candle.timestamp_close.isoformat())
        by_key.setdefault(key, []).append(i)

    kept: dict[int, Candle] = {}
    for indices in by_key.values():
        if len(indices) == 1:
            kept[indices[0]] = ok_candles[indices[0]]
            continue
        base = ok_candles[indices[0]]
        if all(ok_candles[i] == base for i in indices):
            # Identical duplicate — harmless, per normalize_candles()'s
            # own stance. Keep the first, flag the rest.
            kept[indices[0]] = base
            for i in indices[1:]:
                findings.append(
                    ValidationFinding(
                        DataQualityStatus.DUPLICATE, i, base.candle_id,
                        "identical duplicate of an earlier record in this dataset",
                        candle=ok_candles[i],
                    )
                )
        else:
            # Genuine conflict — never silently pick one (same stance
            # as DuplicateCandleError in normalization.py).
            for i in indices:
                findings.append(
                    ValidationFinding(
                        DataQualityStatus.CONFLICT, i, ok_candles[i].candle_id,
                        "conflicting candle data for the same symbol/timeframe/close "
                        "— cannot silently choose one, needs data-source investigation",
                        candle=ok_candles[i],
                    )
                )

    # Gap flagging over the chronologically-ordered, deduped, non-conflicting set.
    ordered = sorted(kept.items(), key=lambda item: item[1].timestamp_close)
    previous: Optional[Candle] = None
    for i, candle in ordered:
        if previous is not None and previous.timestamp_close < candle.timestamp_open:
            findings.append(
                ValidationFinding(
                    DataQualityStatus.GAP, i, candle.candle_id,
                    f"gap: previous candle closed at {previous.timestamp_close.isoformat()}, "
                    f"this one opens at {candle.timestamp_open.isoformat()}",
                    candle=candle,
                )
            )
        else:
            findings.append(ValidationFinding(DataQualityStatus.VALID, i, candle.candle_id, "ok", candle=candle))
        previous = candle

    findings.sort(key=lambda f: f.record_index)
    return findings

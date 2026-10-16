"""Candle normalization. Strategy FROZEN v1.0 §4: 'never infer missing
candles as real candles', 'dedupe by symbol + timeframe + close
timestamp'. OHLC integrity itself is already enforced by the Candle
model's own validators (core/domain/market_data.py) — this module's job
is sequence-level concerns: ordering, dedup, and gap flagging."""

from __future__ import annotations

from core.domain.market_data import Candle, DataQualityFlag


class DuplicateCandleError(ValueError):
    """Raised when two non-identical candles claim the same
    (symbol, timeframe, timestamp_close) — a genuine data-quality
    problem, not a normal duplicate to silently discard."""


def normalize_candles(raw_candles: list[Candle]) -> list[Candle]:
    """Sort chronologically, deduplicate by (symbol, timeframe,
    timestamp_close), and flag gaps between consecutive candles.

    Two candles with the same key and IDENTICAL field values are treated
    as a harmless duplicate (e.g. re-delivered by the data feed) and
    collapsed to one. Two candles with the same key but DIFFERENT values
    are a genuine data-quality problem — raised, not silently resolved,
    since silently picking one would be inventing which is 'true'.
    """

    by_key: dict[tuple[str, str, str], Candle] = {}
    for candle in raw_candles:
        key = (candle.symbol, candle.timeframe.value, candle.timestamp_close.isoformat())
        existing = by_key.get(key)
        if existing is None:
            by_key[key] = candle
        elif existing != candle:
            raise DuplicateCandleError(
                f"conflicting candles for {key}: {existing} vs {candle} — "
                f"cannot silently choose one, this needs data-source investigation"
            )
        # else: identical duplicate, silently collapsed (not an error).

    ordered = sorted(by_key.values(), key=lambda c: c.timestamp_close)
    return [_flag_gap(prev, curr) for prev, curr in _with_previous(ordered)]


def _with_previous(candles: list[Candle]):
    previous: Candle | None = None
    for candle in candles:
        yield previous, candle
        previous = candle


def _flag_gap(previous: Candle | None, candle: Candle) -> Candle:
    """A gap = this candle's open doesn't immediately follow the prior
    candle's close in the expected sequence. Flagging is informational
    only — Strategy FROZEN v1.0 explicitly says a gap candle is not
    automatically invalid (v5 I10, DEFERRED)."""

    if previous is None:
        return candle
    if previous.timestamp_close < candle.timestamp_open:
        if DataQualityFlag.GAP not in candle.data_quality_flags:
            return candle.model_copy(
                update={"data_quality_flags": candle.data_quality_flags + (DataQualityFlag.GAP,)}
            )
    return candle

"""Sequential replay engine. Implements the backtest engine contract's
core loop shape (Phase 2 §22):

    for each decision_timestamp T:
        candles = data_up_to_and_including(T)
        state = canonical_pipeline.analyze(candles, config, T)
        ...

This module only produces the (T, candles-up-to-T) sequence — it does not
call the canonical pipeline itself (that doesn't exist until later
milestones). Keeping this separate means the same ReplayEngine can back
both the eventual backtest/ module and any local pipeline-development
testing, without duplicating the no-look-ahead mechanism."""

from __future__ import annotations

from datetime import datetime
from typing import Iterator, NamedTuple

from core.domain.common import Timeframe
from core.domain.market_data import Candle
from market_data.candle_store.store import CandleStore


class ReplayStep(NamedTuple):
    at: datetime
    candles: list[Candle]


class ReplayEngine:
    """Steps through a symbol/timeframe's candles one close-timestamp at
    a time, at each step exposing only candles with timestamp_close <=
    that step's timestamp — mirroring CandleStore.get_up_to() exactly,
    since that's the only place look-ahead could otherwise sneak in."""

    def __init__(self, store: CandleStore, symbol: str, timeframe: Timeframe) -> None:
        self._store = store
        self._symbol = symbol
        self._timeframe = timeframe

    def run(self) -> Iterator[ReplayStep]:
        all_candles = self._store.all(self._symbol, self._timeframe)
        for candle in all_candles:
            at = candle.timestamp_close
            yield ReplayStep(at=at, candles=self._store.get_up_to(self._symbol, self._timeframe, at))

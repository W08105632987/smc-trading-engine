"""NewsProvider Protocol. Amendment v1.0 §10: the core engine depends on
this abstraction, never a concrete provider. No provider implementation
exists yet — none is required by the currently-frozen architecture."""

from __future__ import annotations

from datetime import datetime
from typing import Protocol, Sequence

from news.domain.news_event import NewsEvent


class NewsProvider(Protocol):
    def get_events(self, start: datetime, end: datetime) -> Sequence[NewsEvent]: ...

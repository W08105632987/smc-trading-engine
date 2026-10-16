from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from news.domain.news_event import NewsEvent, NewsEventStatus, NewsImpact
from news.domain.news_window import NewsWindow
from news.filtering.config import NewsFilterConfig, NewsMode


def _dt(minute: int) -> datetime:
    # timedelta-based so any minute offset (not just 0-59) is valid.
    return datetime(2026, 1, 5, 0, 0, tzinfo=timezone.utc) + timedelta(minutes=minute)


def make_news_event(**overrides) -> NewsEvent:
    defaults = dict(
        event_id="n1", provider="test-provider", title="NFP", currency="USD",
        country="US", scheduled_at=_dt(60), impact=NewsImpact.HIGH,
        source="calendar-api", status=NewsEventStatus.SCHEDULED, created_at=_dt(0),
    )
    defaults.update(overrides)
    return NewsEvent(**defaults)


class TestNewsEvent:
    def test_valid_event(self):
        ev = make_news_event()
        assert ev.impact == NewsImpact.HIGH

    def test_not_knowable_before_created_at(self):
        # News Awareness Amendment v1.0 §11 follow-up (LOCKED): a news
        # event cannot affect replay decisions before created_at.
        ev = make_news_event(created_at=_dt(0))
        assert ev.is_knowable_at(_dt(-1)) is False

    def test_knowable_exactly_at_created_at(self):
        ev = make_news_event(created_at=_dt(0))
        assert ev.is_knowable_at(_dt(0)) is True

    def test_knowable_after_created_at(self):
        ev = make_news_event(created_at=_dt(0))
        assert ev.is_knowable_at(_dt(1)) is True

    def test_knowable_well_before_scheduled_at(self):
        # The whole point of the LOCKED rule: an event announced Monday
        # for a Tuesday release is knowable well ahead of scheduled_at —
        # otherwise a "before the event" blackout window would require
        # look-ahead by construction.
        ev = make_news_event(created_at=_dt(0), scheduled_at=_dt(60 * 28))
        # created_at passed; scheduled_at is still ~28 hours away.
        assert ev.is_knowable_at(_dt(1)) is True

    def test_scheduled_at_does_not_gate_knowability(self):
        # Knowability depends only on created_at, never on scheduled_at —
        # even far in advance of the release itself.
        ev = make_news_event(created_at=_dt(0), scheduled_at=_dt(10_000))
        assert ev.is_knowable_at(_dt(5)) is True


class TestNewsWindow:
    def test_valid_window(self):
        window = NewsWindow(
            news_event_id="n1", currency="USD", impact=NewsImpact.HIGH,
            window_start=_dt(45), window_end=_dt(75),
        )
        assert window.contains(_dt(60)) is True
        assert window.contains(_dt(0)) is False

    def test_end_before_start_rejected(self):
        with pytest.raises(ValidationError):
            NewsWindow(
                news_event_id="n1", currency="USD", impact=NewsImpact.HIGH,
                window_start=_dt(75), window_end=_dt(45),
            )


class TestNewsFilterConfig:
    """Amendment v1.0 §6: FILTER_ONLY/BLACKOUT modes must not silently
    default their thresholds — this is the test-level guarantee that
    values are genuinely required, not just documented as required."""

    def test_disabled_mode_needs_no_values(self):
        cfg = NewsFilterConfig()  # mode defaults to DISABLED
        assert cfg.mode == NewsMode.DISABLED

    def test_blackout_mode_without_values_rejected(self):
        with pytest.raises(ValidationError, match="must be configured, never defaulted"):
            NewsFilterConfig(mode=NewsMode.BLACKOUT)

    def test_filter_only_without_values_rejected(self):
        with pytest.raises(ValidationError):
            NewsFilterConfig(mode=NewsMode.FILTER_ONLY)

    def test_blackout_mode_with_explicit_values_ok(self):
        # NOTE: these numbers are test fixtures only, not recommendations
        # — the amendment explicitly forbids treating any specific
        # minute value as decided.
        cfg = NewsFilterConfig(
            mode=NewsMode.BLACKOUT, minimum_impact=NewsImpact.HIGH,
            minutes_before=30, minutes_after=30,
        )
        assert cfg.mode == NewsMode.BLACKOUT

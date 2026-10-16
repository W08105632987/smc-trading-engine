from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from core.domain.common import Direction
from core.domain.setups import HTFContextSnapshot, Setup, SetupState
from strategy.setups.setup_engine import SetupAdmissionEngine, apply_displacement


def _dt(minute: int) -> datetime:
    return datetime(2026, 1, 5, 0, 0, tzinfo=timezone.utc) + timedelta(minutes=minute)


def make_setup(setup_id: str, score: Decimal, created_min: int, state=SetupState.WAITING_FOR_RETEST) -> Setup:
    return Setup(
        setup_id=setup_id, originating_zone_id=f"z-{setup_id}", originating_candle_id=f"c-{setup_id}",
        direction=Direction.BULLISH, created_at=_dt(created_min), state=state, score=score,
        score_snapshot={"factor": score},
        htf_context=HTFContextSnapshot(d1_state="BULLISH", h4_state="BULLISH", h1_state="BULLISH"),
        expiry_at=_dt(created_min + 1000), invalidation_level=Decimal("2390"),
    )


class TestAdmissionUnderCap:
    def test_admitted_when_under_cap(self):
        engine = SetupAdmissionEngine(max_pending_setups=3)
        existing = [make_setup("s1", Decimal("70"), 0), make_setup("s2", Decimal("75"), 1)]
        candidate = make_setup("s3", Decimal("60"), 2)
        result = engine.evaluate(existing, candidate)
        assert result.admitted is True
        assert result.displaced is None


class TestAdmissionAtCap:
    def test_lowest_scoring_existing_is_displaced(self):
        engine = SetupAdmissionEngine(max_pending_setups=3)
        existing = [
            make_setup("s1", Decimal("70"), 0),
            make_setup("s2", Decimal("60"), 1),  # lowest
            make_setup("s3", Decimal("80"), 2),
        ]
        candidate = make_setup("s4", Decimal("75"), 3)
        result = engine.evaluate(existing, candidate)
        assert result.admitted is True
        assert result.displaced.setup_id == "s2"

    def test_candidate_rejected_when_it_is_the_lowest(self):
        engine = SetupAdmissionEngine(max_pending_setups=3)
        existing = [
            make_setup("s1", Decimal("70"), 0),
            make_setup("s2", Decimal("75"), 1),
            make_setup("s3", Decimal("80"), 2),
        ]
        candidate = make_setup("s4", Decimal("65"), 3)  # lowest of the pool
        result = engine.evaluate(existing, candidate)
        assert result.admitted is False
        assert result.displaced is None

    def test_equal_score_tie_rejects_newest_which_is_the_candidate(self):
        engine = SetupAdmissionEngine(max_pending_setups=3)
        existing = [
            make_setup("s1", Decimal("70"), 0),
            make_setup("s2", Decimal("70"), 1),  # tied with candidate
            make_setup("s3", Decimal("80"), 2),
        ]
        candidate = make_setup("s4", Decimal("70"), 3)  # same score, but newest (created_at later)
        result = engine.evaluate(existing, candidate)
        assert result.admitted is False


class TestConfirmedExemption:
    def test_confirmed_setups_never_counted_toward_cap(self):
        engine = SetupAdmissionEngine(max_pending_setups=1)
        existing = [
            make_setup("s1", Decimal("90"), 0, state=SetupState.CONFIRMED),
            make_setup("s2", Decimal("90"), 1, state=SetupState.CONFIRMED),
            make_setup("s3", Decimal("90"), 2, state=SetupState.CONFIRMED),
        ]
        # 3 CONFIRMED setups exist, but cap=1 only applies to pending
        # states — with zero actual pending setups, the candidate is
        # admitted freely.
        candidate = make_setup("s4", Decimal("10"), 3)
        result = engine.evaluate(existing, candidate)
        assert result.admitted is True
        assert result.displaced is None

    def test_confirmed_setup_never_appears_as_displaced_even_if_lowest_scored(self):
        engine = SetupAdmissionEngine(max_pending_setups=1)
        existing = [
            make_setup("s1", Decimal("5"), 0, state=SetupState.CONFIRMED),  # lowest score, but CONFIRMED
            make_setup("s2", Decimal("70"), 1, state=SetupState.WAITING_FOR_RETEST),  # the one actually pending
        ]
        candidate = make_setup("s3", Decimal("80"), 2)
        result = engine.evaluate(existing, candidate)
        # s2 is the only pending setup (cap=1 already met), so it's the
        # one evaluated for displacement — s1 (CONFIRMED) is never
        # touched despite having a much lower score.
        assert result.admitted is True
        assert result.displaced.setup_id == "s2"


class TestApplyDisplacement:
    def test_produces_displaced_state_with_reference(self):
        setup = make_setup("s1", Decimal("60"), 0)
        result = apply_displacement(setup, displaced_by_setup_id="s2")
        assert result.state == SetupState.DISPLACED
        assert result.displaced_by_setup_id == "s2"
        assert result.setup_id == setup.setup_id  # same setup, new state

    def test_original_setup_instance_unchanged(self):
        # Setup is frozen/immutable — apply_displacement must not mutate
        # the original in place.
        setup = make_setup("s1", Decimal("60"), 0)
        apply_displacement(setup, displaced_by_setup_id="s2")
        assert setup.state == SetupState.WAITING_FOR_RETEST  # untouched

    def test_cannot_displace_a_terminal_setup(self):
        setup = make_setup("s1", Decimal("60"), 0, state=SetupState.REJECTED)
        with pytest.raises(ValueError):
            apply_displacement(setup, displaced_by_setup_id="s2")

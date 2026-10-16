from datetime import datetime, timedelta, timezone
from decimal import Decimal

from core.domain.common import Direction
from core.domain.setups import HTFContextSnapshot, Setup, SetupState
from backtest.setup_lifecycle import evaluate_setup_lifecycle


def _dt(minute: int) -> datetime:
    return datetime(2026, 1, 5, 0, 0, tzinfo=timezone.utc) + timedelta(minutes=minute)


def make_setup(direction=Direction.BULLISH, invalidation=Decimal("2390"), expiry_min=100) -> Setup:
    return Setup(
        setup_id="s1", originating_zone_id="z1", originating_candle_id="c1", direction=direction,
        created_at=_dt(0), state=SetupState.WAITING_FOR_RETEST, score=Decimal("78"),
        score_snapshot={"total": Decimal("78")},
        htf_context=HTFContextSnapshot(d1_state="BULLISH", h4_state="BULLISH", h1_state="BULLISH"),
        expiry_at=_dt(expiry_min), invalidation_level=invalidation,
    )


class TestExpiry:
    def test_setup_remains_eligible_before_expiry(self):
        setup = make_setup(expiry_min=100)
        result = evaluate_setup_lifecycle(setup, at=_dt(50), latest_close=Decimal("2400"))
        assert result.state == SetupState.WAITING_FOR_RETEST

    def test_expired_setup_transitions_to_expired(self):
        setup = make_setup(expiry_min=100)
        result = evaluate_setup_lifecycle(setup, at=_dt(150), latest_close=Decimal("2400"))
        assert result.state == SetupState.EXPIRED

    def test_exact_expiry_boundary_expires(self):
        # >= boundary, matching the locked "at >= setup.expiry_at" rule.
        setup = make_setup(expiry_min=100)
        result = evaluate_setup_lifecycle(setup, at=_dt(100), latest_close=Decimal("2400"))
        assert result.state == SetupState.EXPIRED

    def test_one_minute_before_expiry_still_eligible(self):
        setup = make_setup(expiry_min=100)
        result = evaluate_setup_lifecycle(setup, at=_dt(99), latest_close=Decimal("2400"))
        assert result.state == SetupState.WAITING_FOR_RETEST

    def test_expired_setup_cannot_later_be_confirmed(self):
        # Once EXPIRED, it's terminal — a subsequent call at any later
        # time must not resurrect it into a pending state.
        setup = make_setup(expiry_min=100)
        expired = evaluate_setup_lifecycle(setup, at=_dt(150), latest_close=Decimal("2400"))
        assert expired.state == SetupState.EXPIRED
        # Terminal states pass through unchanged on any further evaluation.
        still_expired = evaluate_setup_lifecycle(expired, at=_dt(200), latest_close=Decimal("2400"))
        assert still_expired.state == SetupState.EXPIRED


class TestInvalidation:
    def test_bullish_invalidated_when_close_below_level(self):
        setup = make_setup(direction=Direction.BULLISH, invalidation=Decimal("2390"))
        result = evaluate_setup_lifecycle(setup, at=_dt(10), latest_close=Decimal("2385"))
        assert result.state == SetupState.INVALIDATED

    def test_bullish_not_invalidated_when_close_above_level(self):
        setup = make_setup(direction=Direction.BULLISH, invalidation=Decimal("2390"))
        result = evaluate_setup_lifecycle(setup, at=_dt(10), latest_close=Decimal("2395"))
        assert result.state == SetupState.WAITING_FOR_RETEST

    def test_bearish_invalidated_when_close_above_level(self):
        setup = make_setup(direction=Direction.BEARISH, invalidation=Decimal("2410"))
        result = evaluate_setup_lifecycle(setup, at=_dt(10), latest_close=Decimal("2415"))
        assert result.state == SetupState.INVALIDATED

    def test_invalidated_setup_cannot_later_be_confirmed(self):
        setup = make_setup(direction=Direction.BULLISH, invalidation=Decimal("2390"))
        invalidated = evaluate_setup_lifecycle(setup, at=_dt(10), latest_close=Decimal("2385"))
        assert invalidated.state == SetupState.INVALIDATED
        still_invalidated = evaluate_setup_lifecycle(invalidated, at=_dt(20), latest_close=Decimal("2500"))
        assert still_invalidated.state == SetupState.INVALIDATED

    def test_invalidation_checked_before_expiry_when_both_apply(self):
        # A setup that's both past expiry AND price-invalidated at the
        # same evaluation must report INVALIDATED (the more specific,
        # price-driven condition), not EXPIRED.
        setup = make_setup(direction=Direction.BULLISH, invalidation=Decimal("2390"), expiry_min=50)
        result = evaluate_setup_lifecycle(setup, at=_dt(100), latest_close=Decimal("2385"))
        assert result.state == SetupState.INVALIDATED


class TestConfirmedSetupsUntouched:
    def test_confirmed_setup_passes_through_unchanged(self):
        confirmed = make_setup().model_copy(update={"state": SetupState.CONFIRMED})
        result = evaluate_setup_lifecycle(confirmed, at=_dt(1000), latest_close=Decimal("1"))
        assert result.state == SetupState.CONFIRMED
        assert result == confirmed


class TestDeterminism:
    def test_identical_input_produces_identical_result(self):
        setup = make_setup(expiry_min=100)
        r1 = evaluate_setup_lifecycle(setup, at=_dt(150), latest_close=Decimal("2400"))
        r2 = evaluate_setup_lifecycle(setup, at=_dt(150), latest_close=Decimal("2400"))
        assert r1 == r2

    def test_uses_replay_time_only_never_wall_clock(self):
        # Structural check: the function signature has no access to
        # datetime.now() — verified by inspecting the module for any
        # such call.
        import inspect
        import backtest.setup_lifecycle as module
        source = inspect.getsource(module)
        assert "datetime.now()" not in source
        assert "time.time()" not in source

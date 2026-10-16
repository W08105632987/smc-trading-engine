from datetime import datetime, timedelta, timezone
from decimal import Decimal

from core.domain.common import Direction, Timeframe
from core.domain.market_data import Candle, DataSource
from core.domain.setups import MicroSwingQualification, TouchOutcome
from core.domain.zones import Zone, ZoneType
from strategy.confirmation.confirmation_engine import ConfirmationEngineImpl


def _dt(minute: int) -> datetime:
    return datetime(2026, 1, 5, 0, 0, tzinfo=timezone.utc) + timedelta(minutes=minute)


def make_candle(index: int, open_, high, low, close) -> Candle:
    return Candle(
        candle_id=f"XAUUSD:M5:c{index}:{_dt(index * 5 + 5).isoformat()}",
        symbol="XAUUSD", timeframe=Timeframe.M5,
        open=Decimal(str(open_)), high=Decimal(str(high)), low=Decimal(str(low)), close=Decimal(str(close)),
        timestamp_open=_dt(index * 5), timestamp_close=_dt(index * 5 + 5),
        source=DataSource.BROKER_NATIVE, confirmed=True,
    )


def make_zone(upper, lower) -> Zone:
    return Zone(
        zone_id="z1", zone_type=ZoneType.FVG, direction=Direction.BULLISH,
        upper=Decimal(str(upper)), lower=Decimal(str(lower)),
        origin_candle_id="origin", creation_timestamp=_dt(0),
    )


# Matches the plain-Python simulation verified earlier: touch at c0,
# pivot high at c2 (2401), confirmed by c3, break at c5.
FIXTURE_CANDLES = [
    make_candle(0, 2399.0, 2399.5, 2398.5, 2399.0),  # touch start (overlaps zone 2398-2400)
    make_candle(1, 2399.0, 2399.0, 2397.0, 2397.5),  # dips lower
    make_candle(2, 2398.0, 2401.0, 2398.0, 2400.5),  # PIVOT candidate (high=2401)
    make_candle(3, 2399.5, 2400.0, 2398.5, 2399.0),  # confirms pivot (lower high than c2)
    make_candle(4, 2399.5, 2400.5, 2399.0, 2400.0),  # no break yet
    make_candle(5, 2400.5, 2402.0, 2400.0, 2401.5),  # BREAK: close(2401.5) > pivot(2401)
]
FIXTURE_ATR = {c.candle_id: Decimal("1.0") for c in FIXTURE_CANDLES}


class TestTouchDetectionAndConfirmation:
    def test_full_sequence_produces_confirmed_touch(self):
        zone = make_zone(upper=2400, lower=2398)
        engine = ConfirmationEngineImpl(min_retracement_atr_mult=Decimal("0.1"))

        touch = engine.process_touch(
            "t1", "s1", zone, Direction.BULLISH, FIXTURE_CANDLES, FIXTURE_ATR
        )

        assert touch is not None
        assert touch.outcome == TouchOutcome.CONFIRMED
        assert touch.reaction_leg_start_candle_id == FIXTURE_CANDLES[0].candle_id
        assert touch.break_candle_id == FIXTURE_CANDLES[5].candle_id
        assert touch.ended_at == FIXTURE_CANDLES[5].timestamp_close

    def test_selected_micro_swing_matches_the_pivot(self):
        zone = make_zone(upper=2400, lower=2398)
        engine = ConfirmationEngineImpl(min_retracement_atr_mult=Decimal("0.1"))
        touch = engine.process_touch("t1", "s1", zone, Direction.BULLISH, FIXTURE_CANDLES, FIXTURE_ATR)

        selected = next(ms for ms in touch.micro_swings if ms.micro_swing_id == touch.selected_micro_swing_id)
        assert selected.pivot_price == Decimal("2401")
        assert selected.pivot_candle_id == FIXTURE_CANDLES[2].candle_id
        assert selected.confirmation_candle_id == FIXTURE_CANDLES[3].candle_id
        assert selected.qualification_status == MicroSwingQualification.QUALIFIED

    def test_micro_swing_touch_id_matches_the_touch(self):
        # The cross-touch invariant, verified end to end through the
        # engine (schema-level enforcement was already proven in
        # Milestone 1's domain tests — this confirms the ENGINE actually
        # produces correctly-scoped IDs, not just that the schema would
        # reject a wrong one).
        zone = make_zone(upper=2400, lower=2398)
        engine = ConfirmationEngineImpl(min_retracement_atr_mult=Decimal("0.1"))
        touch = engine.process_touch("t1", "s1", zone, Direction.BULLISH, FIXTURE_CANDLES, FIXTURE_ATR)
        for ms in touch.micro_swings:
            assert ms.touch_id == "t1"

    def test_no_touch_returns_none(self):
        far_away_zone = make_zone(upper=3000, lower=2999)  # never touched by FIXTURE_CANDLES
        engine = ConfirmationEngineImpl(min_retracement_atr_mult=Decimal("0.1"))
        touch = engine.process_touch("t1", "s1", far_away_zone, Direction.BULLISH, FIXTURE_CANDLES, FIXTURE_ATR)
        assert touch is None

    def test_no_break_within_available_data_stays_active(self):
        # Corrected understanding (Milestone 4 review): "no break found
        # in this batch of candles" is NOT the same as "this touch has
        # concluded." Only an external event (zone invalidation, setup
        # expiry) can actually close a touch — this detector alone must
        # never declare NO_BREAK just because it ran out of candles.
        zone = make_zone(upper=2400, lower=2398)
        engine = ConfirmationEngineImpl(min_retracement_atr_mult=Decimal("0.1"))
        truncated = FIXTURE_CANDLES[:5]  # up to and including c4, no c5
        touch = engine.process_touch("t1", "s1", zone, Direction.BULLISH, truncated, FIXTURE_ATR)

        assert touch is not None
        assert touch.outcome == TouchOutcome.ACTIVE
        assert touch.break_candle_id is None
        assert touch.ended_at is None

    def test_break_before_pivot_confirms_impossible_by_construction(self):
        # Adversarial: truncate so only the pivot candle exists, no
        # confirming right-neighbor at all — no pivot can even be
        # detected, let alone broken.
        zone = make_zone(upper=2400, lower=2398)
        engine = ConfirmationEngineImpl(min_retracement_atr_mult=Decimal("0.1"))
        truncated = FIXTURE_CANDLES[:3]  # c0, c1, c2 (pivot) — no c3 to confirm it
        touch = engine.process_touch("t1", "s1", zone, Direction.BULLISH, truncated, FIXTURE_ATR)

        assert touch is not None
        assert touch.outcome == TouchOutcome.ACTIVE  # no pivots found at all yet
        assert touch.micro_swings == ()

    def test_break_candle_can_never_equal_confirmation_candle(self):
        # Adversarial: even if the confirmation candle ITSELF closes
        # beyond the pivot price (which would trivially "confirm" a
        # break on the same candle that confirms the pivot), the search
        # for a break starts strictly AFTER the confirming candle's
        # index — so this must never be reported as a break.
        candles = [
            make_candle(0, 2399.0, 2399.5, 2398.5, 2399.0),
            make_candle(1, 2399.0, 2399.0, 2397.0, 2397.5),
            make_candle(2, 2398.0, 2401.0, 2398.0, 2400.5),  # pivot (high=2401)
            # Confirming candle's OWN close (2401.2) is already beyond
            # the pivot price (2401) — a naive implementation might
            # treat this as an instant break. It must not.
            make_candle(3, 2400.8, 2401.5, 2400.5, 2401.2),
        ]
        atr = {c.candle_id: Decimal("1.0") for c in candles}
        zone = make_zone(upper=2400, lower=2398)
        engine = ConfirmationEngineImpl(min_retracement_atr_mult=Decimal("0.05"))

        touch = engine.process_touch("t1", "s1", zone, Direction.BULLISH, candles, atr)

        assert touch.outcome != TouchOutcome.CONFIRMED
        assert touch.break_candle_id is None


class TestInsufficientRetracementRejection:
    def test_pivot_with_tiny_retracement_is_rejected_not_selected(self):
        candles = [
            make_candle(0, 2399.0, 2399.5, 2398.5, 2399.0),
            make_candle(1, 2399.0, 2399.0, 2397.0, 2397.5),
            make_candle(2, 2398.0, 2401.0, 2398.0, 2400.5),
            # Confirming candle closes almost exactly at the pivot price
            # -> retracement of 0.05, below the 0.1 ATR minimum.
            make_candle(3, 2400.9, 2400.97, 2400.85, 2400.95),
        ]
        atr = {c.candle_id: Decimal("1.0") for c in candles}
        zone = make_zone(upper=2400, lower=2398)
        engine = ConfirmationEngineImpl(min_retracement_atr_mult=Decimal("0.1"))

        touch = engine.process_touch("t1", "s1", zone, Direction.BULLISH, candles, atr)
        assert touch.outcome == TouchOutcome.ACTIVE  # rejected pivot, no confirmation — stays open
        assert touch.selected_micro_swing_id is None
        rejected = touch.micro_swings[0]
        assert rejected.qualification_status == MicroSwingQualification.REJECTED_INSUFFICIENT_RETRACEMENT


class TestSupersededMicroSwing:
    def test_second_qualifying_pivot_supersedes_the_first(self):
        candles = [
            make_candle(0, 2399.0, 2399.5, 2398.5, 2399.0),  # touch start
            make_candle(1, 2399.0, 2399.0, 2397.0, 2397.5),
            make_candle(2, 2398.0, 2401.0, 2398.0, 2400.5),  # pivot #1 (high=2401)
            make_candle(3, 2399.5, 2400.0, 2398.5, 2399.0),  # confirms pivot #1, qualifies
            make_candle(4, 2399.0, 2399.2, 2397.5, 2398.0),  # dips again
            make_candle(5, 2398.0, 2403.0, 2398.0, 2402.5),  # pivot #2 (high=2403, higher than #1)
            make_candle(6, 2400.5, 2401.0, 2399.5, 2400.0),  # confirms pivot #2, qualifies
            make_candle(7, 2401.0, 2404.0, 2400.5, 2403.5),  # BREAK of pivot #2 (close > 2403)
        ]
        atr = {c.candle_id: Decimal("1.0") for c in candles}
        zone = make_zone(upper=2400, lower=2398)
        engine = ConfirmationEngineImpl(min_retracement_atr_mult=Decimal("0.1"))

        touch = engine.process_touch("t1", "s1", zone, Direction.BULLISH, candles, atr)

        qualified_or_superseded = [
            ms for ms in touch.micro_swings
            if ms.qualification_status in (
                MicroSwingQualification.QUALIFIED, MicroSwingQualification.SUPERSEDED
            )
        ]
        assert len(qualified_or_superseded) == 2
        superseded = [ms for ms in qualified_or_superseded if ms.qualification_status == MicroSwingQualification.SUPERSEDED]
        assert len(superseded) == 1
        assert superseded[0].pivot_price == Decimal("2401")  # the first pivot
        assert touch.outcome == TouchOutcome.CONFIRMED
        # Break was evaluated against the SECOND (selected) pivot, not the first.
        selected = next(ms for ms in touch.micro_swings if ms.micro_swing_id == touch.selected_micro_swing_id)
        assert selected.pivot_price == Decimal("2403")

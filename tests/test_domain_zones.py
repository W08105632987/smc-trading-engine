from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from pydantic import ValidationError

from core.domain.common import Direction
from core.domain.zones import FVG, OrderBlock, Zone, ZoneType


def _dt(minute: int) -> datetime:
    # timedelta-based so any minute offset (not just 0-59) is valid.
    return datetime(2026, 1, 5, 0, 0, tzinfo=timezone.utc) + timedelta(minutes=minute)


class TestZone:
    def test_upper_must_exceed_lower(self):
        with pytest.raises(ValidationError):
            Zone(
                zone_id="z1", zone_type=ZoneType.FVG, direction=Direction.BULLISH,
                upper=Decimal("2400"), lower=Decimal("2405"),
                origin_candle_id="c1", creation_timestamp=_dt(0),
            )

    def test_negative_retest_count_rejected(self):
        with pytest.raises(ValidationError):
            Zone(
                zone_id="z1", zone_type=ZoneType.FVG, direction=Direction.BULLISH,
                upper=Decimal("2405"), lower=Decimal("2400"),
                origin_candle_id="c1", creation_timestamp=_dt(0), retest_count=-1,
            )


class TestFVG:
    def test_bullish_fvg(self):
        fvg = FVG(
            zone_id="z1", direction=Direction.BULLISH, upper=Decimal("2405"),
            lower=Decimal("2402"), origin_candle_id="c1", creation_timestamp=_dt(0),
            candle1_id="c1", candle2_id="c2", candle3_id="c3",
        )
        assert fvg.zone_type == ZoneType.FVG


class TestOrderBlock:
    def test_bullish_ob(self):
        ob = OrderBlock(
            zone_id="z2", direction=Direction.BULLISH, upper=Decimal("2400"),
            lower=Decimal("2398"), origin_candle_id="c5", creation_timestamp=_dt(0),
            origin_candle_direction=Direction.BEARISH,
        )
        assert ob.zone_type == ZoneType.ORDER_BLOCK
        assert ob.origin_candle_direction == Direction.BEARISH

import pytest
from decimal import Decimal

from pydantic import ValidationError

from core.domain.broker import AccountMode, BrokerProfile, TPRepresentationMode


def make_profile(**overrides) -> BrokerProfile:
    defaults = dict(
        broker_id="demo-broker", account_mode=AccountMode.HEDGING, symbol="XAUUSD",
        digits=2, tick_size=Decimal("0.01"), tick_value=Decimal("1.00"),
        min_volume=Decimal("0.01"), max_volume=Decimal("100"), volume_step=Decimal("0.01"),
        minimum_stop_distance=Decimal("0.50"), freeze_level=Decimal("0.20"),
        filling_mode="IOC", tp_representation_mode=TPRepresentationMode.SEPARATE_ORDERS,
        trading_sessions=("LONDON", "NEW_YORK"), margin_rules={"leverage": Decimal("100")},
        supported_operations=("MARKET", "MODIFY", "CLOSE"),
    )
    defaults.update(overrides)
    return BrokerProfile(**defaults)


class TestBrokerProfile:
    def test_valid_profile(self):
        profile = make_profile()
        assert profile.account_mode == AccountMode.HEDGING

    def test_min_greater_than_max_rejected(self):
        with pytest.raises(ValidationError):
            make_profile(min_volume=Decimal("10"), max_volume=Decimal("1"))

    def test_staged_partial_close_mode(self):
        profile = make_profile(tp_representation_mode=TPRepresentationMode.STAGED_PARTIAL_CLOSE)
        assert profile.tp_representation_mode == TPRepresentationMode.STAGED_PARTIAL_CLOSE

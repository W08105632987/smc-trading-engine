"""Tests for brokers/profile/. Every BrokerProfile constructed below is
SYNTHETIC TEST DATA ONLY — invented values for exercising the repository
and validator, never claiming to represent any real broker's actual
specifications. This is exactly the boundary the Milestone 7 approval
requires: synthetic profiles exist only inside test fixtures like this
one, never as application-level defaults."""

from decimal import Decimal

import pytest

from brokers.profile.profile_repository import BrokerProfileRepository, DuplicateBrokerProfileError
from brokers.profile.profile_validator import BrokerProfileValidator
from core.domain.broker import AccountMode, BrokerProfile, TPRepresentationMode


def make_synthetic_test_profile(broker_id: str = "synthetic-test-broker-1", **overrides) -> BrokerProfile:
    """SYNTHETIC TEST FIXTURE — every value here is invented for testing
    purposes only. This function must never be imported or referenced
    from application code outside tests/."""
    defaults = dict(
        broker_id=broker_id, account_mode=AccountMode.HEDGING, symbol="XAUUSD",
        digits=2, tick_size=Decimal("0.01"), tick_value=Decimal("1.00"),
        min_volume=Decimal("0.01"), max_volume=Decimal("100"), volume_step=Decimal("0.01"),
        minimum_stop_distance=Decimal("0.50"), freeze_level=Decimal("0.20"),
        filling_mode="IOC", tp_representation_mode=TPRepresentationMode.SEPARATE_ORDERS,
        trading_sessions=("SYNTHETIC_SESSION",), margin_rules={"leverage": Decimal("100")},
        supported_operations=("MARKET", "MODIFY", "CLOSE"),
    )
    defaults.update(overrides)
    return BrokerProfile(**defaults)


class TestRepositoryStartsEmpty:
    def test_fresh_repository_has_no_default_profile(self):
        # The structural proof required by the Milestone 7 approval: no
        # production/default profile is pre-loaded anywhere.
        repo = BrokerProfileRepository()
        assert repo.is_empty() is True
        assert repo.list_all() == ()


class TestRepositoryCRUD:
    def test_add_then_get(self):
        repo = BrokerProfileRepository()
        profile = make_synthetic_test_profile()
        repo.add(profile)
        assert repo.get(profile.broker_id) == profile

    def test_get_unknown_broker_id_returns_none(self):
        repo = BrokerProfileRepository()
        assert repo.get("does-not-exist") is None

    def test_remove(self):
        repo = BrokerProfileRepository()
        profile = make_synthetic_test_profile()
        repo.add(profile)
        repo.remove(profile.broker_id)
        assert repo.get(profile.broker_id) is None

    def test_remove_unknown_id_does_not_raise(self):
        repo = BrokerProfileRepository()
        repo.remove("does-not-exist")  # must not raise

    def test_list_all_returns_every_added_profile(self):
        # NOTE: not using set() here — BrokerProfile has a `margin_rules:
        # dict` field, and pydantic's frozen=True hash generation calls
        # hash() across every field, which raises on a plain dict
        # regardless of frozen status. Comparing by broker_id instead.
        repo = BrokerProfileRepository()
        p1 = make_synthetic_test_profile(broker_id="synthetic-1")
        p2 = make_synthetic_test_profile(broker_id="synthetic-2")
        repo.add(p1)
        repo.add(p2)
        ids = {p.broker_id for p in repo.list_all()}
        assert ids == {"synthetic-1", "synthetic-2"}

    def test_identical_re_add_is_a_no_op(self):
        repo = BrokerProfileRepository()
        profile = make_synthetic_test_profile()
        repo.add(profile)
        repo.add(profile)  # must not raise
        assert len(repo.list_all()) == 1

    def test_conflicting_re_add_raises(self):
        repo = BrokerProfileRepository()
        p1 = make_synthetic_test_profile(broker_id="synthetic-1", digits=2)
        p2 = make_synthetic_test_profile(broker_id="synthetic-1", digits=3)  # same id, different data
        repo.add(p1)
        with pytest.raises(DuplicateBrokerProfileError):
            repo.add(p2)


class TestValidator:
    def test_valid_synthetic_profile_passes(self):
        validator = BrokerProfileValidator()
        result = validator.validate(make_synthetic_test_profile())
        assert result.valid is True
        assert result.errors == ()

    def test_empty_supported_operations_rejected(self):
        validator = BrokerProfileValidator()
        profile = make_synthetic_test_profile(supported_operations=())
        result = validator.validate(profile)
        assert result.valid is False
        assert "supported_operations cannot be empty" in result.errors

    def test_empty_trading_sessions_rejected(self):
        validator = BrokerProfileValidator()
        profile = make_synthetic_test_profile(trading_sessions=())
        result = validator.validate(profile)
        assert result.valid is False
        assert "trading_sessions cannot be empty" in result.errors

    def test_negative_freeze_level_rejected(self):
        validator = BrokerProfileValidator()
        profile = make_synthetic_test_profile(freeze_level=Decimal("-1"))
        result = validator.validate(profile)
        assert result.valid is False

    def test_multiple_violations_all_reported(self):
        validator = BrokerProfileValidator()
        profile = make_synthetic_test_profile(
            supported_operations=(), trading_sessions=(), freeze_level=Decimal("-1"),
        )
        result = validator.validate(profile)
        assert len(result.errors) == 3


class TestNoAccidentalDefaultProfilePromotion:
    """The specific adversarial check the Milestone 7 approval asked
    for: confirm nothing in brokers/profile/ itself constructs or
    exposes a profile without an explicit caller doing so."""

    def test_profile_repository_module_exposes_no_profile_constant(self):
        import brokers.profile.profile_repository as module
        profile_like = [
            name for name in dir(module)
            if not name.startswith("_") and "PROFILE" in name.upper() and name.isupper()
        ]
        assert profile_like == []

    def test_profile_validator_module_exposes_no_profile_constant(self):
        import brokers.profile.profile_validator as module
        profile_like = [
            name for name in dir(module)
            if not name.startswith("_") and "PROFILE" in name.upper() and name.isupper()
        ]
        assert profile_like == []

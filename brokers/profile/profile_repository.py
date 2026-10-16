"""BrokerProfile repository. Implementation Spec v1.0 §10.

CRITICAL BOUNDARY, per the Milestone 7 approval: this module holds NO
production/default BrokerProfile and NO globally-available synthetic
profile constant. OD1 (Execution FROZEN v1.0) deliberately left broker
selection as a deployment-time decision — a repository with a
pre-populated 'default' profile would quietly reverse that. Every
BrokerProfile that ever exists in this repository is put there
explicitly by a caller (a test, or eventually a real deployment
configuration step in a later milestone) — this module never manufactures
one on its own."""

from __future__ import annotations

from core.domain.broker import BrokerProfile


class DuplicateBrokerProfileError(ValueError):
    """Raised on attempting to add a second, DIFFERENT profile under a
    broker_id that's already registered. Adding an identical profile
    again is a harmless no-op."""


class BrokerProfileRepository:
    def __init__(self) -> None:
        self._profiles: dict[str, BrokerProfile] = {}

    def add(self, profile: BrokerProfile) -> None:
        existing = self._profiles.get(profile.broker_id)
        if existing is not None:
            if existing == profile:
                return  # identical re-add, harmless no-op
            raise DuplicateBrokerProfileError(
                f"broker_id {profile.broker_id!r} is already registered with "
                f"different profile data; refusing to silently overwrite it"
            )
        self._profiles[profile.broker_id] = profile

    def get(self, broker_id: str) -> BrokerProfile | None:
        return self._profiles.get(broker_id)

    def remove(self, broker_id: str) -> None:
        self._profiles.pop(broker_id, None)

    def list_all(self) -> tuple[BrokerProfile, ...]:
        return tuple(self._profiles.values())

    def is_empty(self) -> bool:
        """Exists specifically so a caller/test can assert that a fresh
        repository starts with zero profiles — the structural proof
        that no default/production profile is pre-loaded."""
        return len(self._profiles) == 0

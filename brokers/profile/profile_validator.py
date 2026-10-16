"""BrokerProfile validator. Structural sanity checks BEYOND what
BrokerProfile's own Pydantic validators already enforce (min_volume <=
max_volume is already checked on the domain model itself — this module
does not duplicate that, only adds what the domain model doesn't cover).

Every check here is a GENERIC structural constraint (positive tick
size, non-empty operation list) — none of them encode what any real
broker's actual values should be. That stays undefined until a real
broker is selected at deployment time (OD1)."""

from __future__ import annotations

from dataclasses import dataclass

from core.domain.broker import BrokerProfile


@dataclass(frozen=True)
class ProfileValidationResult:
    valid: bool
    errors: tuple[str, ...]


class BrokerProfileValidator:
    def validate(self, profile: BrokerProfile) -> ProfileValidationResult:
        errors: list[str] = []

        if profile.tick_size <= 0:
            errors.append("tick_size must be positive")
        if profile.tick_value <= 0:
            errors.append("tick_value must be positive")
        if profile.digits < 0:
            errors.append("digits cannot be negative")
        if profile.freeze_level < 0:
            errors.append("freeze_level cannot be negative")
        if profile.minimum_stop_distance < 0:
            errors.append("minimum_stop_distance cannot be negative")
        if not profile.supported_operations:
            errors.append("supported_operations cannot be empty")
        if not profile.trading_sessions:
            errors.append("trading_sessions cannot be empty")
        if profile.spread < 0:
            errors.append("spread cannot be negative")
        if profile.commission_per_lot < 0:
            errors.append("commission_per_lot cannot be negative")
        if profile.slippage < 0:
            errors.append("slippage cannot be negative")

        return ProfileValidationResult(valid=len(errors) == 0, errors=tuple(errors))

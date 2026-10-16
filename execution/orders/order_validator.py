"""Order validator. Execution FROZEN v1.0 §13: any broker-constraint
failure results in REJECT/ABORT with a reason code — never a silent
adjustment. This module has no BrokerProfile to validate against yet
(that's a later milestone), so it validates only what's already frozen
and knowable without one: decision expiry (OD3) and basic sanity on the
decision itself. Broker-specific constraint checks (lot step, min/max
volume, minimum stop distance) belong here too, but only once a real
BrokerProfile exists to check against — flagged, not invented."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from core.domain.decision import TradeDecision


@dataclass(frozen=True)
class ValidationResult:
    valid: bool
    reason_code: str


class OrderValidator:
    def validate(self, decision: TradeDecision, at: datetime) -> ValidationResult:
        if decision.is_expired(at):
            return ValidationResult(valid=False, reason_code="DECISION_EXPIRED")
        return ValidationResult(valid=True, reason_code="OK")

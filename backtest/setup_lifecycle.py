"""Setup lifecycle orchestration. This did NOT exist in any prior
milestone — M4 and M5's own reviews explicitly flagged 'checking
expiry_at against current time, checking invalidation_level against
price' as unbuilt orchestration. This module builds exactly that, and
nothing more: no new Setup states, no new expiry duration, no altered
admission/confirmation behavior — it only evaluates the two already-
frozen conditions and drives the already-existing state transitions."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from core.domain.common import Direction, ensure_utc
from core.domain.setups import Setup, SetupState, validate_setup_transition


def evaluate_setup_lifecycle(setup: Setup, at: datetime, latest_close: Decimal) -> Setup:
    """Returns the setup unchanged, or a new instance transitioned to
    EXPIRED/INVALIDATED. Only operates on setups in a state where those
    transitions are legal (WAITING_FOR_RETEST, WAITING_FOR_CONFIRMATION)
    — terminal/CONFIRMED setups pass through untouched, since expiry and
    invalidation only apply to still-pending setups (Strategy FROZEN
    v1.0: a setup that reaches CONFIRMED has already passed structural
    confirmation and isn't subject to these checks anymore).

    `at` must be replay time, NEVER the wall clock — this function has
    no access to system time and never reads it."""

    at = ensure_utc(at)

    if setup.state not in (SetupState.WAITING_FOR_RETEST, SetupState.WAITING_FOR_CONFIRMATION):
        return setup

    # Invalidation is checked BEFORE expiry — a setup whose structural
    # premise has already failed shouldn't wait around for its timer to
    # also run out; the more specific, price-driven condition wins.
    if _is_invalidated(setup, latest_close):
        validate_setup_transition(setup.state, SetupState.INVALIDATED)
        return setup.model_copy(update={"state": SetupState.INVALIDATED})

    if at >= setup.expiry_at:
        validate_setup_transition(setup.state, SetupState.EXPIRED)
        return setup.model_copy(update={"state": SetupState.EXPIRED})

    return setup


def _is_invalidated(setup: Setup, latest_close: Decimal) -> bool:
    """Bullish: invalidated if price closes BELOW invalidation_level
    (the structural low the setup depends on holding). Bearish: the
    mirror — invalidated if price closes ABOVE it. Uses candle CLOSE,
    consistent with every other structural check in this codebase (BOS/
    CHoCH/displacement all key off close, never wick) — deliberately
    not treating a wick-only breach as invalidation."""

    if setup.direction == Direction.BULLISH:
        return latest_close < setup.invalidation_level
    return latest_close > setup.invalidation_level

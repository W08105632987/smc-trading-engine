"""Setup creation and admission. v5 I6 / final freeze:

  - pending cap (max_pending_setups, PROVISIONAL default 3) applies only
    to WAITING_FOR_RETEST and WAITING_FOR_CONFIRMATION — CONFIRMED is
    exempt (locked in the final strategy freeze).
  - Admission is score-based: when the cap would be exceeded, the
    LOWEST-scoring setup among {existing pending ∪ new candidate} is
    displaced/rejected.
  - Equal-score ties reject the newest candidate (deterministic
    tie-break, no second arbitrary parameter).
  - A displaced existing setup transitions to DISPLACED via the same
    validate_setup_transition() used everywhere else — never a
    hand-rolled state change.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from core.domain.setups import Setup, SetupState, validate_setup_transition


PENDING_CAP_STATES = frozenset({SetupState.WAITING_FOR_RETEST, SetupState.WAITING_FOR_CONFIRMATION})


@dataclass(frozen=True)
class AdmissionResult:
    admitted: bool
    displaced: Setup | None  # the existing setup that was displaced, if any


class SetupAdmissionEngine:
    """Concrete admission/priority logic. Deliberately separate from
    Setup creation itself — admission is a policy decision applied to an
    already-constructed candidate, not part of constructing it."""

    def __init__(self, max_pending_setups: int) -> None:
        self._max_pending = max_pending_setups

    def evaluate(self, all_existing_setups: Sequence[Setup], candidate: Setup) -> AdmissionResult:
        # CONFIRMED (and any terminal-state) setups are exempt from the
        # cap entirely — only count what's actually capped.
        pending = [s for s in all_existing_setups if s.state in PENDING_CAP_STATES]

        if len(pending) < self._max_pending:
            return AdmissionResult(admitted=True, displaced=None)

        pool = pending + [candidate]
        min_score = min(s.score for s in pool)
        tied_for_lowest = [s for s in pool if s.score == min_score]
        # Tie-break: reject newest. Ties resolve deterministically without
        # a second arbitrary parameter (v5 final freeze).
        to_reject = max(tied_for_lowest, key=lambda s: s.created_at)

        if to_reject.setup_id == candidate.setup_id:
            return AdmissionResult(admitted=False, displaced=None)

        # Confirm this is actually a legal transition before returning it
        # — never silently assume DISPLACED is reachable from wherever
        # the existing setup happens to be.
        validate_setup_transition(to_reject.state, SetupState.DISPLACED)
        return AdmissionResult(admitted=True, displaced=to_reject)


def apply_displacement(setup: Setup, displaced_by_setup_id: str) -> Setup:
    """Constructs the DISPLACED successor of an existing setup. Setup is
    immutable (frozen=True), so this returns a new instance rather than
    mutating — consistent with every other state transition in this
    codebase."""

    validate_setup_transition(setup.state, SetupState.DISPLACED)
    return setup.model_copy(
        update={"state": SetupState.DISPLACED, "displaced_by_setup_id": displaced_by_setup_id}
    )

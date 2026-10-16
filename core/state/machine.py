"""Generic deterministic state-machine infrastructure. This module knows
nothing about SMC strategy or execution specifics — it's the shared
mechanism that core/domain/setups.py's validate_setup_transition() and
core/domain/execution.py's VALID_ORDER_TRANSITIONS / 
VALID_PROTECTION_TRANSITIONS are built on the same pattern as, so every
state machine in this codebase is checked the same way."""

from __future__ import annotations

from typing import Generic, Hashable, Mapping, TypeVar

S = TypeVar("S", bound=Hashable)


class IllegalTransitionError(ValueError):
    """Raised whenever code attempts a transition not present in a
    machine's transition table. This is intentionally a ValueError
    subclass so it's caught by any code already handling validation
    errors, but distinguishable by type when that matters."""

    def __init__(self, from_state: S, to_state: S, machine_name: str) -> None:
        self.from_state = from_state
        self.to_state = to_state
        self.machine_name = machine_name
        super().__init__(
            f"illegal transition in {machine_name}: {from_state} -> {to_state}"
        )


class TransitionTable(Generic[S]):
    """Wraps a {state: frozenset(allowed_next_states)} mapping and
    enforces it. Terminal states are any state mapping to an empty
    frozenset — no special-casing needed beyond that."""

    def __init__(self, name: str, table: Mapping[S, frozenset[S]]) -> None:
        self.name = name
        self._table = dict(table)

    def is_terminal(self, state: S) -> bool:
        return len(self._table.get(state, frozenset())) == 0

    def allowed_next(self, state: S) -> frozenset[S]:
        return self._table.get(state, frozenset())

    def validate(self, from_state: S, to_state: S) -> None:
        if to_state not in self.allowed_next(from_state):
            raise IllegalTransitionError(from_state, to_state, self.name)

    def can_transition(self, from_state: S, to_state: S) -> bool:
        return to_state in self.allowed_next(from_state)

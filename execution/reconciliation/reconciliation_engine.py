"""Reconciliation engine. Execution FROZEN v1.0 §8: broker state is
authoritative for live/paper. No mismatch is EVER auto-corrected by
submitting a new trade — reconciliation only detects, logs, and blocks;
resolution (where automatic at all) is limited to syncing local state to
match a confirmed broker outcome, never inventing trading activity."""

from __future__ import annotations

from typing import Protocol, Sequence

from core.domain.execution import Position


class ReconciliationAdapter(Protocol):
    def get_open_positions(self) -> tuple[Position, ...]: ...


class MismatchType:
    LOCAL_MISSING_AT_BROKER = "LOCAL_MISSING_AT_BROKER"
    BROKER_MISSING_LOCALLY = "BROKER_MISSING_LOCALLY"
    QUANTITY_MISMATCH = "QUANTITY_MISMATCH"
    SL_MISMATCH = "SL_MISMATCH"
    TP_MISMATCH = "TP_MISMATCH"


class ReconciliationEngine:
    def find_mismatches(
        self, local_positions: Sequence[Position], adapter: ReconciliationAdapter
    ) -> list[str]:
        """Never auto-corrects — only reports. The caller decides what
        to do with each mismatch (block new entries, alert, etc.),
        exactly as Execution FROZEN v1.0 §8 requires."""

        broker_positions = {p.position_id: p for p in adapter.get_open_positions()}
        local_by_id = {p.position_id: p for p in local_positions}

        mismatches: list[str] = []

        for position_id, local in local_by_id.items():
            broker = broker_positions.get(position_id)
            if broker is None:
                mismatches.append(f"{MismatchType.LOCAL_MISSING_AT_BROKER}:{position_id}")
                continue
            if local.volume != broker.volume:
                mismatches.append(f"{MismatchType.QUANTITY_MISMATCH}:{position_id}")
            if local.stop_loss != broker.stop_loss:
                mismatches.append(f"{MismatchType.SL_MISMATCH}:{position_id}")
            if local.take_profit_1 != broker.take_profit_1:
                mismatches.append(f"{MismatchType.TP_MISMATCH}:{position_id}")

        for position_id in broker_positions:
            if position_id not in local_by_id:
                mismatches.append(f"{MismatchType.BROKER_MISSING_LOCALLY}:{position_id}")

        return mismatches

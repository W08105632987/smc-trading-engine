"""Zone engine: FVG and Order Block detection. Phase 2 §12-13.

FVG creation timestamp = candle 3's close (self-contained, zero extra
lag beyond that candle closing — v4 §F). Order Block creation timestamp
= its linked displacement's confirmed_at, since the OB's VALIDITY
depends on that displacement having occurred, even though the OB's
origin candle itself is earlier in time (already-known, historical data
by the time the displacement confirms it as significant)."""

from __future__ import annotations

from typing import Sequence

from core.domain.common import Direction
from core.domain.liquidity import DisplacementEvent, MSSEvent
from core.domain.market_data import Candle
from core.domain.zones import FVG, OrderBlock, Zone


class ZoneEngineImpl:
    """Concrete implementation of core.contracts.detectors.ZoneEngine."""

    def detect_fvgs(
        self,
        candles: Sequence[Candle],
        displacements_by_candle_id: dict[str, DisplacementEvent] | None = None,
    ) -> Sequence[FVG]:
        """Bullish: candle3.low > candle1.high. Bearish: candle3.high <
        candle1.low. Linked to the displacement event that created it
        'when possible' (Phase 2 §12) — matched by candle3 being the
        displacement's own candle, since that's the natural case where
        an FVG forms as part of a qualifying displacement move."""

        displacements_by_candle_id = displacements_by_candle_id or {}
        fvgs: list[FVG] = []
        for i in range(len(candles) - 2):
            c1, c2, c3 = candles[i], candles[i + 1], candles[i + 2]
            displacement = displacements_by_candle_id.get(c3.candle_id)

            if c3.low > c1.high:
                fvgs.append(
                    FVG(
                        zone_id=f"fvg:bullish:{c3.candle_id}",
                        direction=Direction.BULLISH,
                        upper=c3.low, lower=c1.high,
                        origin_candle_id=c1.candle_id,
                        creation_timestamp=c3.timestamp_close,
                        candle1_id=c1.candle_id, candle2_id=c2.candle_id, candle3_id=c3.candle_id,
                        linked_displacement_id=displacement.displacement_id if displacement else None,
                    )
                )
            elif c3.high < c1.low:
                fvgs.append(
                    FVG(
                        zone_id=f"fvg:bearish:{c3.candle_id}",
                        direction=Direction.BEARISH,
                        upper=c1.low, lower=c3.high,
                        origin_candle_id=c1.candle_id,
                        creation_timestamp=c3.timestamp_close,
                        candle1_id=c1.candle_id, candle2_id=c2.candle_id, candle3_id=c3.candle_id,
                        linked_displacement_id=displacement.displacement_id if displacement else None,
                    )
                )
        return fvgs

    def detect_order_blocks(
        self,
        candles: Sequence[Candle],
        mss_events: Sequence[MSSEvent],
        displacements_by_id: dict[str, DisplacementEvent],
        candle_index_by_id: Optional[dict[str, int]] = None,
    ) -> Sequence[OrderBlock]:
        """Bullish OB = final bearish candle before qualifying bullish
        displacement that follows/reacts from meaningful liquidity,
        produces valid displacement/MSS. Deliberately isolated from
        every other zone type (Phase 2 §13: 'among the most
        interpretation-sensitive SMC concepts') — only origin candles
        immediately preceding an MSS-confirmed displacement qualify,
        never 'every opposite candle before a large candle.'"""

        if candle_index_by_id is None:
            candle_index_by_id = {c.candle_id: i for i, c in enumerate(candles)}
        order_blocks: list[OrderBlock] = []

        for mss in mss_events:
            displacement = displacements_by_id.get(mss.displacement_id)
            if displacement is None:
                continue

            disp_index = candle_index_by_id.get(displacement.candle_id)
            if disp_index is None or disp_index == 0:
                continue  # no candle before the displacement to serve as origin

            origin_candle = candles[disp_index - 1]
            origin_is_bearish = origin_candle.close < origin_candle.open
            origin_is_bullish = origin_candle.close > origin_candle.open

            if mss.direction == Direction.BULLISH and not origin_is_bearish:
                continue  # bullish OB requires a bearish origin candle
            if mss.direction == Direction.BEARISH and not origin_is_bullish:
                continue  # bearish OB requires a bullish origin candle

            order_blocks.append(
                OrderBlock(
                    zone_id=f"ob:{mss.mss_id}",
                    direction=mss.direction,
                    upper=origin_candle.high, lower=origin_candle.low,
                    origin_candle_id=origin_candle.candle_id,
                    creation_timestamp=displacement.confirmed_at,
                    linked_displacement_id=displacement.displacement_id,
                    linked_mss_id=mss.mss_id,
                    origin_candle_direction=(
                        Direction.BEARISH if origin_is_bearish else Direction.BULLISH
                    ),
                )
            )

        return order_blocks

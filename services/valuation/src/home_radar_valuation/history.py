from __future__ import annotations

from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal

from home_radar_valuation.domain import PriceHistory


def build_price_history(
    current_ask: Decimal,
    first_seen_at: datetime,
    snapshot_prices: list[tuple[datetime, Decimal]],
    price_cut_dates: list[datetime],
    relisting_dates: list[datetime],
    as_of: datetime,
) -> PriceHistory:
    valid_snapshots = sorted(
        (item for item in snapshot_prices if item[0] <= as_of), key=lambda item: item[0]
    )
    original_ask = valid_snapshots[0][1] if valid_snapshots else current_ask
    absolute_reduction = max(Decimal("0"), original_ask - current_ask)
    percentage_reduction = absolute_reduction / original_ask if original_ask > 0 else Decimal("0")
    valid_cuts = sorted(value for value in price_cut_dates if value <= as_of)
    last_cut = valid_cuts[-1] if valid_cuts else None
    return PriceHistory(
        original_ask=original_ask,
        current_ask=current_ask,
        absolute_reduction=absolute_reduction.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP),
        percentage_reduction=percentage_reduction.quantize(
            Decimal("0.000001"), rounding=ROUND_HALF_UP
        ),
        price_cut_count=len(valid_cuts),
        days_since_last_cut=(as_of - last_cut).days if last_cut is not None else None,
        days_on_market=max(0, (as_of - first_seen_at).days),
        relisting_flag=any(value <= as_of for value in relisting_dates),
    )

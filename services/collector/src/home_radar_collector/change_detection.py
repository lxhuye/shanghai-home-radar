from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from home_radar_models.enums import ListingEventType


@dataclass(frozen=True)
class DetectedChange:
    event_type: ListingEventType
    previous_value: str | None = None
    current_value: str | None = None


def detect_observation_changes(
    *,
    is_new: bool,
    previous_price: Decimal | None,
    current_price: Decimal,
) -> list[DetectedChange]:
    if is_new:
        return [DetectedChange(ListingEventType.NEW, current_value=str(current_price))]

    changes: list[DetectedChange] = []
    if previous_price is not None and current_price < previous_price:
        changes.append(
            DetectedChange(ListingEventType.PRICE_CUT, str(previous_price), str(current_price))
        )
    elif previous_price is not None and current_price > previous_price:
        changes.append(
            DetectedChange(ListingEventType.PRICE_INCREASE, str(previous_price), str(current_price))
        )

    return changes

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from home_radar_models.enums import ListingEventType, ListingStatus

from home_radar_market.domain import (
    BaselineSliceKey,
    ObservationRecord,
    record_matches_key,
)
from home_radar_market.statistics import percentile


@dataclass(frozen=True)
class ListingMetrics:
    active_inventory: int
    new_listings: int
    price_cut_count: int
    price_cut_ratio: Decimal | None
    median_initial_ask: Decimal | None
    median_current_ask: Decimal | None
    median_price_cut_pct: Decimal | None
    median_days_on_market: Decimal | None
    relisting_rate: Decimal | None
    missing_candidate_count: int
    inactive_count: int
    ask_price_change_30d: Decimal | None
    ask_price_change_90d: Decimal | None
    inventory_change_30d: Decimal | None
    inventory_change_90d: Decimal | None
    listing_exits: int


def calculate_listing_metrics(
    all_records: list[ObservationRecord],
    key: BaselineSliceKey,
    start_at: datetime,
    end_at: datetime,
) -> ListingMetrics:
    scoped = [record for record in all_records if record_matches_key(record, key)]
    window = [record for record in scoped if start_at <= record.observed_at < end_at]
    current = _trusted_state(scoped, end_at)
    active = [record for record in current if _status(record) == ListingStatus.ACTIVE.value]
    missing = [
        record
        for record in current
        if record.coverage_complete and _status(record) == ListingStatus.MISSING_CANDIDATE.value
    ]
    inactive = [
        record
        for record in current
        if record.coverage_complete and _status(record) == ListingStatus.INACTIVE.value
    ]
    cohort = {record.entity_key for record in window}
    by_entity: dict[str, list[ObservationRecord]] = {}
    for record in scoped:
        by_entity.setdefault(record.entity_key, []).append(record)
    for history in by_entity.values():
        history.sort(key=lambda item: (item.observed_at, item.created_at, str(item.id)))

    cut_entities: set[str] = set()
    relisted_entities: set[str] = set()
    new_entities: set[str] = set()
    initial_prices: list[Decimal] = []
    current_prices: list[Decimal] = []
    cut_percentages: list[Decimal] = []
    dom_days: list[Decimal] = []
    for record in window:
        event_types = _event_types(record)
        if ListingEventType.PRICE_CUT.value in event_types:
            cut_entities.add(record.entity_key)
        if ListingEventType.RELISTED.value in event_types:
            relisted_entities.add(record.entity_key)
        if ListingEventType.NEW.value in event_types or _first_seen(record) >= start_at:
            new_entities.add(record.entity_key)
    for entity_key, history in by_entity.items():
        before_cutoff = [record for record in history if record.observed_at < end_at]
        if not before_cutoff:
            continue
        first = before_cutoff[0]
        last = before_cutoff[-1]
        if first.total_price is not None:
            initial_prices.append(first.total_price)
        if last.total_price is not None:
            current_prices.append(last.total_price)
        prices = [record.total_price for record in before_cutoff if record.total_price is not None]
        if any(
            current_price < previous
            for previous, current_price in zip(prices, prices[1:], strict=False)
        ):
            cut_entities.add(entity_key)
        if first.total_price is not None and last.total_price is not None and first.total_price > 0:
            cut_percentages.append(
                max(Decimal(), (first.total_price - last.total_price) / first.total_price)
            )
    for record in active:
        dom_days.append(
            max(
                Decimal(),
                Decimal(str((end_at - _first_seen(record)).total_seconds() / 86_400)),
            )
        )

    price_cut_count = len(cut_entities & cohort)
    relisting_denominator = len(new_entities | relisted_entities)
    return ListingMetrics(
        active_inventory=len(active),
        new_listings=len(new_entities),
        price_cut_count=price_cut_count,
        price_cut_ratio=(Decimal(price_cut_count) / Decimal(len(cohort))) if cohort else None,
        median_initial_ask=percentile(initial_prices, Decimal("0.5")),
        median_current_ask=percentile(current_prices, Decimal("0.5")),
        median_price_cut_pct=percentile(cut_percentages, Decimal("0.5")),
        median_days_on_market=percentile(dom_days, Decimal("0.5")),
        relisting_rate=(
            Decimal(len(relisted_entities)) / Decimal(relisting_denominator)
            if relisting_denominator
            else None
        ),
        missing_candidate_count=len(missing),
        inactive_count=len(inactive),
        ask_price_change_30d=_ask_change(scoped, end_at, 30),
        ask_price_change_90d=_ask_change(scoped, end_at, 90),
        inventory_change_30d=_inventory_change(scoped, end_at, 30),
        inventory_change_90d=_inventory_change(scoped, end_at, 90),
        listing_exits=len(missing) + len(inactive),
    )


def _trusted_state(records: list[ObservationRecord], cutoff: datetime) -> list[ObservationRecord]:
    before = [record for record in records if record.observed_at < cutoff]
    by_entity: dict[str, list[ObservationRecord]] = {}
    for record in before:
        by_entity.setdefault(record.entity_key, []).append(record)
    result: list[ObservationRecord] = []
    for history in by_entity.values():
        history.sort(
            key=lambda record: (record.observed_at, record.created_at, str(record.id)),
            reverse=True,
        )
        for record in history:
            status = _status(record)
            if status == ListingStatus.ACTIVE.value or record.coverage_complete:
                result.append(record)
                break
    return result


def _ask_change(
    records: list[ObservationRecord], cutoff: datetime, lookback_days: int
) -> Decimal | None:
    current = [
        record.total_price for record in _trusted_state(records, cutoff) if record.total_price
    ]
    previous = [
        record.total_price
        for record in _trusted_state(records, cutoff - timedelta(days=lookback_days))
        if record.total_price
    ]
    current_median = percentile(current, Decimal("0.5"))
    previous_median = percentile(previous, Decimal("0.5"))
    if current_median is None or previous_median is None or previous_median == 0:
        return None
    return current_median / previous_median - Decimal("1")


def _inventory_change(
    records: list[ObservationRecord], cutoff: datetime, lookback_days: int
) -> Decimal | None:
    current = sum(
        _status(record) == ListingStatus.ACTIVE.value for record in _trusted_state(records, cutoff)
    )
    previous = sum(
        _status(record) == ListingStatus.ACTIVE.value
        for record in _trusted_state(records, cutoff - timedelta(days=lookback_days))
    )
    if previous == 0:
        return None
    return Decimal(current) / Decimal(previous) - Decimal("1")


def _status(record: ObservationRecord) -> str | None:
    value = record.metadata.get("listing_status")
    return value if isinstance(value, str) else None


def _event_types(record: ObservationRecord) -> set[str]:
    value = record.metadata.get("event_types", [])
    if not isinstance(value, list):
        return set()
    return {item for item in value if isinstance(item, str)}


def _first_seen(record: ObservationRecord) -> datetime:
    value = record.metadata.get("first_seen_at")
    if isinstance(value, str):
        parsed = datetime.fromisoformat(value)
        if parsed.tzinfo is not None:
            return parsed.astimezone(UTC)
    return record.observed_at

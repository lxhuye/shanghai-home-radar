from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any

from home_radar_models.market import MarketObservation


@dataclass(frozen=True)
class ObservationRecord:
    id: uuid.UUID
    observation_type: str
    source: str
    source_record_id: str
    listing_id: uuid.UUID | None
    district: str | None
    submarket: str | None
    community: str | None
    area_bucket: str | None
    layout: str | None
    total_price: Decimal | None
    unit_price: Decimal | None
    monthly_rent: Decimal | None
    rent_per_sqm: Decimal | None
    index_value: Decimal | None
    observed_at: datetime
    created_at: datetime
    coverage_complete: bool
    metadata: dict[str, Any]

    @classmethod
    def from_model(cls, value: MarketObservation) -> ObservationRecord:
        return cls(
            id=value.id,
            observation_type=value.observation_type,
            source=value.source,
            source_record_id=value.source_record_id,
            listing_id=value.listing_id,
            district=value.district,
            submarket=value.submarket,
            community=value.community,
            area_bucket=value.area_bucket,
            layout=value.layout,
            total_price=value.total_price,
            unit_price=value.unit_price,
            monthly_rent=value.monthly_rent,
            rent_per_sqm=value.rent_per_sqm,
            index_value=value.index_value,
            observed_at=value.observed_at,
            created_at=value.created_at,
            coverage_complete=value.coverage_complete,
            metadata=value.observation_metadata,
        )

    @property
    def entity_key(self) -> str:
        if self.listing_id is not None:
            return f"listing:{self.listing_id}"
        return f"source:{self.observation_type}:{self.source}:{self.source_record_id}"


@dataclass(frozen=True)
class BaselineSliceKey:
    observation_type: str
    window_days: int
    level: str
    district: str | None
    submarket: str | None
    community: str | None
    area_bucket: str | None
    layout: str | None


def slice_keys(record: ObservationRecord, window_days: int) -> set[BaselineSliceKey]:
    geographies: list[tuple[str, str | None, str | None, str | None]] = [
        ("shanghai", None, None, None)
    ]
    if record.district is not None:
        geographies.append(("district", record.district, None, None))
    if record.district is not None and record.submarket is not None:
        geographies.append(("submarket", record.district, record.submarket, None))
    if (
        record.district is not None
        and record.submarket is not None
        and record.community is not None
    ):
        geographies.append(("community", record.district, record.submarket, record.community))

    segments: set[tuple[str | None, str | None]] = {(None, None)}
    if record.area_bucket is not None:
        segments.add((record.area_bucket, None))
    if record.layout is not None:
        segments.add((None, record.layout))
    if record.area_bucket is not None and record.layout is not None:
        segments.add((record.area_bucket, record.layout))
    return {
        BaselineSliceKey(
            observation_type=record.observation_type,
            window_days=window_days,
            level=level,
            district=district,
            submarket=submarket,
            community=community,
            area_bucket=area_bucket,
            layout=layout,
        )
        for level, district, submarket, community in geographies
        for area_bucket, layout in segments
    }


def record_matches_key(record: ObservationRecord, key: BaselineSliceKey) -> bool:
    if record.observation_type != key.observation_type:
        return False
    if key.district is not None and record.district != key.district:
        return False
    if key.submarket is not None and record.submarket != key.submarket:
        return False
    if key.community is not None and record.community != key.community:
        return False
    if key.area_bucket is not None and record.area_bucket != key.area_bucket:
        return False
    return key.layout is None or record.layout == key.layout


def latest_per_entity(records: list[ObservationRecord]) -> list[ObservationRecord]:
    latest: dict[str, ObservationRecord] = {}
    for record in records:
        existing = latest.get(record.entity_key)
        if existing is None or (
            record.observed_at,
            record.created_at,
            str(record.id),
        ) > (existing.observed_at, existing.created_at, str(existing.id)):
            latest[record.entity_key] = record
    return sorted(latest.values(), key=lambda record: record.entity_key)

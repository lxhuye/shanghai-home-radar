from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Any

from home_radar_models.collection import CrawlRun
from home_radar_models.enums import (
    CrawlCompleteness,
    CrawlRunStatus,
    DataMode,
    MarketObservationType,
)
from home_radar_models.listing import Listing, ListingEvent, ListingSnapshot
from home_radar_models.market import MarketObservation
from sqlalchemy import select
from sqlalchemy.orm import Session

from home_radar_market.config import MarketBaselineConfig


@dataclass(frozen=True)
class ObservationInput:
    observation_type: MarketObservationType
    data_mode: DataMode
    source: str
    source_record_id: str
    observed_at: datetime
    district: str | None = None
    submarket: str | None = None
    community: str | None = None
    area_sqm: Decimal | None = None
    bedrooms: int | None = None
    total_price: Decimal | None = None
    unit_price: Decimal | None = None
    monthly_rent: Decimal | None = None
    rent_per_sqm: Decimal | None = None
    index_value: Decimal | None = None
    source_confidence: Decimal = Decimal("1")
    coverage_complete: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)


def ingest_observation(
    session: Session, value: ObservationInput, config: MarketBaselineConfig
) -> tuple[MarketObservation, bool]:
    if value.observed_at.tzinfo is None:
        raise ValueError("observed_at must be timezone-aware")
    existing = session.scalar(
        select(MarketObservation).where(
            MarketObservation.data_mode == value.data_mode.value,
            MarketObservation.observation_type == value.observation_type.value,
            MarketObservation.source == value.source,
            MarketObservation.source_record_id == value.source_record_id,
            MarketObservation.observed_at == value.observed_at,
        )
    )
    if existing is not None:
        return existing, False
    area_bucket = config.area_bucket_for(value.area_sqm) if value.area_sqm is not None else None
    layout = config.layout_for(value.bedrooms)
    observation = MarketObservation(
        observation_type=value.observation_type.value,
        data_mode=value.data_mode.value,
        source=value.source,
        source_record_id=value.source_record_id,
        observed_at=value.observed_at,
        district=value.district,
        submarket=value.submarket,
        community=value.community,
        area_sqm=value.area_sqm,
        area_bucket=area_bucket,
        bedrooms=value.bedrooms,
        layout=layout,
        total_price=value.total_price,
        unit_price=value.unit_price,
        monthly_rent=value.monthly_rent,
        rent_per_sqm=value.rent_per_sqm,
        index_value=value.index_value,
        source_confidence=value.source_confidence,
        coverage_complete=value.coverage_complete,
        observation_metadata=value.metadata,
    )
    session.add(observation)
    return observation, True


def import_listing_observations(
    session: Session, data_mode: DataMode, config: MarketBaselineConfig
) -> int:
    """Project only finalized successful crawl snapshots into immutable observations."""
    rows = session.execute(
        select(ListingSnapshot, Listing, CrawlRun)
        .join(Listing, Listing.id == ListingSnapshot.listing_id)
        .join(CrawlRun, CrawlRun.id == ListingSnapshot.crawl_run_id)
        .where(
            CrawlRun.status == CrawlRunStatus.SUCCEEDED.value,
            CrawlRun.data_mode == data_mode.value,
        )
        .order_by(ListingSnapshot.snapshot_at)
    ).all()
    snapshot_ids = [snapshot.id for snapshot, _, _ in rows]
    existing_ids = set(
        session.scalars(
            select(MarketObservation.listing_snapshot_id).where(
                MarketObservation.listing_snapshot_id.in_(snapshot_ids),
                MarketObservation.data_mode == data_mode.value,
            )
        )
    )
    event_rows = session.execute(
        select(ListingEvent.listing_id, ListingEvent.crawl_run_id, ListingEvent.event_type).where(
            ListingEvent.crawl_run_id.is_not(None)
        )
    ).all()
    events: dict[tuple[uuid.UUID, uuid.UUID], list[str]] = {}
    for listing_id, run_id, event_type in event_rows:
        assert run_id is not None
        events.setdefault((listing_id, run_id), []).append(event_type)

    imported = 0
    for snapshot, listing, run in rows:
        if snapshot.id in existing_ids:
            continue
        observation = MarketObservation(
            observation_type=MarketObservationType.LISTING.value,
            data_mode=run.data_mode,
            source=listing.source,
            source_record_id=str(snapshot.id),
            listing_id=listing.id,
            listing_snapshot_id=snapshot.id,
            crawl_run_id=run.id,
            district=listing.district,
            submarket=listing.submarket,
            community=listing.community,
            area_sqm=listing.area_sqm,
            area_bucket=config.area_bucket_for(listing.area_sqm),
            bedrooms=listing.bedrooms,
            layout=config.layout_for(listing.bedrooms),
            total_price=snapshot.total_price,
            unit_price=snapshot.unit_price,
            observed_at=snapshot.snapshot_at,
            longitude=listing.longitude,
            latitude=listing.latitude,
            coverage_complete=run.completeness == CrawlCompleteness.COMPLETE.value,
            observation_metadata={
                "listing_status": snapshot.status,
                "first_seen_at": listing.first_seen_at.isoformat(),
                "floor": listing.floor,
                "total_floors": listing.total_floors,
                "orientation": listing.orientation,
                "year_built": listing.year_built,
                "elevator": listing.elevator,
                "building_type": listing.building_type,
                "event_types": events.get((listing.id, run.id), []),
                "crawl_scope": run.scope_key,
                "crawl_completeness": run.completeness,
            },
        )
        session.add(observation)
        imported += 1
    session.flush()
    return imported

from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from geoalchemy2.elements import WKTElement
from home_radar_models.collection import CrawlRun, RawSourceRecord
from home_radar_models.enums import (
    ListingEventType,
    ListingStatus,
    NormalizationStatus,
    PresenceState,
)
from home_radar_models.listing import Listing, ListingEvent, ListingPresence, ListingSnapshot
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from home_radar_collector.change_detection import detect_observation_changes
from home_radar_collector.contracts import ListingObservation


@dataclass(frozen=True)
class IngestionResult:
    listing: Listing
    snapshot_created: bool


@dataclass(frozen=True)
class ReconciliationResult:
    missing_candidates: int = 0
    inactivated: int = 0

    @property
    def changed(self) -> int:
        return self.missing_candidates + self.inactivated


class ListingRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def try_scope_lock(self, *, source: str, scope_key: str) -> bool:
        digest = hashlib.blake2b(f"{source}:{scope_key}".encode(), digest_size=8).digest()
        lock_key = int.from_bytes(digest, byteorder="big", signed=True)
        result = self.session.execute(
            text("SELECT pg_try_advisory_xact_lock(:lock_key)"), {"lock_key": lock_key}
        )
        return bool(result.scalar_one())

    def add_raw_record(
        self,
        *,
        crawl_run_id: uuid.UUID,
        observed_at: datetime,
        payload: dict[str, object],
        source_record_id: str | None,
        normalization_status: NormalizationStatus,
        normalization_error: str | None,
        listing_id: uuid.UUID | None = None,
    ) -> RawSourceRecord:
        record = RawSourceRecord(
            crawl_run_id=crawl_run_id,
            listing_id=listing_id,
            observed_at=observed_at,
            payload=payload,
            source_record_id=source_record_id,
            normalization_status=normalization_status.value,
            normalization_error=normalization_error,
        )
        self.session.add(record)
        return record

    def ingest_observation(
        self,
        *,
        crawl_run_id: uuid.UUID,
        observation: ListingObservation,
        raw_payload: dict[str, object],
    ) -> IngestionResult:
        listing = self.session.scalar(
            select(Listing)
            .where(
                Listing.source == observation.source,
                Listing.source_listing_id == observation.source_listing_id,
            )
            .with_for_update()
        )
        is_new = listing is None
        previous_price = listing.total_price if listing else None
        is_current = listing is None or observation.observed_at >= listing.last_seen_at

        if listing is None:
            listing = self._new_listing(observation)
            self.session.add(listing)
        elif observation.observed_at < listing.first_seen_at:
            listing.first_seen_at = observation.observed_at

        existing_snapshot = self.session.scalar(
            select(ListingSnapshot).where(
                ListingSnapshot.crawl_run_id == crawl_run_id,
                ListingSnapshot.listing_id == listing.id,
            )
        )
        if existing_snapshot is not None:
            return IngestionResult(listing=listing, snapshot_created=False)

        same_observation = self.session.scalar(
            select(ListingSnapshot.id).where(
                ListingSnapshot.listing_id == listing.id,
                ListingSnapshot.snapshot_at == observation.observed_at,
            )
        )
        snapshot_created = same_observation is None
        if snapshot_created:
            self.session.add(
                ListingSnapshot(
                    listing=listing,
                    crawl_run_id=crawl_run_id,
                    snapshot_at=observation.observed_at,
                    total_price=observation.total_price,
                    unit_price=self._unit_price(observation),
                    status=observation.status.value,
                    raw_payload=raw_payload,
                )
            )

        if is_current:
            self._apply_current_state(listing, observation)
            if snapshot_created:
                for change in detect_observation_changes(
                    is_new=is_new,
                    previous_price=previous_price,
                    current_price=observation.total_price,
                ):
                    self._add_event_once(
                        listing=listing,
                        crawl_run_id=crawl_run_id,
                        event_type=change.event_type,
                        occurred_at=observation.observed_at,
                        previous_value=change.previous_value,
                        current_value=change.current_value,
                    )
        self.session.flush()
        return IngestionResult(listing=listing, snapshot_created=snapshot_created)

    def mark_observed_presence(
        self,
        *,
        crawl_run_id: uuid.UUID,
        listing: Listing,
        source: str,
        scope_key: str,
        observed_at: datetime,
        complete: bool,
    ) -> bool:
        identity = {"listing_id": listing.id, "source": source, "scope_key": scope_key}
        presence = self.session.get(ListingPresence, identity)
        was_absent = presence is not None and presence.state != PresenceState.ACTIVE.value
        if presence is None:
            presence = ListingPresence(**identity)
            self.session.add(presence)

        presence.consecutive_complete_misses = 0
        if complete:
            presence.last_seen_complete_run_at = observed_at
        # Presence is derived from whether the listing was observed, not a source status label.
        # This keeps a partial run from driving a lifecycle transition to inactive.
        presence.state = PresenceState.ACTIVE.value

        if was_absent:
            self._add_event_once(
                listing=listing,
                crawl_run_id=crawl_run_id,
                event_type=ListingEventType.RELISTED,
                occurred_at=observed_at,
                previous_value=ListingStatus.INACTIVE.value,
                current_value=ListingStatus.ACTIVE.value,
                details={"scope_key": scope_key},
            )
        self.session.flush()
        self._refresh_listing_status(listing)
        return was_absent

    def reconcile_complete_scope(
        self,
        *,
        crawl_run_id: uuid.UUID,
        source: str,
        scope_key: str,
        observed_at: datetime,
        final_after_runs: int,
        seen_listing_ids: set[uuid.UUID],
    ) -> ReconciliationResult:
        query = select(ListingPresence).where(
            ListingPresence.source == source,
            ListingPresence.scope_key == scope_key,
            ListingPresence.state.in_(
                [PresenceState.ACTIVE.value, PresenceState.MISSING_CANDIDATE.value]
            ),
        )
        if seen_listing_ids:
            query = query.where(ListingPresence.listing_id.not_in(seen_listing_ids))
        candidates = self.session.scalars(query.with_for_update()).all()

        missing_candidates = 0
        inactivated = 0
        for presence in candidates:
            presence.consecutive_complete_misses += 1
            previous_state = presence.state
            if presence.consecutive_complete_misses >= final_after_runs:
                presence.state = PresenceState.INACTIVE.value
                event_type = ListingEventType.INACTIVATED
                inactivated += 1
            elif presence.state == PresenceState.ACTIVE.value:
                presence.state = PresenceState.MISSING_CANDIDATE.value
                event_type = ListingEventType.MISSING_CANDIDATE
                missing_candidates += 1
            else:
                continue

            listing = presence.listing
            self._append_reconciliation_snapshot(
                listing=listing,
                crawl_run_id=crawl_run_id,
                observed_at=observed_at,
                status=presence.state,
                scope_key=scope_key,
            )
            self._add_event_once(
                listing=listing,
                crawl_run_id=crawl_run_id,
                event_type=event_type,
                occurred_at=observed_at,
                previous_value=previous_state,
                current_value=presence.state,
                details={
                    "scope_key": scope_key,
                    "consecutive_complete_misses": presence.consecutive_complete_misses,
                },
            )
            self.session.flush()
            self._refresh_listing_status(listing)
        return ReconciliationResult(
            missing_candidates=missing_candidates,
            inactivated=inactivated,
        )

    def get_run(self, run_id: uuid.UUID) -> CrawlRun | None:
        return self.session.get(CrawlRun, run_id)

    def latest_complete_run(
        self, *, source: str, scope_key: str, data_mode: str
    ) -> CrawlRun | None:
        return self.session.scalar(
            select(CrawlRun)
            .where(
                CrawlRun.source == source,
                CrawlRun.scope_key == scope_key,
                CrawlRun.data_mode == data_mode,
                CrawlRun.status == "succeeded",
                CrawlRun.completeness == "complete",
            )
            .order_by(CrawlRun.started_at.desc(), CrawlRun.id.desc())
            .limit(1)
        )

    def _append_reconciliation_snapshot(
        self,
        *,
        listing: Listing,
        crawl_run_id: uuid.UUID,
        observed_at: datetime,
        status: str,
        scope_key: str,
    ) -> None:
        existing = self.session.scalar(
            select(ListingSnapshot.id).where(
                ListingSnapshot.crawl_run_id == crawl_run_id,
                ListingSnapshot.listing_id == listing.id,
            )
        )
        if existing is None:
            self.session.add(
                ListingSnapshot(
                    listing=listing,
                    crawl_run_id=crawl_run_id,
                    snapshot_at=observed_at,
                    total_price=listing.total_price,
                    unit_price=listing.unit_price,
                    status=status,
                    raw_payload={
                        "reason": "not_seen_in_complete_scope",
                        "scope_key": scope_key,
                    },
                )
            )

    def _add_event_once(
        self,
        *,
        listing: Listing,
        crawl_run_id: uuid.UUID,
        event_type: ListingEventType,
        occurred_at: datetime,
        previous_value: str | None = None,
        current_value: str | None = None,
        details: dict[str, object] | None = None,
    ) -> None:
        existing = self.session.scalar(
            select(ListingEvent.id).where(
                ListingEvent.crawl_run_id == crawl_run_id,
                ListingEvent.listing_id == listing.id,
                ListingEvent.event_type == event_type.value,
            )
        )
        if existing is None:
            self.session.add(
                ListingEvent(
                    listing=listing,
                    crawl_run_id=crawl_run_id,
                    event_type=event_type.value,
                    occurred_at=occurred_at,
                    previous_value=previous_value,
                    current_value=current_value,
                    details=details or {},
                )
            )

    def _refresh_listing_status(self, listing: Listing) -> None:
        states = set(
            self.session.scalars(
                select(ListingPresence.state).where(ListingPresence.listing_id == listing.id)
            ).all()
        )
        if PresenceState.ACTIVE.value in states:
            listing.status = ListingStatus.ACTIVE.value
        elif PresenceState.MISSING_CANDIDATE.value in states:
            listing.status = ListingStatus.MISSING_CANDIDATE.value
        elif states and listing.status != ListingStatus.SOLD.value:
            listing.status = ListingStatus.INACTIVE.value

    @staticmethod
    def _new_listing(observation: ListingObservation) -> Listing:
        return Listing(
            id=uuid.uuid4(),
            source=observation.source,
            source_listing_id=observation.source_listing_id,
            source_url=str(observation.source_url),
            district=observation.district,
            submarket=observation.submarket,
            community=observation.community,
            total_price=observation.total_price,
            unit_price=ListingRepository._unit_price(observation),
            area_sqm=observation.area_sqm,
            first_seen_at=observation.observed_at,
            last_seen_at=observation.observed_at,
            status=ListingStatus.ACTIVE.value,
        )

    @staticmethod
    def _unit_price(observation: ListingObservation) -> Decimal:
        if observation.unit_price is None:
            raise ValueError("unit price was not derived")
        return observation.unit_price

    @staticmethod
    def _apply_current_state(listing: Listing, observation: ListingObservation) -> None:
        listing.source_url = str(observation.source_url)
        listing.district = observation.district
        listing.submarket = observation.submarket
        listing.community = observation.community
        listing.longitude = observation.longitude
        listing.latitude = observation.latitude
        listing.coordinates = (
            WKTElement(f"POINT({observation.longitude} {observation.latitude})", srid=4326)
            if observation.longitude is not None and observation.latitude is not None
            else None
        )
        listing.total_price = observation.total_price
        listing.unit_price = ListingRepository._unit_price(observation)
        listing.area_sqm = observation.area_sqm
        listing.bedrooms = observation.bedrooms
        listing.living_rooms = observation.living_rooms
        listing.floor = observation.floor
        listing.total_floors = observation.total_floors
        listing.orientation = observation.orientation
        listing.year_built = observation.year_built
        listing.elevator = observation.elevator
        listing.building_type = observation.building_type
        listing.last_seen_at = max(listing.last_seen_at, observation.observed_at)

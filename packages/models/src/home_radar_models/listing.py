from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Any

from geoalchemy2 import Geometry
from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from home_radar_models.base import Base, TimestampMixin
from home_radar_models.enums import ListingEventType, ListingStatus, PresenceState

if TYPE_CHECKING:
    from home_radar_models.collection import CrawlRun

JsonType = JSON().with_variant(JSONB(), "postgresql")


class Listing(TimestampMixin, Base):
    __tablename__ = "listing"
    __table_args__ = (
        UniqueConstraint("source", "source_listing_id", name="uq_listing_source_external_id"),
        CheckConstraint("area_sqm > 0", name="positive_area"),
        CheckConstraint("total_price >= 0", name="nonnegative_total_price"),
        Index("ix_listing_market_active", "district", "submarket", "status"),
        Index("ix_listing_price_area", "total_price", "area_sqm"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    source: Mapped[str] = mapped_column(String(50), nullable=False)
    source_listing_id: Mapped[str] = mapped_column(String(255), nullable=False)
    source_url: Mapped[str] = mapped_column(Text, nullable=False)

    district: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    submarket: Mapped[str] = mapped_column(String(120), nullable=False, index=True)
    community: Mapped[str] = mapped_column(String(160), nullable=False, index=True)
    longitude: Mapped[Decimal | None] = mapped_column(Numeric(10, 7))
    latitude: Mapped[Decimal | None] = mapped_column(Numeric(10, 7))
    coordinates: Mapped[Any | None] = mapped_column(
        Geometry(geometry_type="POINT", srid=4326, spatial_index=True)
    )

    total_price: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    unit_price: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    area_sqm: Mapped[Decimal] = mapped_column(Numeric(8, 2), nullable=False)
    bedrooms: Mapped[int | None] = mapped_column(Integer)
    living_rooms: Mapped[int | None] = mapped_column(Integer)
    floor: Mapped[str | None] = mapped_column(String(80))
    total_floors: Mapped[int | None] = mapped_column(Integer)
    orientation: Mapped[str | None] = mapped_column(String(80))
    year_built: Mapped[int | None] = mapped_column(Integer)
    elevator: Mapped[bool | None] = mapped_column(Boolean)
    building_type: Mapped[str | None] = mapped_column(String(80))

    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default=ListingStatus.ACTIVE.value, index=True
    )
    snapshots: Mapped[list[ListingSnapshot]] = relationship(
        back_populates="listing",
        order_by="ListingSnapshot.snapshot_at",
        passive_deletes="all",
    )
    events: Mapped[list[ListingEvent]] = relationship(
        back_populates="listing",
        order_by="ListingEvent.occurred_at",
        passive_deletes="all",
    )
    presence: Mapped[list[ListingPresence]] = relationship(
        back_populates="listing", passive_deletes="all"
    )


class ListingSnapshot(Base):
    __tablename__ = "listing_snapshot"
    __table_args__ = (
        UniqueConstraint("listing_id", "snapshot_at", name="uq_snapshot_listing_time"),
        UniqueConstraint("crawl_run_id", "listing_id", name="uq_snapshot_crawl_run_listing"),
        Index("ix_snapshot_listing_time_desc", "listing_id", "snapshot_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    listing_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("listing.id", ondelete="RESTRICT"), nullable=False
    )
    crawl_run_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("crawl_run.id", ondelete="RESTRICT")
    )
    snapshot_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    total_price: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    unit_price: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    raw_payload: Mapped[dict[str, Any]] = mapped_column(JsonType, nullable=False)

    listing: Mapped[Listing] = relationship(back_populates="snapshots")
    crawl_run: Mapped[CrawlRun | None] = relationship(back_populates="snapshots")


class ListingEvent(Base):
    __tablename__ = "listing_event"
    __table_args__ = (Index("ix_listing_event_occurred", "event_type", "occurred_at"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    listing_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("listing.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    crawl_run_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("crawl_run.id", ondelete="RESTRICT"), index=True
    )
    event_type: Mapped[str] = mapped_column(String(40), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    previous_value: Mapped[str | None] = mapped_column(Text)
    current_value: Mapped[str | None] = mapped_column(Text)
    details: Mapped[dict[str, Any]] = mapped_column(JsonType, nullable=False, default=dict)

    listing: Mapped[Listing] = relationship(back_populates="events")
    crawl_run: Mapped[CrawlRun | None] = relationship(back_populates="events")

    @property
    def kind(self) -> ListingEventType:
        return ListingEventType(self.event_type)


class ListingPresence(TimestampMixin, Base):
    __tablename__ = "listing_presence"
    __table_args__ = (
        CheckConstraint("consecutive_complete_misses >= 0", name="nonnegative_complete_misses"),
        Index("ix_listing_presence_scope_state", "source", "scope_key", "state"),
    )

    listing_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("listing.id", ondelete="RESTRICT"), primary_key=True
    )
    source: Mapped[str] = mapped_column(String(50), primary_key=True)
    scope_key: Mapped[str] = mapped_column(String(255), primary_key=True)
    state: Mapped[str] = mapped_column(
        String(24), nullable=False, default=PresenceState.ACTIVE.value
    )
    consecutive_complete_misses: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_seen_complete_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    listing: Mapped[Listing] = relationship(back_populates="presence")

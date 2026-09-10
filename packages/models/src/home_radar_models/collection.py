from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import (
    JSON,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    Uuid,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from home_radar_models.base import Base
from home_radar_models.enums import (
    CrawlCompleteness,
    CrawlRunStatus,
    DataMode,
    NormalizationStatus,
)

if TYPE_CHECKING:
    from home_radar_models.listing import ListingEvent, ListingSnapshot

JsonType = JSON().with_variant(JSONB(), "postgresql")


class CrawlRun(Base):
    __tablename__ = "crawl_run"
    __table_args__ = (
        CheckConstraint("data_mode IN ('demo', 'sample', 'live')", name="crawl_run_data_mode"),
        Index("ix_crawl_run_source_scope_started", "source", "scope_key", "started_at"),
        Index("ix_crawl_run_mode_started", "data_mode", "started_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    source: Mapped[str] = mapped_column(String(50), nullable=False)
    data_mode: Mapped[str] = mapped_column(
        String(16), nullable=False, default=DataMode.SAMPLE.value
    )
    scope_key: Mapped[str] = mapped_column(String(255), nullable=False, default="default")
    status: Mapped[str] = mapped_column(
        String(24), nullable=False, default=CrawlRunStatus.RUNNING.value
    )
    completeness: Mapped[str] = mapped_column(
        String(24), nullable=False, default=CrawlCompleteness.UNKNOWN.value
    )
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    raw_item_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    normalized_item_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    parse_error_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error_type: Mapped[str | None] = mapped_column(String(160))
    error_message: Mapped[str | None] = mapped_column(Text)
    # ``metadata`` is reserved by SQLAlchemy's declarative base.
    run_metadata: Mapped[dict[str, Any]] = mapped_column("metadata", JsonType, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    raw_records: Mapped[list[RawSourceRecord]] = relationship(
        back_populates="crawl_run", passive_deletes="all"
    )
    snapshots: Mapped[list[ListingSnapshot]] = relationship(
        back_populates="crawl_run", passive_deletes="all"
    )
    events: Mapped[list[ListingEvent]] = relationship(
        back_populates="crawl_run", passive_deletes="all"
    )

    @property
    def records_seen(self) -> int:
        return self.raw_item_count

    @records_seen.setter
    def records_seen(self, value: int) -> None:
        self.raw_item_count = value

    @property
    def records_ingested(self) -> int:
        return self.normalized_item_count

    @records_ingested.setter
    def records_ingested(self, value: int) -> None:
        self.normalized_item_count = value

    @property
    def records_failed(self) -> int:
        return self.parse_error_count

    @records_failed.setter
    def records_failed(self, value: int) -> None:
        self.parse_error_count = value


class RawSourceRecord(Base):
    __tablename__ = "raw_source_record"
    __table_args__ = (
        Index("ix_raw_record_crawl_run_source_record", "crawl_run_id", "source_record_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    crawl_run_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("crawl_run.id", ondelete="RESTRICT"), nullable=False
    )
    listing_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("listing.id", ondelete="RESTRICT")
    )
    source_record_id: Mapped[str | None] = mapped_column(String(255))
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JsonType, nullable=False)
    normalization_status: Mapped[str] = mapped_column(
        String(24), nullable=False, default=NormalizationStatus.PENDING.value
    )
    normalization_error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    crawl_run: Mapped[CrawlRun] = relationship(back_populates="raw_records")

    @property
    def run_id(self) -> uuid.UUID:
        return self.crawl_run_id

    @run_id.setter
    def run_id(self, value: uuid.UUID) -> None:
        self.crawl_run_id = value

    @property
    def source_listing_id(self) -> str | None:
        return self.source_record_id

    @source_listing_id.setter
    def source_listing_id(self, value: str | None) -> None:
        self.source_record_id = value


# Temporary import compatibility for the P1 collector and API.
CollectionRun = CrawlRun

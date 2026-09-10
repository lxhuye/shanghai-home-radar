from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from home_radar_models.base import Base
from home_radar_models.enums import (
    BaselineConfidence,
    DataMode,
    MarketObservationType,
)

JsonType = JSON().with_variant(JSONB(), "postgresql")


class MarketObservation(Base):
    """Source-independent market evidence; asking and transaction data stay separated."""

    __tablename__ = "market_observation"
    __table_args__ = (
        UniqueConstraint(
            "listing_snapshot_id",
            name="uq_market_observation_listing_snapshot",
        ),
        UniqueConstraint(
            "data_mode",
            "observation_type",
            "source",
            "source_record_id",
            "observed_at",
            name="uq_market_observation_source_time",
        ),
        CheckConstraint(
            "area_sqm IS NULL OR area_sqm > 0", name="market_observation_positive_area"
        ),
        CheckConstraint(
            "total_price IS NULL OR total_price >= 0",
            name="market_observation_nonnegative_price",
        ),
        CheckConstraint(
            "unit_price IS NULL OR unit_price >= 0",
            name="market_observation_nonnegative_unit_price",
        ),
        CheckConstraint(
            "monthly_rent IS NULL OR monthly_rent >= 0",
            name="market_observation_nonnegative_rent",
        ),
        CheckConstraint(
            "rent_per_sqm IS NULL OR rent_per_sqm >= 0",
            name="nonnegative_rent_per_sqm",
        ),
        CheckConstraint(
            "source_confidence >= 0 AND source_confidence <= 1",
            name="market_observation_confidence_range",
        ),
        CheckConstraint(
            "data_mode IN ('demo', 'sample', 'live')",
            name="market_observation_data_mode",
        ),
        CheckConstraint(
            "observation_type IN "
            "('listing', 'transaction', 'rental', 'official_index', 'external_baseline')",
            name="market_observation_type",
        ),
        CheckConstraint(
            "observation_type NOT IN ('listing', 'transaction') OR "
            "(area_sqm IS NOT NULL AND total_price IS NOT NULL AND unit_price IS NOT NULL)",
            name="market_observation_sale_price_fields",
        ),
        CheckConstraint(
            "observation_type != 'rental' OR "
            "(monthly_rent IS NOT NULL OR rent_per_sqm IS NOT NULL)",
            name="market_observation_rental_fields",
        ),
        CheckConstraint(
            "observation_type != 'official_index' OR index_value IS NOT NULL",
            name="market_observation_index_field",
        ),
        Index(
            "ix_market_observation_scope_time",
            "observation_type",
            "district",
            "submarket",
            "community",
            "observed_at",
        ),
        Index(
            "ix_market_observation_mode_type_time",
            "data_mode",
            "observation_type",
            "observed_at",
        ),
        Index(
            "ix_market_observation_segment_time",
            "data_mode",
            "observation_type",
            "area_bucket",
            "layout",
            "observed_at",
        ),
        Index("ix_market_observation_listing_time", "listing_id", "observed_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    observation_type: Mapped[str] = mapped_column(
        String(32), nullable=False, default=MarketObservationType.LISTING.value
    )
    data_mode: Mapped[str] = mapped_column(
        String(16), nullable=False, default=DataMode.SAMPLE.value
    )
    source: Mapped[str] = mapped_column(String(80), nullable=False)
    source_record_id: Mapped[str] = mapped_column(String(255), nullable=False)
    listing_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("listing.id", ondelete="RESTRICT"), index=True
    )
    listing_snapshot_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("listing_snapshot.id", ondelete="RESTRICT")
    )
    crawl_run_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("crawl_run.id", ondelete="RESTRICT"), index=True
    )
    district: Mapped[str | None] = mapped_column(String(80))
    submarket: Mapped[str | None] = mapped_column(String(120))
    community: Mapped[str | None] = mapped_column(String(160))
    area_sqm: Mapped[Decimal | None] = mapped_column(Numeric(8, 2))
    area_bucket: Mapped[str | None] = mapped_column(String(40))
    bedrooms: Mapped[int | None] = mapped_column(Integer)
    layout: Mapped[str | None] = mapped_column(String(40))
    total_price: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    unit_price: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    monthly_rent: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    rent_per_sqm: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    index_value: Mapped[Decimal | None] = mapped_column(Numeric(14, 6))
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    longitude: Mapped[Decimal | None] = mapped_column(Numeric(10, 7))
    latitude: Mapped[Decimal | None] = mapped_column(Numeric(10, 7))
    source_confidence: Mapped[Decimal] = mapped_column(
        Numeric(5, 4), nullable=False, default=Decimal("1")
    )
    coverage_complete: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    observation_metadata: Mapped[dict[str, Any]] = mapped_column(
        "metadata", JsonType, nullable=False, default=dict
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class MarketBaseline(Base):
    """Versioned materialized baseline for one evidence type, window, scope, and segment."""

    __tablename__ = "market_baseline"
    __table_args__ = (
        CheckConstraint(
            "data_mode IN ('demo', 'sample', 'live')", name="market_baseline_data_mode"
        ),
        CheckConstraint(
            "observation_type IN "
            "('listing', 'transaction', 'rental', 'official_index', 'external_baseline')",
            name="market_baseline_observation_type",
        ),
        CheckConstraint("window_days IN (30, 90, 180, 365)", name="market_baseline_window"),
        CheckConstraint(
            "level IN ('shanghai', 'district', 'submarket', 'community')",
            name="market_baseline_level",
        ),
        CheckConstraint(
            "confidence_level IN ('high', 'medium', 'low', 'insufficient')",
            name="market_baseline_confidence_level",
        ),
        UniqueConstraint(
            "materialization_run_id",
            "observation_type",
            "window_days",
            "level",
            "district",
            "submarket",
            "community",
            "area_bucket",
            "layout",
            name="uq_market_baseline_materialized_slice",
            postgresql_nulls_not_distinct=True,
        ),
        UniqueConstraint("baseline_version", name="uq_market_baseline_version"),
        Index(
            "ix_market_baseline_lookup_v2",
            "data_mode",
            "observation_type",
            "window_days",
            "level",
            "district",
            "submarket",
            "as_of_date",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    materialization_run_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("baseline_materialization_run.id", ondelete="RESTRICT"), nullable=False
    )
    as_of_date: Mapped[date] = mapped_column(Date, nullable=False)
    generated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    data_mode: Mapped[str] = mapped_column(
        String(16), nullable=False, default=DataMode.SAMPLE.value
    )
    observation_type: Mapped[str] = mapped_column(
        String(32), nullable=False, default=MarketObservationType.LISTING.value
    )
    window_days: Mapped[int] = mapped_column(Integer, nullable=False)
    level: Mapped[str] = mapped_column(String(32), nullable=False)
    district: Mapped[str | None] = mapped_column(String(80))
    submarket: Mapped[str | None] = mapped_column(String(120))
    community: Mapped[str | None] = mapped_column(String(160))
    area_bucket: Mapped[str | None] = mapped_column(String(40))
    layout: Mapped[str | None] = mapped_column(String(40))

    observation_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    outlier_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    effective_price_sample_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    effective_unit_price_sample_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0
    )
    price_p10: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    price_p25: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    price_p50: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    price_p75: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    price_p90: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    unit_price_p10: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    unit_price_p25: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    unit_price_p50: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    unit_price_p75: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    unit_price_p90: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))

    active_inventory: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    new_listings: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    price_cut_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    price_cut_ratio: Mapped[Decimal | None] = mapped_column(Numeric(7, 6))
    median_initial_ask: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    median_current_ask: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    median_price_cut_pct: Mapped[Decimal | None] = mapped_column(Numeric(7, 6))
    median_days_on_market: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))
    relisting_rate: Mapped[Decimal | None] = mapped_column(Numeric(7, 6))
    missing_candidate_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    inactive_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    ask_price_change_30d: Mapped[Decimal | None] = mapped_column(Numeric(9, 6))
    ask_price_change_90d: Mapped[Decimal | None] = mapped_column(Numeric(9, 6))
    inventory_change_30d: Mapped[Decimal | None] = mapped_column(Numeric(9, 6))
    inventory_change_90d: Mapped[Decimal | None] = mapped_column(Numeric(9, 6))

    median_monthly_rent: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    rent_per_sqm: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    gross_rental_yield: Mapped[Decimal | None] = mapped_column(Numeric(9, 6))
    rental_listing_liquidity: Mapped[Decimal | None] = mapped_column(Numeric(7, 4))

    liquidity_score: Mapped[Decimal | None] = mapped_column(Numeric(7, 4))
    liquidity_confidence: Mapped[Decimal | None] = mapped_column(Numeric(7, 6))
    liquidity_components: Mapped[dict[str, Any]] = mapped_column(
        JsonType, nullable=False, default=dict
    )
    confidence_level: Mapped[str] = mapped_column(
        String(24), nullable=False, default=BaselineConfidence.INSUFFICIENT.value
    )
    confidence_score: Mapped[Decimal] = mapped_column(
        Numeric(7, 6), nullable=False, default=Decimal("0")
    )
    confidence_components: Mapped[dict[str, Any]] = mapped_column(
        JsonType, nullable=False, default=dict
    )

    source_types: Mapped[list[str]] = mapped_column(JsonType, nullable=False, default=list)
    sources: Mapped[list[str]] = mapped_column(JsonType, nullable=False, default=list)
    observation_start: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    observation_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    window_start_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    window_end_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    input_cutoff_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    calculation_version: Mapped[str] = mapped_column(String(40), nullable=False)
    configuration_version: Mapped[str] = mapped_column(String(80), nullable=False)
    baseline_version: Mapped[str] = mapped_column(String(96), nullable=False)
    provenance: Mapped[dict[str, Any]] = mapped_column(JsonType, nullable=False, default=dict)


class BaselineMaterializationRun(Base):
    __tablename__ = "baseline_materialization_run"
    __table_args__ = (
        CheckConstraint(
            "data_mode IN ('demo', 'sample', 'live')",
            name="data_mode",
        ),
        CheckConstraint(
            "status IN ('running', 'succeeded', 'failed')",
            name="status",
        ),
        UniqueConstraint(
            "as_of_date",
            "data_mode",
            "calculation_version",
            "configuration_version",
            "input_signature",
            name="uq_baseline_materialization_run_identity",
        ),
        Index("ix_baseline_materialization_run_started", "started_at"),
        Index(
            "ix_baseline_materialization_run_latest",
            "data_mode",
            "status",
            "as_of_date",
            "finished_at",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    as_of_date: Mapped[date] = mapped_column(Date, nullable=False)
    data_mode: Mapped[str] = mapped_column(String(16), nullable=False)
    calculation_version: Mapped[str] = mapped_column(String(40), nullable=False)
    configuration_version: Mapped[str] = mapped_column(String(80), nullable=False)
    input_signature: Mapped[str] = mapped_column(String(64), nullable=False)
    input_cutoff_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    observation_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    baseline_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    error_message: Mapped[str | None] = mapped_column(Text)

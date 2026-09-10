from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from geoalchemy2 import Geometry
from sqlalchemy import (
    JSON,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
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
from home_radar_models.enums import DataMode

JsonType = JSON().with_variant(JSONB(), "postgresql")
DATA_MODE_CHECK = "data_mode IN ('demo', 'sample', 'live')"
CONFIDENCE_CHECK = "confidence >= 0 AND confidence <= 1"


class EmploymentCenter(Base):
    """Point-in-time, source-independent employment-center evidence."""

    __tablename__ = "employment_center"
    __table_args__ = (
        CheckConstraint(DATA_MODE_CHECK, name="employment_center_data_mode"),
        CheckConstraint(CONFIDENCE_CHECK, name="employment_center_confidence_range"),
        CheckConstraint(
            "current_employment_weight >= 0 AND future_employment_weight >= 0",
            name="employment_center_nonnegative_weights",
        ),
        UniqueConstraint(
            "data_mode",
            "source",
            "source_record_id",
            "effective_from",
            name="uq_employment_center_source_effective",
        ),
        Index("ix_employment_center_mode_effective", "data_mode", "effective_from"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    data_mode: Mapped[str] = mapped_column(
        String(16), nullable=False, default=DataMode.SAMPLE.value
    )
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    category: Mapped[str] = mapped_column(String(80), nullable=False)
    coordinates: Mapped[Any] = mapped_column(
        Geometry(geometry_type="POINT", srid=4326, spatial_index=True), nullable=False
    )
    current_employment_weight: Mapped[Decimal] = mapped_column(Numeric(10, 6), nullable=False)
    future_employment_weight: Mapped[Decimal] = mapped_column(Numeric(10, 6), nullable=False)
    effective_from: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    effective_to: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    source: Mapped[str] = mapped_column(String(120), nullable=False)
    source_record_id: Mapped[str] = mapped_column(String(255), nullable=False)
    source_timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    confidence: Mapped[Decimal] = mapped_column(Numeric(7, 6), nullable=False)
    provenance: Mapped[dict[str, Any]] = mapped_column(JsonType, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class FutureProject(Base):
    """Transport, renewal, supply, or public-service project evidence."""

    __tablename__ = "future_project"
    __table_args__ = (
        CheckConstraint(DATA_MODE_CHECK, name="future_project_data_mode"),
        CheckConstraint(CONFIDENCE_CHECK, name="future_project_confidence_range"),
        CheckConstraint(
            "status IN ('current', 'under_construction', 'approved', 'planned', 'conceptual')",
            name="future_project_status",
        ),
        CheckConstraint(
            "probability IS NULL OR (probability >= 0 AND probability <= 1)",
            name="future_project_probability_range",
        ),
        UniqueConstraint(
            "data_mode",
            "source",
            "source_record_id",
            "effective_from",
            name="uq_future_project_source_effective",
        ),
        Index(
            "ix_future_project_scope",
            "data_mode",
            "project_type",
            "district",
            "submarket",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    data_mode: Mapped[str] = mapped_column(String(16), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    project_type: Mapped[str] = mapped_column(String(48), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    coordinates: Mapped[Any | None] = mapped_column(
        Geometry(geometry_type="POINT", srid=4326, spatial_index=True)
    )
    district: Mapped[str | None] = mapped_column(String(80))
    submarket: Mapped[str | None] = mapped_column(String(120))
    community: Mapped[str | None] = mapped_column(String(160))
    expected_completion: Mapped[date | None] = mapped_column(Date)
    probability: Mapped[Decimal | None] = mapped_column(Numeric(7, 6))
    effective_from: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    effective_to: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    source: Mapped[str] = mapped_column(String(120), nullable=False)
    source_record_id: Mapped[str] = mapped_column(String(255), nullable=False)
    source_date: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    confidence: Mapped[Decimal] = mapped_column(Numeric(7, 6), nullable=False)
    provenance: Mapped[dict[str, Any]] = mapped_column(JsonType, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class FutureFactorObservation(Base):
    """Append-only normalized evidence for a future factor and geographic scope."""

    __tablename__ = "future_factor_observation"
    __table_args__ = (
        CheckConstraint(DATA_MODE_CHECK, name="data_mode"),
        CheckConstraint(CONFIDENCE_CHECK, name="confidence_range"),
        CheckConstraint(
            "scope_type IN ('shanghai', 'district', 'submarket', 'community', 'listing')",
            name="future_factor_observation_scope",
        ),
        CheckConstraint(
            "current_score IS NULL OR (current_score >= 0 AND current_score <= 100)",
            name="current_score_range",
        ),
        CheckConstraint(
            "future_score IS NULL OR (future_score >= 0 AND future_score <= 100)",
            name="future_score_range",
        ),
        UniqueConstraint(
            "data_mode",
            "factor",
            "source",
            "source_record_id",
            "observed_at",
            name="uq_future_factor_source_time",
        ),
        Index(
            "ix_future_factor_scope_latest",
            "data_mode",
            "factor",
            "scope_type",
            "observed_at",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    data_mode: Mapped[str] = mapped_column(String(16), nullable=False)
    factor: Mapped[str] = mapped_column(String(64), nullable=False)
    scope_type: Mapped[str] = mapped_column(String(24), nullable=False)
    listing_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("listing.id", ondelete="RESTRICT")
    )
    district: Mapped[str | None] = mapped_column(String(80))
    submarket: Mapped[str | None] = mapped_column(String(120))
    community: Mapped[str | None] = mapped_column(String(160))
    current_score: Mapped[Decimal | None] = mapped_column(Numeric(6, 2))
    future_score: Mapped[Decimal | None] = mapped_column(Numeric(6, 2))
    current_value: Mapped[Decimal | None] = mapped_column(Numeric(18, 6))
    future_value: Mapped[Decimal | None] = mapped_column(Numeric(18, 6))
    unit: Mapped[str | None] = mapped_column(String(48))
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    effective_from: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    effective_to: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    source: Mapped[str] = mapped_column(String(120), nullable=False)
    source_record_id: Mapped[str] = mapped_column(String(255), nullable=False)
    source_timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    confidence: Mapped[Decimal] = mapped_column(Numeric(7, 6), nullable=False)
    explanation: Mapped[str] = mapped_column(Text, nullable=False)
    observation_metadata: Mapped[dict[str, Any]] = mapped_column(
        JsonType, nullable=False, default=dict
    )
    provenance: Mapped[dict[str, Any]] = mapped_column(JsonType, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class FutureAssessment(Base):
    """Immutable, versioned P5 result for one P4 valuation anchor."""

    __tablename__ = "future_assessment"
    __table_args__ = (
        CheckConstraint(DATA_MODE_CHECK, name="future_assessment_data_mode"),
        CheckConstraint(
            "future_score IS NULL OR (future_score >= 0 AND future_score <= 100)",
            name="future_assessment_score_range",
        ),
        CheckConstraint(
            "obsolescence_risk IS NULL OR (obsolescence_risk >= 0 AND obsolescence_risk <= 100)",
            name="future_assessment_obsolescence_range",
        ),
        CheckConstraint(
            "structural_alpha >= -100 AND structural_alpha <= 100",
            name="future_assessment_alpha_range",
        ),
        CheckConstraint(
            "confidence IN ('high', 'medium', 'low', 'insufficient')",
            name="future_assessment_confidence",
        ),
        CheckConstraint(
            "calibration_state IN ('calibrated', 'partially_calibrated', 'uncalibrated')",
            name="future_assessment_calibration",
        ),
        CheckConstraint(
            "confidence_score >= 0 AND confidence_score <= 1",
            name="future_assessment_confidence_score_range",
        ),
        UniqueConstraint("future_assessment_version", name="uq_future_assessment_version"),
        UniqueConstraint(
            "listing_id",
            "data_mode",
            "input_fingerprint",
            "future_model_version",
            "scenario_model_version",
            "configuration_version",
            name="uq_future_assessment_input_versions",
        ),
        Index(
            "ix_future_assessment_listing_latest",
            "listing_id",
            "data_mode",
            "generated_at",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    listing_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("listing.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    data_mode: Mapped[str] = mapped_column(String(16), nullable=False)
    future_assessment_version: Mapped[str] = mapped_column(String(96), nullable=False)
    input_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    future_score: Mapped[Decimal | None] = mapped_column(Numeric(6, 2))
    future_score_coverage: Mapped[Decimal] = mapped_column(Numeric(7, 6), nullable=False)
    obsolescence_risk: Mapped[Decimal | None] = mapped_column(Numeric(6, 2))
    obsolescence_coverage: Mapped[Decimal] = mapped_column(Numeric(7, 6), nullable=False)
    structural_alpha: Mapped[Decimal] = mapped_column(Numeric(7, 2), nullable=False)
    quality_value_quadrant: Mapped[str | None] = mapped_column(String(40))
    relative_outlook: Mapped[str] = mapped_column(String(32), nullable=False)
    confidence: Mapped[str] = mapped_column(String(24), nullable=False)
    confidence_score: Mapped[Decimal] = mapped_column(Numeric(7, 6), nullable=False)
    calibration_state: Mapped[str] = mapped_column(String(32), nullable=False)
    scenarios: Mapped[list[dict[str, Any]]] = mapped_column(JsonType, nullable=False, default=list)
    factor_breakdown: Mapped[list[dict[str, Any]]] = mapped_column(
        JsonType, nullable=False, default=list
    )
    risk_breakdown: Mapped[dict[str, Any]] = mapped_column(JsonType, nullable=False, default=dict)
    structural_components: Mapped[dict[str, Any]] = mapped_column(
        JsonType, nullable=False, default=dict
    )
    warnings: Mapped[list[str]] = mapped_column(JsonType, nullable=False, default=list)
    missing_inputs: Mapped[list[str]] = mapped_column(JsonType, nullable=False, default=list)
    provenance: Mapped[dict[str, Any]] = mapped_column(JsonType, nullable=False, default=dict)
    fair_value_anchor: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    value_score: Mapped[Decimal] = mapped_column(Numeric(6, 2), nullable=False)
    future_model_version: Mapped[str] = mapped_column(String(40), nullable=False)
    scenario_model_version: Mapped[str] = mapped_column(String(40), nullable=False)
    valuation_version: Mapped[str] = mapped_column(String(96), nullable=False)
    baseline_version: Mapped[str] = mapped_column(String(256), nullable=False)
    configuration_version: Mapped[str] = mapped_column(String(80), nullable=False)
    data_version: Mapped[str] = mapped_column(String(96), nullable=False)
    data_timestamp: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    generated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

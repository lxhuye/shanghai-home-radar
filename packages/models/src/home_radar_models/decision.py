from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

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
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from home_radar_models.base import Base
from home_radar_models.enums import DataMode

JsonType = JSON().with_variant(JSONB(), "postgresql")


class DecisionAssessment(Base):
    """Immutable P5.5 orchestration of versioned P3, P4, and P5 results."""

    __tablename__ = "decision_assessment"
    __table_args__ = (
        CheckConstraint(
            "data_mode IN ('demo', 'sample', 'live')",
            name="decision_assessment_data_mode",
        ),
        CheckConstraint(
            "opportunity_classification IN "
            "('QUALITY_AT_DISCOUNT', 'GOOD_BUT_EXPENSIVE', 'VALUE_TRAP', "
            "'LOW_QUALITY', 'INSUFFICIENT_DATA')",
            name="decision_assessment_classification",
        ),
        CheckConstraint(
            "workflow_state IN ('PASS', 'WATCH', 'CONTACT', 'VIEW')",
            name="decision_assessment_workflow",
        ),
        CheckConstraint(
            "eligibility_status IN ('ELIGIBLE', 'HARD_RISK_FILTERED', 'INSUFFICIENT')",
            name="decision_assessment_eligibility",
        ),
        CheckConstraint(
            "value_score >= 0 AND value_score <= 100",
            name="decision_assessment_value_score_range",
        ),
        CheckConstraint(
            "future_score IS NULL OR (future_score >= 0 AND future_score <= 100)",
            name="decision_assessment_future_score_range",
        ),
        CheckConstraint(
            "liquidity_score IS NULL OR (liquidity_score >= 0 AND liquidity_score <= 100)",
            name="decision_assessment_liquidity_range",
        ),
        CheckConstraint(
            "obsolescence_risk IS NULL OR (obsolescence_risk >= 0 AND obsolescence_risk <= 100)",
            name="decision_assessment_obsolescence_range",
        ),
        UniqueConstraint("decision_version", name="uq_decision_assessment_version"),
        UniqueConstraint(
            "listing_id",
            "data_mode",
            "input_fingerprint",
            "decision_model_version",
            "configuration_version",
            name="uq_decision_assessment_input_versions",
        ),
        Index(
            "ix_decision_assessment_listing_latest",
            "listing_id",
            "data_mode",
            "generated_at",
        ),
        Index(
            "ix_decision_assessment_opportunity",
            "data_mode",
            "eligibility_status",
            "opportunity_classification",
        ),
        Index(
            "ix_decision_assessment_rank_universe",
            "data_mode",
            "decision_model_version",
            "configuration_version",
            "generated_at",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    listing_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("listing.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    data_mode: Mapped[str] = mapped_column(
        String(16), nullable=False, default=DataMode.SAMPLE.value
    )
    decision_version: Mapped[str] = mapped_column(String(96), nullable=False)
    input_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    generated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    current_ask: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    fair_value: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    fair_value_low: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    fair_value_high: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    value_score: Mapped[Decimal] = mapped_column(Numeric(6, 2), nullable=False)
    future_score: Mapped[Decimal | None] = mapped_column(Numeric(6, 2))
    liquidity_score: Mapped[Decimal | None] = mapped_column(Numeric(7, 4))
    obsolescence_risk: Mapped[Decimal | None] = mapped_column(Numeric(6, 2))
    structural_alpha: Mapped[Decimal] = mapped_column(Numeric(7, 2), nullable=False)
    valuation_confidence: Mapped[str] = mapped_column(String(24), nullable=False)
    future_confidence: Mapped[str] = mapped_column(String(24), nullable=False)
    calibration_state: Mapped[str] = mapped_column(String(32), nullable=False)

    opportunity_classification: Mapped[str] = mapped_column(String(40), nullable=False)
    workflow_state: Mapped[str] = mapped_column(String(16), nullable=False)
    eligibility_status: Mapped[str] = mapped_column(String(32), nullable=False)
    confidence_gate_passed: Mapped[bool] = mapped_column(Boolean, nullable=False)
    hard_risks: Mapped[list[str]] = mapped_column(JsonType, nullable=False, default=list)
    positive_reasons: Mapped[list[dict[str, Any]]] = mapped_column(
        JsonType, nullable=False, default=list
    )
    negative_reasons: Mapped[list[dict[str, Any]]] = mapped_column(
        JsonType, nullable=False, default=list
    )
    warnings: Mapped[list[str]] = mapped_column(JsonType, nullable=False, default=list)
    ranking_dimensions: Mapped[dict[str, Any]] = mapped_column(
        JsonType, nullable=False, default=dict
    )
    provenance: Mapped[dict[str, Any]] = mapped_column(JsonType, nullable=False, default=dict)

    valuation_version: Mapped[str] = mapped_column(String(96), nullable=False)
    future_assessment_version: Mapped[str] = mapped_column(String(96), nullable=False)
    baseline_version: Mapped[str] = mapped_column(String(256), nullable=False)
    decision_model_version: Mapped[str] = mapped_column(String(40), nullable=False)
    configuration_version: Mapped[str] = mapped_column(String(80), nullable=False)
    data_version: Mapped[str] = mapped_column(String(96), nullable=False)
    data_timestamp: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class DecisionValidationBatch(Base):
    """A frozen blind-review batch whose model output stays hidden until reveal."""

    __tablename__ = "decision_validation_batch"
    __table_args__ = (
        CheckConstraint(
            "data_mode IN ('demo', 'sample', 'live')",
            name="data_mode",
        ),
        CheckConstraint(
            "status IN ('blind_labeling', 'labels_frozen', 'model_generated', "
            "'revealed', 'finalized')",
            name="decision_validation_batch_status",
        ),
        CheckConstraint(
            "final_recommendation IS NULL OR final_recommendation IN "
            "('P6_READY', 'P6_READY_WITH_LIMITATIONS', 'P5_CALIBRATION_REQUIRED')",
            name="recommendation",
        ),
        UniqueConstraint("validation_run_id", name="uq_decision_validation_run_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    data_mode: Mapped[str] = mapped_column(String(16), nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="blind_labeling")
    target_sample_size: Mapped[int] = mapped_column(Integer, nullable=False)
    input_cutoff_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    decision_model_version: Mapped[str] = mapped_column(String(40), nullable=False)
    configuration_version: Mapped[str] = mapped_column(String(80), nullable=False)
    metrics: Mapped[dict[str, Any]] = mapped_column(JsonType, nullable=False, default=dict)
    protocol_version: Mapped[str] = mapped_column(String(16), nullable=False, default="p5.5")
    validation_run_id: Mapped[str | None] = mapped_column(String(96))
    freeze_manifest: Mapped[dict[str, Any]] = mapped_column(JsonType, nullable=False, default=dict)
    dataset_fingerprint: Mapped[str | None] = mapped_column(String(64))
    human_labels_fingerprint: Mapped[str | None] = mapped_column(String(64))
    model_outputs_fingerprint: Mapped[str | None] = mapped_column(String(64))
    final_recommendation: Mapped[str | None] = mapped_column(String(40))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    revealed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    labels_frozen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    model_generated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finalized_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class DecisionValidationItem(Base):
    __tablename__ = "decision_validation_item"
    __table_args__ = (
        CheckConstraint(
            "human_label IS NULL OR human_label IN ('WORTH_VIEWING', 'WAIT', 'VALUE_TRAP', 'PASS')",
            name="human_label",
        ),
        CheckConstraint(
            "frozen_rank IS NULL OR frozen_rank > 0",
            name="positive_rank",
        ),
        CheckConstraint(
            "geography_bucket IS NULL OR geography_bucket IN "
            "('OUTER_XUHUI', 'NORTHERN_MINHANG', 'PUTUO', 'YANGPU', 'MATURE_PUDONG')",
            name="decision_validation_item_geography",
        ),
        CheckConstraint(
            "human_opportunity_classification IS NULL OR "
            "human_opportunity_classification IN "
            "('QUALITY_AT_DISCOUNT', 'GOOD_BUT_EXPENSIVE', 'VALUE_TRAP', "
            "'LOW_QUALITY', 'INSUFFICIENT_INFORMATION')",
            name="human_class",
        ),
        CheckConstraint(
            "human_workflow_recommendation IS NULL OR "
            "human_workflow_recommendation IN ('PASS', 'WATCH', 'CONTACT', 'VIEW')",
            name="human_workflow",
        ),
        CheckConstraint(
            "human_confidence IS NULL OR human_confidence IN ('LOW', 'MEDIUM', 'HIGH')",
            name="human_confidence",
        ),
        UniqueConstraint("batch_id", "listing_id", name="uq_decision_validation_batch_listing"),
        Index("ix_decision_validation_item_batch_rank", "batch_id", "frozen_rank"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    batch_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("decision_validation_batch.id", ondelete="RESTRICT"), nullable=False
    )
    listing_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("listing.id", ondelete="RESTRICT"), nullable=False
    )
    decision_assessment_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("decision_assessment.id", ondelete="RESTRICT"), nullable=True
    )
    frozen_rank: Mapped[int | None] = mapped_column(Integer)
    listing_snapshot: Mapped[dict[str, Any]] = mapped_column(JsonType, nullable=False, default=dict)
    model_snapshot: Mapped[dict[str, Any]] = mapped_column(JsonType, nullable=False, default=dict)
    human_label: Mapped[str | None] = mapped_column(String(24))
    geography_bucket: Mapped[str | None] = mapped_column(String(40))
    archetypes: Mapped[list[str]] = mapped_column(JsonType, nullable=False, default=list)
    data_provenance: Mapped[dict[str, Any]] = mapped_column(JsonType, nullable=False, default=dict)
    human_opportunity_classification: Mapped[str | None] = mapped_column(String(40))
    human_workflow_recommendation: Mapped[str | None] = mapped_column(String(16))
    human_confidence: Mapped[str | None] = mapped_column(String(16))
    human_positive_reasons: Mapped[list[str]] = mapped_column(
        JsonType, nullable=False, default=list
    )
    human_risks: Mapped[list[str]] = mapped_column(JsonType, nullable=False, default=list)
    human_would_visit: Mapped[bool | None] = mapped_column(Boolean)
    notes: Mapped[str | None] = mapped_column(Text)
    labeled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class DecisionValidationReview(Base):
    """Post-reveal review. Original blind labels remain untouched."""

    __tablename__ = "decision_validation_review"
    __table_args__ = (
        CheckConstraint(
            "why_ranked_verdict IN ('AGREE', 'PARTIAL', 'CONTRADICT')",
            name="verdict",
        ),
        CheckConstraint(
            "root_cause IS NULL OR root_cause IN "
            "('DATA_GAP', 'BAD_COMPARABLE_SELECTION', 'VALUATION_ERROR', "
            "'LIQUIDITY_ERROR', 'FUTURE_FACTOR_ERROR', 'OBSOLESCENCE_ERROR', "
            "'DECISION_RULE_ERROR', 'HUMAN_DISAGREEMENT', 'UNKNOWN')",
            name="root_cause",
        ),
        UniqueConstraint("item_id", name="uq_decision_validation_review_item"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    item_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("decision_validation_item.id", ondelete="RESTRICT"), nullable=False
    )
    why_ranked_verdict: Mapped[str] = mapped_column(String(16), nullable=False)
    root_cause: Mapped[str | None] = mapped_column(String(40))
    notes: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class DecisionValidationLabelAmendment(Base):
    """Append-only post-reveal correction proposal; never replaces a frozen label."""

    __tablename__ = "decision_validation_label_amendment"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    item_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("decision_validation_item.id", ondelete="RESTRICT"), nullable=False
    )
    proposed_label: Mapped[dict[str, Any]] = mapped_column(JsonType, nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

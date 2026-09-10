from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    JSON,
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


class ValuationResult(Base):
    """Immutable, versioned P4 result cached for one canonical listing input."""

    __tablename__ = "valuation_result"
    __table_args__ = (
        CheckConstraint(
            "data_mode IN ('demo', 'sample', 'live')", name="valuation_result_data_mode"
        ),
        CheckConstraint(
            "valuation_basis IN ('listing_derived', 'transaction_supported', 'mixed_source')",
            name="valuation_result_basis",
        ),
        CheckConstraint(
            "valuation_confidence IN ('high', 'medium', 'low', 'insufficient')",
            name="valuation_result_confidence",
        ),
        CheckConstraint(
            "transaction_support IN ('none', 'weak', 'strong')",
            name="valuation_result_transaction_support",
        ),
        CheckConstraint(
            "decision IN ('pass', 'watch', 'contact', 'view', 'attack')",
            name="valuation_result_decision",
        ),
        CheckConstraint(
            "fair_value > 0 AND fair_value_low > 0 AND fair_value_high > 0 "
            "AND fair_value_low <= fair_value AND fair_value <= fair_value_high",
            name="valuation_result_value_range",
        ),
        CheckConstraint("current_ask > 0", name="valuation_result_positive_current_ask"),
        CheckConstraint(
            "estimated_executable_price IS NULL OR estimated_executable_price > 0",
            name="valuation_result_positive_executable_price",
        ),
        CheckConstraint(
            "valuation_confidence_score >= 0 AND valuation_confidence_score <= 1",
            name="valuation_result_confidence_score_range",
        ),
        CheckConstraint(
            "value_score >= 0 AND value_score <= 100",
            name="valuation_result_value_score_range",
        ),
        CheckConstraint(
            "comparable_count >= 0 AND effective_comparable_count >= 0",
            name="valuation_result_nonnegative_comparables",
        ),
        UniqueConstraint("valuation_version", name="uq_valuation_result_version"),
        UniqueConstraint(
            "listing_id",
            "data_mode",
            "input_fingerprint",
            "valuation_model_version",
            "scoring_model_version",
            "configuration_version",
            name="uq_valuation_result_input_versions",
        ),
        Index(
            "ix_valuation_result_listing_latest",
            "listing_id",
            "data_mode",
            "calculated_at",
        ),
        Index(
            "ix_valuation_result_mode_decision_score",
            "data_mode",
            "decision",
            "value_score",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    listing_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("listing.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    data_mode: Mapped[str] = mapped_column(
        String(16), nullable=False, default=DataMode.SAMPLE.value
    )
    valuation_version: Mapped[str] = mapped_column(String(96), nullable=False)
    input_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    calculated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    fair_value: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    fair_value_low: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    fair_value_high: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    valuation_basis: Mapped[str] = mapped_column(String(32), nullable=False)
    valuation_confidence_score: Mapped[Decimal] = mapped_column(Numeric(7, 6), nullable=False)
    valuation_confidence: Mapped[str] = mapped_column(String(24), nullable=False)
    transaction_support: Mapped[str] = mapped_column(String(16), nullable=False)

    current_ask: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    ask_discount_to_fair_value: Mapped[Decimal] = mapped_column(Numeric(9, 6), nullable=False)
    estimated_executable_price: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    executable_discount_to_fair_value: Mapped[Decimal | None] = mapped_column(Numeric(9, 6))
    value_score: Mapped[Decimal] = mapped_column(Numeric(6, 2), nullable=False)
    decision: Mapped[str] = mapped_column(String(16), nullable=False)

    comparable_count: Mapped[int] = mapped_column(Integer, nullable=False)
    effective_comparable_count: Mapped[Decimal] = mapped_column(Numeric(10, 4), nullable=False)
    baseline_level_used: Mapped[str] = mapped_column(String(48), nullable=False)
    baseline_confidence: Mapped[str] = mapped_column(String(24), nullable=False)
    baseline_version: Mapped[str] = mapped_column(String(256), nullable=False)
    fallback_reason: Mapped[str | None] = mapped_column(Text)

    adjustments: Mapped[list[dict[str, Any]]] = mapped_column(
        JsonType, nullable=False, default=list
    )
    comparables: Mapped[list[dict[str, Any]]] = mapped_column(
        JsonType, nullable=False, default=list
    )
    warnings: Mapped[list[str]] = mapped_column(JsonType, nullable=False, default=list)
    score_components: Mapped[dict[str, Any]] = mapped_column(JsonType, nullable=False, default=dict)
    price_history: Mapped[dict[str, Any]] = mapped_column(JsonType, nullable=False, default=dict)
    provenance: Mapped[dict[str, Any]] = mapped_column(JsonType, nullable=False, default=dict)

    valuation_model_version: Mapped[str] = mapped_column(String(40), nullable=False)
    scoring_model_version: Mapped[str] = mapped_column(String(40), nullable=False)
    configuration_version: Mapped[str] = mapped_column(String(80), nullable=False)
    baseline_generated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

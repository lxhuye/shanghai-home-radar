from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, Field


class DecisionReasonRead(BaseModel):
    code: str
    text: str
    source_stage: str
    evidence: dict[str, str]


class DecisionRead(BaseModel):
    listing_id: uuid.UUID
    data_mode: str
    data_notice: str
    recommendation_status: str
    investment_conclusion: None = None
    cache_hit: bool = False
    rank: int | None = None
    eligible_count: int
    current_ask: Decimal
    fair_value: Decimal
    fair_value_low: Decimal
    fair_value_high: Decimal
    value_score: Decimal
    future_score: Decimal | None
    liquidity_score: Decimal | None
    obsolescence_risk: Decimal | None
    structural_alpha: Decimal
    valuation_confidence: str
    future_confidence: str
    calibration_state: str
    opportunity_classification: str
    workflow_state: str
    eligibility_status: str
    confidence_gate_passed: bool
    hard_risks: list[str]
    positive_reasons: list[DecisionReasonRead]
    negative_reasons: list[DecisionReasonRead]
    warnings: list[str]
    ranking_dimensions: dict[str, Any]
    provenance: dict[str, Any]
    valuation_version: str
    future_assessment_version: str
    baseline_version: str
    decision_model_version: str
    configuration_version: str
    data_version: str
    data_timestamp: datetime | None
    input_fingerprint: str
    decision_version: str
    generated_at: datetime


class OpportunityPage(BaseModel):
    data_mode: str
    evaluated_count: int
    eligible_count: int
    items: list[DecisionRead]


class WhyRankedRead(BaseModel):
    listing_id: uuid.UUID
    decision_version: str
    rank: int | None
    eligible_count: int
    summary: str
    eligibility_status: str
    opportunity_classification: str
    ranking_dimensions: dict[str, Any]
    positive_reasons: list[DecisionReasonRead]
    negative_reasons: list[DecisionReasonRead]
    hard_risks: list[str]


class ValidationBatchCreate(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    listing_ids: list[uuid.UUID] = Field(min_length=1, max_length=100)
    as_of: datetime | None = None


class ValidationLabelInput(BaseModel):
    listing_id: uuid.UUID
    human_label: Literal["WORTH_VIEWING", "WAIT", "VALUE_TRAP", "PASS"]
    notes: str | None = Field(default=None, max_length=2000)


class ValidationLabelsRequest(BaseModel):
    labels: list[ValidationLabelInput] = Field(min_length=1, max_length=100)


class ValidationItemRead(BaseModel):
    listing_id: uuid.UUID
    listing: dict[str, Any]
    human_label: str | None
    notes: str | None
    labeled_at: datetime | None
    frozen_rank: int | None = None
    model_result: dict[str, Any] | None = None


class ValidationBatchRead(BaseModel):
    id: uuid.UUID
    name: str
    data_mode: str
    status: str
    target_sample_size: int
    actual_sample_size: int
    labeled_count: int
    input_cutoff_at: datetime
    decision_model_version: str
    configuration_version: str
    metrics: dict[str, Any] | None
    items: list[ValidationItemRead]


class RealWorldDatasetItemInput(BaseModel):
    listing_id: uuid.UUID
    geography_bucket: Literal[
        "OUTER_XUHUI",
        "NORTHERN_MINHANG",
        "PUTUO",
        "YANGPU",
        "MATURE_PUDONG",
    ]
    archetypes: list[str] = Field(min_length=1, max_length=10)
    data_provenance: dict[str, str]


class RealWorldValidationCreate(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    entries: list[RealWorldDatasetItemInput] = Field(min_length=1, max_length=55)
    as_of: datetime | None = None


class RealWorldHumanLabelInput(BaseModel):
    listing_id: uuid.UUID
    opportunity_classification: Literal[
        "QUALITY_AT_DISCOUNT",
        "GOOD_BUT_EXPENSIVE",
        "VALUE_TRAP",
        "LOW_QUALITY",
        "INSUFFICIENT_INFORMATION",
    ]
    workflow_recommendation: Literal["PASS", "WATCH", "CONTACT", "VIEW"]
    confidence: Literal["LOW", "MEDIUM", "HIGH"]
    positive_reasons: list[str] = Field(min_length=1, max_length=3)
    risks: list[str] = Field(min_length=1, max_length=3)
    would_physically_visit: bool
    notes: str | None = Field(default=None, max_length=2000)


class RealWorldLabelsRequest(BaseModel):
    labels: list[RealWorldHumanLabelInput] = Field(min_length=1, max_length=55)


class RealWorldReviewInput(BaseModel):
    listing_id: uuid.UUID
    why_ranked_verdict: Literal["AGREE", "PARTIAL", "CONTRADICT"]
    root_cause: (
        Literal[
            "DATA_GAP",
            "BAD_COMPARABLE_SELECTION",
            "VALUATION_ERROR",
            "LIQUIDITY_ERROR",
            "FUTURE_FACTOR_ERROR",
            "OBSOLESCENCE_ERROR",
            "DECISION_RULE_ERROR",
            "HUMAN_DISAGREEMENT",
            "UNKNOWN",
        ]
        | None
    ) = None
    notes: str | None = Field(default=None, max_length=4000)


class RealWorldReviewsRequest(BaseModel):
    reviews: list[RealWorldReviewInput] = Field(min_length=1, max_length=55)


class RealWorldAmendmentRequest(BaseModel):
    listing_id: uuid.UUID
    proposed_label: dict[str, Any]
    reason: str = Field(min_length=1, max_length=4000)


class RealWorldValidationItemRead(BaseModel):
    listing_id: uuid.UUID
    geography_bucket: str
    archetypes: list[str]
    data_provenance: dict[str, Any]
    listing: dict[str, Any]
    human_label: dict[str, Any] | None
    labeled_at: datetime | None
    frozen_rank: int | None
    model_result: dict[str, Any] | None


class RealWorldValidationRunRead(BaseModel):
    id: uuid.UUID
    validation_run_id: str
    name: str
    protocol_version: str
    data_mode: str
    status: str
    target_sample_size: int
    actual_sample_size: int
    labeled_count: int
    reviewed_count: int
    input_cutoff_at: datetime
    freeze_manifest: dict[str, Any]
    metrics: dict[str, Any] | None
    final_recommendation: str | None
    labels_frozen_at: datetime | None
    model_generated_at: datetime | None
    revealed_at: datetime | None
    finalized_at: datetime | None
    items: list[RealWorldValidationItemRead]

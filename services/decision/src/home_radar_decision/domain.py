from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any, Literal

OpportunityClassification = Literal[
    "QUALITY_AT_DISCOUNT",
    "GOOD_BUT_EXPENSIVE",
    "VALUE_TRAP",
    "LOW_QUALITY",
    "INSUFFICIENT_DATA",
]
WorkflowState = Literal["PASS", "WATCH", "CONTACT", "VIEW"]
EligibilityStatus = Literal["ELIGIBLE", "HARD_RISK_FILTERED", "INSUFFICIENT"]
HumanLabel = Literal["WORTH_VIEWING", "WAIT", "VALUE_TRAP", "PASS"]


@dataclass(frozen=True)
class DecisionReason:
    code: str
    text: str
    source_stage: str
    evidence: dict[str, str]


@dataclass(frozen=True)
class DecisionInputs:
    listing_id: uuid.UUID
    data_mode: str
    current_ask: Decimal
    fair_value: Decimal
    fair_value_low: Decimal
    fair_value_high: Decimal
    ask_discount_to_fair_value: Decimal
    value_score: Decimal
    liquidity_score: Decimal | None
    valuation_confidence: str
    baseline_confidence: str
    future_score: Decimal | None
    future_score_coverage: Decimal
    future_confidence: str
    obsolescence_risk: Decimal | None
    obsolescence_coverage: Decimal
    structural_alpha: Decimal
    calibration_state: str
    valuation_warnings: tuple[str, ...]
    future_warnings: tuple[str, ...]
    factor_breakdown: tuple[dict[str, Any], ...]
    risk_breakdown: dict[str, Any]
    valuation_version: str
    future_assessment_version: str
    baseline_version: str
    valuation_configuration_version: str
    future_configuration_version: str
    data_version: str
    data_timestamp: datetime | None


@dataclass(frozen=True)
class DecisionEvaluation:
    listing_id: uuid.UUID
    data_mode: str
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
    opportunity_classification: OpportunityClassification
    workflow_state: WorkflowState
    eligibility_status: EligibilityStatus
    confidence_gate_passed: bool
    hard_risks: tuple[str, ...]
    positive_reasons: tuple[DecisionReason, ...]
    negative_reasons: tuple[DecisionReason, ...]
    warnings: tuple[str, ...]
    ranking_dimensions: dict[str, str | int]
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


@dataclass(frozen=True)
class RankedDecision:
    rank: int
    eligible_count: int
    evaluation: DecisionEvaluation


@dataclass(frozen=True)
class BlindEvaluationCase:
    listing_id: uuid.UUID
    rank: int | None
    opportunity_classification: OpportunityClassification
    workflow_state: WorkflowState
    eligibility_status: EligibilityStatus
    human_label: HumanLabel


@dataclass(frozen=True)
class BlindEvaluationMetrics:
    labeled_count: int
    precision_at_5: Decimal | None
    precision_at_10: Decimal | None
    value_trap_false_positive_rate: Decimal | None
    view_acceptance_rate: Decimal | None
    top_10_manual_acceptance_rate: Decimal | None

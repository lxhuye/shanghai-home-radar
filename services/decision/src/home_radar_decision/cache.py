from __future__ import annotations

import hashlib
import uuid
from dataclasses import asdict
from datetime import datetime
from typing import Any

from home_radar_forecasting.cache import evaluate_cached_future
from home_radar_forecasting.config import FutureConfig
from home_radar_market.config import MarketBaselineConfig
from home_radar_models.decision import DecisionAssessment
from home_radar_models.enums import DataMode
from home_radar_valuation.config import ValuationConfig
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from home_radar_decision.config import DecisionConfig
from home_radar_decision.domain import DecisionEvaluation, DecisionReason
from home_radar_decision.orchestrator import DecisionOrchestrator
from home_radar_decision.repository import load_decision_inputs


def evaluate_cached_decision(
    session: Session,
    listing_id: uuid.UUID,
    data_mode: DataMode,
    as_of: datetime,
    market_config: MarketBaselineConfig,
    valuation_config: ValuationConfig,
    future_config: FutureConfig,
    decision_config: DecisionConfig,
) -> tuple[DecisionAssessment, bool]:
    future, _ = evaluate_cached_future(
        session,
        listing_id,
        data_mode,
        as_of,
        market_config,
        valuation_config,
        future_config,
    )
    session.flush()
    evaluation = DecisionOrchestrator(decision_config).evaluate(
        load_decision_inputs(session, future)
    )
    _advisory_lock(session, listing_id, data_mode)
    existing = session.scalar(
        select(DecisionAssessment).where(
            DecisionAssessment.listing_id == listing_id,
            DecisionAssessment.data_mode == data_mode.value,
            DecisionAssessment.input_fingerprint == evaluation.input_fingerprint,
            DecisionAssessment.decision_model_version == decision_config.decision_model_version,
            DecisionAssessment.configuration_version == decision_config.configuration_version,
        )
    )
    if existing is not None:
        return existing, True
    model = _model_from_evaluation(evaluation)
    session.add(model)
    session.flush()
    return model, False


def _model_from_evaluation(evaluation: DecisionEvaluation) -> DecisionAssessment:
    return DecisionAssessment(
        listing_id=evaluation.listing_id,
        data_mode=evaluation.data_mode,
        decision_version=evaluation.decision_version,
        input_fingerprint=evaluation.input_fingerprint,
        generated_at=evaluation.generated_at,
        current_ask=evaluation.current_ask,
        fair_value=evaluation.fair_value,
        fair_value_low=evaluation.fair_value_low,
        fair_value_high=evaluation.fair_value_high,
        value_score=evaluation.value_score,
        future_score=evaluation.future_score,
        liquidity_score=evaluation.liquidity_score,
        obsolescence_risk=evaluation.obsolescence_risk,
        structural_alpha=evaluation.structural_alpha,
        valuation_confidence=evaluation.valuation_confidence,
        future_confidence=evaluation.future_confidence,
        calibration_state=evaluation.calibration_state,
        opportunity_classification=evaluation.opportunity_classification,
        workflow_state=evaluation.workflow_state,
        eligibility_status=evaluation.eligibility_status,
        confidence_gate_passed=evaluation.confidence_gate_passed,
        hard_risks=list(evaluation.hard_risks),
        positive_reasons=[_reason_dict(reason) for reason in evaluation.positive_reasons],
        negative_reasons=[_reason_dict(reason) for reason in evaluation.negative_reasons],
        warnings=list(evaluation.warnings),
        ranking_dimensions=evaluation.ranking_dimensions,
        provenance=evaluation.provenance,
        valuation_version=evaluation.valuation_version,
        future_assessment_version=evaluation.future_assessment_version,
        baseline_version=evaluation.baseline_version,
        decision_model_version=evaluation.decision_model_version,
        configuration_version=evaluation.configuration_version,
        data_version=evaluation.data_version,
        data_timestamp=evaluation.data_timestamp,
    )


def _reason_dict(reason: DecisionReason) -> dict[str, Any]:
    return asdict(reason)


def _advisory_lock(session: Session, listing_id: uuid.UUID, data_mode: DataMode) -> None:
    if session.get_bind().dialect.name != "postgresql":
        return
    digest = hashlib.sha256(f"decision:{data_mode.value}:{listing_id}".encode()).digest()
    lock_key = int.from_bytes(digest[:8], byteorder="big", signed=True)
    session.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": lock_key})

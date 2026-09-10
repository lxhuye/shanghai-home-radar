from __future__ import annotations

import hashlib
import uuid
from dataclasses import asdict
from datetime import datetime
from typing import Any

from home_radar_market.config import MarketBaselineConfig
from home_radar_models.enums import DataMode
from home_radar_models.future import FutureAssessment
from home_radar_models.valuation import ValuationResult
from home_radar_valuation.cache import evaluate_cached_listing
from home_radar_valuation.config import ValuationConfig
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from home_radar_forecasting.config import FutureConfig
from home_radar_forecasting.domain import FactorEvidence, FutureEvaluation
from home_radar_forecasting.engine import (
    DeterministicFutureEngine,
    build_future_input_fingerprint,
)
from home_radar_forecasting.factors import build_future_factors
from home_radar_forecasting.repository import load_future_inputs


def evaluate_cached_future(
    session: Session,
    listing_id: uuid.UUID,
    data_mode: DataMode,
    as_of: datetime,
    market_config: MarketBaselineConfig,
    valuation_config: ValuationConfig,
    future_config: FutureConfig,
) -> tuple[FutureAssessment, bool]:
    valuation, _ = evaluate_cached_listing(
        session,
        listing_id,
        data_mode,
        as_of,
        market_config,
        valuation_config,
    )
    session.flush()
    inputs = load_future_inputs(
        session,
        listing_id,
        data_mode,
        as_of,
        valuation=valuation,
    )
    factors = build_future_factors(inputs, as_of, future_config)
    fingerprint = build_future_input_fingerprint(inputs, factors, future_config, as_of)
    _advisory_lock(session, listing_id, data_mode)
    existing = _cached_assessment(session, listing_id, data_mode, fingerprint, future_config)
    if existing is not None:
        return existing, True
    evaluation = DeterministicFutureEngine(future_config).evaluate(
        inputs, as_of, input_fingerprint=fingerprint
    )
    result = _model_from_evaluation(valuation, evaluation)
    session.add(result)
    session.flush()
    return result, False


def _cached_assessment(
    session: Session,
    listing_id: uuid.UUID,
    data_mode: DataMode,
    fingerprint: str,
    config: FutureConfig,
) -> FutureAssessment | None:
    return session.scalar(
        select(FutureAssessment).where(
            FutureAssessment.listing_id == listing_id,
            FutureAssessment.data_mode == data_mode.value,
            FutureAssessment.input_fingerprint == fingerprint,
            FutureAssessment.future_model_version == config.future_model_version,
            FutureAssessment.scenario_model_version == config.scenario_model_version,
            FutureAssessment.configuration_version == config.configuration_version,
        )
    )


def _model_from_evaluation(
    valuation: ValuationResult, evaluation: FutureEvaluation
) -> FutureAssessment:
    return FutureAssessment(
        listing_id=evaluation.listing_id,
        data_mode=evaluation.data_mode,
        future_assessment_version=evaluation.future_assessment_version,
        input_fingerprint=evaluation.input_fingerprint,
        future_score=evaluation.future_score,
        future_score_coverage=evaluation.future_score_coverage,
        obsolescence_risk=evaluation.obsolescence_risk,
        obsolescence_coverage=evaluation.obsolescence_coverage,
        structural_alpha=evaluation.structural_alpha,
        quality_value_quadrant=evaluation.quality_value_quadrant,
        relative_outlook=evaluation.relative_outlook,
        confidence=evaluation.confidence,
        confidence_score=evaluation.confidence_score,
        calibration_state=evaluation.calibration_state,
        scenarios=[_json_safe(asdict(item)) for item in evaluation.scenarios],
        factor_breakdown=[_factor_dict(item) for item in evaluation.factors],
        risk_breakdown=_json_safe(evaluation.obsolescence_components),
        structural_components=_json_safe(evaluation.structural_components),
        warnings=list(evaluation.warnings),
        missing_inputs=list(evaluation.missing_inputs),
        provenance=_json_safe(evaluation.provenance),
        fair_value_anchor=valuation.fair_value,
        value_score=valuation.value_score,
        future_model_version=evaluation.future_model_version,
        scenario_model_version=evaluation.scenario_model_version,
        valuation_version=evaluation.valuation_version,
        baseline_version=evaluation.baseline_version,
        configuration_version=evaluation.configuration_version,
        data_version=evaluation.data_version,
        data_timestamp=evaluation.data_timestamp,
        generated_at=evaluation.generated_at,
    )


def _factor_dict(value: FactorEvidence) -> dict[str, Any]:
    result = {key: _json_safe(item) for key, item in asdict(value).items()}
    result["delta"] = str(value.delta) if value.delta is not None else None
    return result


def _json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    if hasattr(value, "isoformat"):
        return value.isoformat()
    if hasattr(value, "as_tuple"):
        return str(value)
    return value


def _advisory_lock(session: Session, listing_id: uuid.UUID, data_mode: DataMode) -> None:
    if session.get_bind().dialect.name != "postgresql":
        return
    digest = hashlib.sha256(f"future:{data_mode.value}:{listing_id}".encode()).digest()
    lock_key = int.from_bytes(digest[:8], byteorder="big", signed=True)
    session.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": lock_key})

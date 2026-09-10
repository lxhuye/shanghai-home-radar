from __future__ import annotations

import hashlib
import json
from dataclasses import asdict
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from home_radar_forecasting.confidence import (
    calculate_future_confidence,
    calibration_state,
)
from home_radar_forecasting.config import FutureConfig
from home_radar_forecasting.domain import (
    CalibrationState,
    ConfidenceResult,
    FactorEvidence,
    FutureEvaluation,
    FutureInputs,
    ObsolescenceResult,
    ScenarioOutcome,
    StructuralAlphaResult,
    WeightedScoreResult,
)
from home_radar_forecasting.factors import build_future_factors
from home_radar_forecasting.obsolescence import calculate_obsolescence_risk
from home_radar_forecasting.scenarios import generate_scenarios
from home_radar_forecasting.scoring import (
    calculate_future_score,
    quality_value_quadrant,
)
from home_radar_forecasting.structural import calculate_structural_alpha
from home_radar_forecasting.warnings import future_warnings


class DeterministicFutureEngine:
    def __init__(self, config: FutureConfig) -> None:
        self.config = config

    def evaluate(
        self,
        inputs: FutureInputs,
        as_of: datetime,
        *,
        input_fingerprint: str | None = None,
    ) -> FutureEvaluation:
        _validate_inputs(inputs, as_of)
        factors = build_future_factors(inputs, as_of, self.config)
        future_score = calculate_future_score(factors, self.config.future_score)
        obsolescence = calculate_obsolescence_risk(
            inputs.property, factors, as_of, self.config.obsolescence_risk
        )
        alpha = calculate_structural_alpha(
            factors, inputs.hierarchical_components, self.config.structural_alpha
        )
        state = calibration_state(inputs, self.config)
        confidence = calculate_future_confidence(
            inputs, factors, future_score.coverage, as_of, state, self.config
        )
        scenarios = generate_scenarios(
            inputs.property.fair_value,
            alpha.index,
            confidence.level,
            self.config.scenario_engine,
        )
        warnings = future_warnings(
            factors,
            obsolescence.score,
            confidence.score,
            state,
            inputs.property.data_mode,
            self.config.warnings,
        )
        fingerprint = input_fingerprint or build_future_input_fingerprint(
            inputs, factors, self.config, as_of
        )
        return _evaluation(
            inputs,
            factors,
            future_score,
            obsolescence,
            alpha,
            confidence,
            scenarios,
            state,
            warnings,
            fingerprint,
            self.config,
        )


def build_future_input_fingerprint(
    inputs: FutureInputs,
    factors: tuple[FactorEvidence, ...],
    config: FutureConfig,
    as_of: datetime,
) -> str:
    payload = {
        "inputs": _json_safe(asdict(inputs)),
        "resolved_factors": [_json_safe(asdict(factor)) for factor in factors],
        "as_of": as_of.isoformat(),
        "versions": {
            "future": config.future_model_version,
            "scenario": config.scenario_model_version,
            "configuration": config.configuration_version,
        },
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def _evaluation(
    inputs: FutureInputs,
    factors: tuple[FactorEvidence, ...],
    future_score: WeightedScoreResult,
    obsolescence: ObsolescenceResult,
    alpha: StructuralAlphaResult,
    confidence: ConfidenceResult,
    scenarios: tuple[ScenarioOutcome, ...],
    state: CalibrationState,
    warnings: tuple[str, ...],
    fingerprint: str,
    config: FutureConfig,
) -> FutureEvaluation:
    property_ = inputs.property
    quadrant = quality_value_quadrant(
        property_.value_score,
        future_score.score,
        high_value_score=config.quality_value_quadrants.high_value_score,
        high_future_score=config.quality_value_quadrants.high_future_score,
    )
    missing = tuple(
        sorted(
            set(future_score.missing_factors)
            | set(obsolescence.missing_components)
            | set(alpha.missing_components)
        )
    )
    generated_at = datetime.now(UTC)
    return FutureEvaluation(
        listing_id=property_.listing_id,
        data_mode=property_.data_mode,
        data_notice=_data_notice(property_.data_mode),
        recommendation_status="research_uncalibrated"
        if state != "calibrated"
        else "calibrated_relative_outlook",
        relative_outlook=_relative_outlook(future_score.score, obsolescence.score, alpha.index),
        investment_conclusion=None,
        future_score=future_score.score,
        future_score_coverage=future_score.coverage,
        obsolescence_risk=obsolescence.score,
        obsolescence_coverage=obsolescence.coverage,
        structural_alpha=alpha.index,
        quality_value_quadrant=quadrant,
        scenarios=scenarios,
        factors=factors,
        confidence=confidence.level,
        confidence_score=confidence.score,
        calibration_state=state,
        warnings=warnings,
        missing_inputs=missing,
        score_components={
            "contributions": future_score.contributions,
            "missing": list(future_score.missing_factors),
        },
        obsolescence_components={
            "scores": obsolescence.component_scores,
            "contributions": obsolescence.contributions,
            "missing": list(obsolescence.missing_components),
        },
        structural_components={
            "contributions": alpha.components,
            "missing": list(alpha.missing_components),
        },
        provenance=_provenance(inputs, confidence.components),
        future_model_version=config.future_model_version,
        scenario_model_version=config.scenario_model_version,
        valuation_version=property_.valuation_version,
        baseline_version=property_.baseline_version,
        configuration_version=config.configuration_version,
        data_version=inputs.data_version,
        data_timestamp=inputs.data_timestamp,
        input_fingerprint=fingerprint,
        future_assessment_version=f"p5-{fingerprint[:32]}",
        generated_at=generated_at,
    )


def _validate_inputs(inputs: FutureInputs, as_of: datetime) -> None:
    if as_of.tzinfo is None:
        raise ValueError("as_of must be timezone-aware")
    if inputs.property.fair_value <= 0:
        raise ValueError("P4 Fair Value anchor must be positive")
    modes = {factor.data_mode for factor in inputs.factors}
    if modes and modes != {inputs.property.data_mode}:
        raise ValueError("future factor data modes must match the property data mode")
    if inputs.data_timestamp is not None and inputs.data_timestamp > as_of:
        raise ValueError("future inputs cannot contain look-ahead data")


def _relative_outlook(
    future_score: Decimal | None,
    obsolescence: Decimal | None,
    alpha: Decimal,
) -> str:
    if future_score is None or obsolescence is None:
        return "INSUFFICIENT"
    if obsolescence >= Decimal("70") or future_score < Decimal("45"):
        return "STRUCTURALLY_WEAK"
    if obsolescence >= Decimal("55"):
        return "CAUTIOUS"
    if future_score >= Decimal("75") and alpha > 0:
        return "STRUCTURALLY_POSITIVE"
    return "NEUTRAL"


def _provenance(inputs: FutureInputs, confidence: dict[str, Decimal]) -> dict[str, Any]:
    return {
        "engine": "deterministic_configuration_driven",
        "llm_used_for_forecast": False,
        "fair_value_anchor_source": "p4_valuation",
        "asking_price_used_as_forecast_anchor": False,
        "market_beta_separate_from_structural_alpha": True,
        "investment_recommendation_emitted": False,
        "confidence_components": confidence,
        "live_gates": _json_safe(asdict(inputs.calibration)),
    }


def _data_notice(data_mode: str) -> str:
    if data_mode == "live":
        return "LIVE evidence; future output remains a relative outlook, not investment advice."
    if data_mode == "demo":
        return "DEMO synthetic evidence; never present this as a live Shanghai forecast."
    return "SAMPLE evidence; this research output is not a live Shanghai forecast."


def _json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    if isinstance(value, (Decimal, datetime)):
        return str(value)
    if hasattr(value, "hex"):
        return str(value)
    return value

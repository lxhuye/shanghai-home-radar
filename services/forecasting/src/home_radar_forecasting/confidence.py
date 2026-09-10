from __future__ import annotations

from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal

from home_radar_forecasting.config import FutureConfig
from home_radar_forecasting.domain import (
    CalibrationState,
    ConfidenceLevel,
    ConfidenceResult,
    FactorEvidence,
    FutureInputs,
)

LEVEL_ORDER: tuple[ConfidenceLevel, ...] = (
    "insufficient",
    "low",
    "medium",
    "high",
)


def calibration_state(inputs: FutureInputs, config: FutureConfig) -> CalibrationState:
    evidence = inputs.calibration
    live_gates = (
        inputs.property.data_mode == "live"
        and evidence.transaction_calibration_available
        and evidence.employment_coverage_sufficient
        and evidence.supply_coverage_sufficient
        and evidence.live_isolation_verified
    )
    if (
        live_gates
        and evidence.verified_case_count >= config.calibration.calibrated_minimum_verified_cases
    ):
        return "calibrated"
    if (
        evidence.transaction_calibration_available
        and evidence.verified_case_count >= config.calibration.partial_minimum_verified_cases
    ):
        return "partially_calibrated"
    return "uncalibrated"


def calculate_future_confidence(
    inputs: FutureInputs,
    factors: tuple[FactorEvidence, ...],
    future_score_coverage: Decimal,
    as_of: datetime,
    state: CalibrationState,
    config: FutureConfig,
) -> ConfidenceResult:
    components = _confidence_components(inputs, factors, future_score_coverage, as_of, config)
    score = sum(
        (components[name] * weight for name, weight in config.confidence.weights.items()),
        Decimal(),
    )
    level = _classified(score, config)
    if future_score_coverage < config.future_score.minimum_coverage:
        level = "insufficient"
    if inputs.property.data_mode != "live":
        level = _capped(level, config.confidence.non_live_maximum)
    if state == "uncalibrated":
        level = _capped(level, config.confidence.uncalibrated_maximum)
    return ConfidenceResult(
        score=_rounded(score),
        level=level,
        components={name: _rounded(value) for name, value in components.items()},
    )


def _confidence_components(
    inputs: FutureInputs,
    factors: tuple[FactorEvidence, ...],
    coverage: Decimal,
    as_of: datetime,
    config: FutureConfig,
) -> dict[str, Decimal]:
    by_name = {factor.factor: factor for factor in factors}
    available = [factor for factor in factors if factor.future_score is not None]
    authority = _average([factor.confidence for factor in available])
    freshness = _average(
        [
            _freshness(factor.source_timestamp, as_of, config.confidence.freshness_half_life_days)
            for factor in available
        ]
    )
    planning = _factor_quality(by_name.get("planning_realization"))
    return {
        "data_coverage": coverage,
        "data_freshness": freshness,
        "source_authority": authority,
        "planning_certainty": planning,
        "employment_data_quality": _factor_confidence(by_name.get("employment_accessibility")),
        "supply_data_quality": _factor_confidence(by_name.get("supply_scarcity")),
        "rental_data_quality": _factor_confidence(by_name.get("rental_demand")),
        "transaction_calibration": Decimal(
            int(inputs.calibration.transaction_calibration_available)
        ),
        "historical_continuity": inputs.calibration.historical_continuity_score,
    }


def _freshness(timestamp: datetime | None, as_of: datetime, half_life: int) -> Decimal:
    if timestamp is None or timestamp > as_of:
        return Decimal("0")
    age_days = Decimal((as_of - timestamp).days)
    half = Decimal(half_life)
    return half / (half + age_days)


def _factor_quality(factor: FactorEvidence | None) -> Decimal:
    if factor is None or factor.future_score is None:
        return Decimal("0")
    return factor.confidence * factor.future_score / Decimal("100")


def _factor_confidence(factor: FactorEvidence | None) -> Decimal:
    if factor is None or factor.future_score is None:
        return Decimal("0")
    return factor.confidence


def _average(values: list[Decimal]) -> Decimal:
    if not values:
        return Decimal("0")
    return sum(values, Decimal()) / Decimal(len(values))


def _classified(score: Decimal, config: FutureConfig) -> ConfidenceLevel:
    if score >= config.confidence.thresholds.high:
        return "high"
    if score >= config.confidence.thresholds.medium:
        return "medium"
    if score >= config.confidence.thresholds.low:
        return "low"
    return "insufficient"


def _capped(level: ConfidenceLevel, maximum: str) -> ConfidenceLevel:
    maximum_level = maximum if maximum in LEVEL_ORDER else "low"
    return LEVEL_ORDER[min(LEVEL_ORDER.index(level), LEVEL_ORDER.index(maximum_level))]


def _rounded(value: Decimal) -> Decimal:
    return value.quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP)

from __future__ import annotations

from decimal import Decimal
from functools import lru_cache
from pathlib import Path

from home_radar_shared.config import load_yaml_config
from pydantic import BaseModel, ConfigDict, Field, model_validator


class DistanceScorePoint(BaseModel):
    model_config = ConfigDict(frozen=True)

    distance_km: Decimal = Field(ge=0)
    score: Decimal = Field(ge=0, le=100)


class AccessibilityConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    distance_score_points: tuple[DistanceScorePoint, ...]
    minimum_total_weight: Decimal = Field(default=Decimal("0.01"), gt=0)

    @model_validator(mode="after")
    def validate_curve(self) -> AccessibilityConfig:
        distances = [point.distance_km for point in self.distance_score_points]
        if (
            len(distances) < 2
            or distances != sorted(distances)
            or len(set(distances)) != len(distances)
        ):
            raise ValueError("accessibility distance points must be unique and ascending")
        return self


class TransportAccessibilityConfig(AccessibilityConfig):
    maximum_planning_uplift: Decimal = Field(ge=0, le=100)


class WeightedScoreConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    weights: dict[str, Decimal]
    minimum_coverage: Decimal = Field(ge=0, le=1)

    @model_validator(mode="after")
    def validate_weights(self) -> WeightedScoreConfig:
        if any(weight < 0 for weight in self.weights.values()):
            raise ValueError("score weights cannot be negative")
        if sum(self.weights.values(), Decimal()) != Decimal("100"):
            raise ValueError("score weights must sum to 100")
        return self


class ObsolescenceConfig(WeightedScoreConfig):
    walk_up_risk: Decimal = Field(ge=0, le=100)
    elevator_unknown_risk: Decimal = Field(ge=0, le=100)
    building_age_risk_per_year: Decimal = Field(ge=0)
    building_age_grace_years: int = Field(ge=0)
    mainstream_area_min: Decimal = Field(gt=0)
    mainstream_area_max: Decimal = Field(gt=0)
    mainstream_total_price_min: Decimal = Field(gt=0)
    mainstream_total_price_max: Decimal = Field(gt=0)

    @model_validator(mode="after")
    def validate_ranges(self) -> ObsolescenceConfig:
        if self.mainstream_area_min >= self.mainstream_area_max:
            raise ValueError("mainstream area range is invalid")
        if self.mainstream_total_price_min >= self.mainstream_total_price_max:
            raise ValueError("mainstream total-price range is invalid")
        return self


class StructuralAlphaConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    component_weights: dict[str, Decimal]
    maximum_absolute_index: Decimal = Field(gt=0, le=100)


class ProbabilityConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    bear: Decimal = Field(ge=0, le=1)
    base: Decimal = Field(ge=0, le=1)
    bull: Decimal = Field(ge=0, le=1)

    @model_validator(mode="after")
    def validate_total(self) -> ProbabilityConfig:
        if self.bear + self.base + self.bull != Decimal("1"):
            raise ValueError("scenario probabilities must sum to one")
        return self


class ScenarioEngineConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    probabilities: ProbabilityConfig
    market_cagr_ranges: dict[int, dict[str, tuple[Decimal, Decimal]]]
    structural_alpha_maximum_annual_adjustment: Decimal = Field(ge=0, le=1)
    scenario_alpha_sensitivity: dict[str, Decimal]
    confidence_range_widening: dict[str, Decimal]

    @model_validator(mode="after")
    def validate_scenarios(self) -> ScenarioEngineConfig:
        if set(self.market_cagr_ranges) != {1, 3, 5}:
            raise ValueError("scenario horizons must be 1, 3 and 5 years")
        required = {"bear", "base", "bull"}
        for scenarios in self.market_cagr_ranges.values():
            if set(scenarios) != required:
                raise ValueError("each horizon must define bear, base and bull")
            if any(low > high for low, high in scenarios.values()):
                raise ValueError("scenario CAGR ranges must be ordered")
        if set(self.scenario_alpha_sensitivity) != required:
            raise ValueError("scenario alpha sensitivity is incomplete")
        return self


class ThresholdConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    high: Decimal = Field(ge=0, le=1)
    medium: Decimal = Field(ge=0, le=1)
    low: Decimal = Field(ge=0, le=1)

    @model_validator(mode="after")
    def validate_order(self) -> ThresholdConfig:
        if not self.high > self.medium > self.low:
            raise ValueError("confidence thresholds must be descending")
        return self


class ConfidenceConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    weights: dict[str, Decimal]
    thresholds: ThresholdConfig
    freshness_half_life_days: int = Field(gt=0)
    non_live_maximum: str
    uncalibrated_maximum: str

    @model_validator(mode="after")
    def validate_weights(self) -> ConfidenceConfig:
        if sum(self.weights.values(), Decimal()) != Decimal("1"):
            raise ValueError("confidence weights must sum to one")
        return self


class CalibrationConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    partial_minimum_verified_cases: int = Field(ge=1)
    calibrated_minimum_verified_cases: int = Field(ge=1)

    @model_validator(mode="after")
    def validate_counts(self) -> CalibrationConfig:
        if self.partial_minimum_verified_cases >= self.calibrated_minimum_verified_cases:
            raise ValueError("calibrated case threshold must exceed partial threshold")
        return self


class WarningConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    high_obsolescence_risk: Decimal = Field(ge=0, le=100)
    supply_shock_score: Decimal = Field(ge=0, le=100)
    planning_dependency_delta: Decimal = Field(ge=0, le=100)
    employment_access_weakness: Decimal = Field(ge=0, le=100)
    aging_product_score: Decimal = Field(ge=0, le=100)
    liquidity_decay_delta: Decimal = Field(ge=-100, le=0)
    low_rental_support: Decimal = Field(ge=0, le=100)
    low_forecast_confidence: Decimal = Field(ge=0, le=1)


class QuadrantConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    high_value_score: Decimal = Field(ge=0, le=100)
    high_future_score: Decimal = Field(ge=0, le=100)


class FutureConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    schema_version: int
    future_model_version: str
    scenario_model_version: str
    configuration_version: str
    planning_realization_weights: dict[str, Decimal]
    employment_accessibility: AccessibilityConfig
    transport_accessibility: TransportAccessibilityConfig
    future_score: WeightedScoreConfig
    obsolescence_risk: ObsolescenceConfig
    structural_alpha: StructuralAlphaConfig
    scenario_engine: ScenarioEngineConfig
    confidence: ConfidenceConfig
    calibration: CalibrationConfig
    warnings: WarningConfig
    quality_value_quadrants: QuadrantConfig

    @model_validator(mode="after")
    def validate_planning_weights(self) -> FutureConfig:
        required = {
            "current",
            "under_construction",
            "approved",
            "planned",
            "conceptual",
        }
        if set(self.planning_realization_weights) != required:
            raise ValueError("planning realization statuses are incomplete")
        if any(weight < 0 or weight > 1 for weight in self.planning_realization_weights.values()):
            raise ValueError("planning realization weights must be between zero and one")
        return self


@lru_cache
def load_future_config(
    path: Path = Path("config/forecasting.yaml"),
) -> FutureConfig:
    return FutureConfig.model_validate(load_yaml_config(path))

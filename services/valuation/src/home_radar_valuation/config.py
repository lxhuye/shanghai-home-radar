from __future__ import annotations

from decimal import Decimal
from functools import lru_cache
from itertools import pairwise
from pathlib import Path

from home_radar_shared.config import load_yaml_config
from pydantic import BaseModel, ConfigDict, Field, model_validator


class WeightedConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    weights: dict[str, Decimal]

    @model_validator(mode="after")
    def validate_weights(self) -> WeightedConfig:
        if any(value < 0 for value in self.weights.values()):
            raise ValueError("weights cannot be negative")
        if sum(self.weights.values(), Decimal()) != Decimal("1"):
            raise ValueError("weights must sum to one")
        return self


class ComparableConfig(WeightedConfig):
    windows_days: tuple[int, ...]
    minimum_comparables: int = Field(ge=1)
    target_comparables: int = Field(ge=1)
    maximum_comparables: int = Field(ge=1)
    tier_1_area_tolerance: Decimal = Field(gt=0, le=1)
    tier_2_area_tolerance: Decimal = Field(gt=0, le=1)
    tier_3_area_tolerance: Decimal = Field(gt=0, le=1)
    tier_1_building_age_years: int = Field(ge=0)
    tier_2_building_age_years: int = Field(ge=0)
    tier_multipliers: dict[str, Decimal]
    transaction_weight_multiplier: Decimal = Field(ge=1)
    baseline_weight: Decimal = Field(gt=0)
    strong_transaction_count: int = Field(ge=1)
    adjacent_layouts: dict[str, tuple[str, ...]]
    outlier_minimum_samples: int = Field(ge=4)
    outlier_mad_threshold: Decimal = Field(gt=0)
    unknown_similarity: Decimal = Field(ge=0, le=1)
    adjacent_layout_similarity: Decimal = Field(ge=0, le=1)
    cross_community_similarity: Decimal = Field(ge=0, le=1)
    building_age_similarity_horizon_years: int = Field(ge=1)

    @model_validator(mode="after")
    def validate_comparable_rules(self) -> ComparableConfig:
        if self.windows_days != (90, 180, 365):
            raise ValueError("P4 comparable windows must expand from 90 to 180 to 365 days")
        if not (
            self.tier_1_area_tolerance <= self.tier_2_area_tolerance <= self.tier_3_area_tolerance
        ):
            raise ValueError("area tolerances must widen by tier")
        if not self.minimum_comparables <= self.target_comparables <= self.maximum_comparables:
            raise ValueError("comparable counts must be ordered minimum <= target <= maximum")
        required = {"tier_1", "tier_2", "tier_3", "tier_4"}
        if set(self.tier_multipliers) != required:
            raise ValueError("tier multipliers must define tier_1 through tier_4")
        return self


class AdjustmentConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    maximum_absolute_total_rate: Decimal = Field(gt=0, le=1)
    floor_elevator_rates: dict[str, dict[str, Decimal]]
    orientation_rates: dict[str, Decimal]
    building_age_rate_per_year: Decimal = Field(ge=0)
    building_age_maximum_rate: Decimal = Field(ge=0, le=1)
    metro_bucket_rates: dict[str, Decimal]
    metro_distance_thresholds_m: tuple[int, int, int]
    metro_minimum_intra_community_difference_m: int = Field(ge=0)
    layout_quality_rates: dict[str, Decimal]
    road_noise_rates: dict[str, Decimal]
    base_uncertainty_rate: Decimal = Field(ge=0, le=1)
    missing_attribute_uncertainty_rate: Decimal = Field(ge=0, le=1)
    maximum_interval_rate: Decimal = Field(gt=0, le=1)

    @model_validator(mode="after")
    def validate_metro_thresholds(self) -> AdjustmentConfig:
        if tuple(sorted(self.metro_distance_thresholds_m)) != self.metro_distance_thresholds_m:
            raise ValueError("metro distance thresholds must be ascending")
        return self


class ConfidenceThresholds(BaseModel):
    model_config = ConfigDict(frozen=True)

    high: Decimal = Field(ge=0, le=1)
    medium: Decimal = Field(ge=0, le=1)
    low: Decimal = Field(ge=0, le=1)

    @model_validator(mode="after")
    def validate_order(self) -> ConfidenceThresholds:
        if not self.high > self.medium > self.low:
            raise ValueError("confidence thresholds must be high > medium > low")
        return self


class ValuationConfidenceConfig(WeightedConfig):
    thresholds: ConfidenceThresholds
    target_comparable_count: int = Field(ge=1)
    target_effective_count: Decimal = Field(gt=0)
    target_source_count: int = Field(ge=1)
    maximum_dispersion_rate: Decimal = Field(gt=0)
    maximum_missing_attributes: int = Field(ge=1)


class CurvePoint(BaseModel):
    model_config = ConfigDict(frozen=True)

    x: Decimal
    y: Decimal = Field(ge=0)


class ValueScoreConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    component_caps: dict[str, Decimal]
    price_edge_curve: tuple[CurvePoint, ...]
    decision_thresholds: dict[str, Decimal]
    accessibility_weights: dict[str, Decimal]
    quality_weights: dict[str, Decimal]
    metro_scores: dict[str, Decimal]
    metro_distance_thresholds_m: tuple[int, int, int]
    floor_elevator_scores: dict[str, dict[str, Decimal]]
    orientation_scores: dict[str, Decimal]
    layout_quality_scores: dict[str, Decimal]
    building_age_points_per_year: Decimal = Field(ge=0)
    building_age_minimum_score: Decimal = Field(ge=0, le=100)
    hard_defect_penalty: Decimal = Field(ge=0, le=100)
    liquidity_modifiers: dict[str, Decimal]
    unusual_property_types: tuple[str, ...]
    mainstream_total_price_min: Decimal = Field(gt=0)
    mainstream_total_price_max: Decimal = Field(gt=0)
    mainstream_area_min: Decimal = Field(gt=0)
    mainstream_area_max: Decimal = Field(gt=0)
    seller_signal: dict[str, Decimal]

    @model_validator(mode="after")
    def validate_score_config(self) -> ValueScoreConfig:
        required_components = {
            "price_edge",
            "liquidity",
            "employment_transit",
            "property_quality",
            "seller_signal",
        }
        if set(self.component_caps) != required_components:
            raise ValueError("P4 score components do not match the screening strategy")
        if sum(self.component_caps.values(), Decimal()) != Decimal("100"):
            raise ValueError("component caps must sum to 100")
        if len(self.price_edge_curve) < 2:
            raise ValueError("price edge curve needs at least two points")
        if any(current.x >= following.x for current, following in pairwise(self.price_edge_curve)):
            raise ValueError("price edge curve x values must be strictly increasing")
        if max(point.y for point in self.price_edge_curve) > self.component_caps["price_edge"]:
            raise ValueError("price edge curve exceeds its component cap")
        for weights in (self.accessibility_weights, self.quality_weights):
            if sum(weights.values(), Decimal()) != Decimal("1"):
                raise ValueError("subscore weights must sum to one")
        if tuple(sorted(self.metro_distance_thresholds_m)) != self.metro_distance_thresholds_m:
            raise ValueError("metro distance thresholds must be ascending")
        seller_weights = (
            "cut_count_weight",
            "cumulative_cut_weight",
            "dom_weight",
            "recency_weight",
            "relisting_weight",
        )
        if sum((self.seller_signal[name] for name in seller_weights), Decimal()) != Decimal("1"):
            raise ValueError("seller signal weights must sum to one")
        if set(self.decision_thresholds) != {"watch", "contact", "view", "attack"}:
            raise ValueError("decision thresholds must define watch/contact/view/attack")
        ordered = [
            self.decision_thresholds[name] for name in ("watch", "contact", "view", "attack")
        ]
        if ordered != sorted(ordered):
            raise ValueError("decision thresholds must be ascending")
        return self


class WarningConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    large_discount_rate: Decimal = Field(ge=0)
    low_liquidity_score: Decimal = Field(ge=0, le=100)
    insufficient_comparable_count: int = Field(ge=1)
    low_confidence_score: Decimal = Field(ge=0, le=1)
    trap_score_cap: Decimal = Field(ge=0, le=100)
    insufficient_confidence_score_cap: Decimal = Field(ge=0, le=100)
    abnormal_property_types: tuple[str, ...]


class ValuationConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    valuation_model_version: str
    scoring_model_version: str
    configuration_version: str
    comparables: ComparableConfig
    adjustments: AdjustmentConfig
    confidence: ValuationConfidenceConfig
    value_score: ValueScoreConfig
    warnings: WarningConfig


@lru_cache
def load_valuation_config(
    path: Path = Path("config/valuation.yaml"),
) -> ValuationConfig:
    return ValuationConfig.model_validate(load_yaml_config(path))

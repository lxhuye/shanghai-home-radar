from __future__ import annotations

from decimal import Decimal
from functools import lru_cache
from pathlib import Path

from home_radar_shared.config import load_yaml_config
from pydantic import BaseModel, ConfigDict, Field, model_validator


class AreaBucket(BaseModel):
    model_config = ConfigDict(frozen=True)

    name: str
    minimum: Decimal | None = None
    maximum: Decimal | None = None

    def contains(self, area_sqm: Decimal) -> bool:
        return (self.minimum is None or area_sqm >= self.minimum) and (
            self.maximum is None or area_sqm < self.maximum
        )


class OutlierConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    minimum_sample_size: int = Field(ge=4)
    robust_z_threshold: Decimal = Field(gt=0)
    iqr_multiplier: Decimal = Field(gt=0)


class ConfidenceThresholds(BaseModel):
    model_config = ConfigDict(frozen=True)

    high: Decimal = Field(ge=0, le=1)
    medium: Decimal = Field(ge=0, le=1)
    low: Decimal = Field(ge=0, le=1)

    @model_validator(mode="after")
    def validate_order(self) -> ConfidenceThresholds:
        if not self.high > self.medium > self.low:
            raise ValueError("confidence thresholds must be ordered high > medium > low")
        return self


class WeightedConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    weights: dict[str, Decimal]

    @model_validator(mode="after")
    def validate_weights(self) -> WeightedConfig:
        if any(weight < 0 for weight in self.weights.values()):
            raise ValueError("weights cannot be negative")
        if sum(self.weights.values(), Decimal()) != Decimal("1"):
            raise ValueError("weights must sum to one")
        return self


class ConfidenceConfig(WeightedConfig):
    absolute_minimum_samples: int = Field(ge=1)
    target_sample_size: int = Field(ge=1)
    target_source_count: int = Field(ge=1)
    thresholds: ConfidenceThresholds


class LiquidityConfig(WeightedConfig):
    minimum_available_weight: Decimal = Field(ge=0, le=1)
    active_inventory_target: int = Field(ge=1)
    new_listing_target: int = Field(ge=1)
    listing_exit_target: int = Field(ge=1)
    days_on_market_target: int = Field(ge=1)


class FallbackConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    community_minimum_samples: int = Field(ge=1)
    submarket_minimum_samples: int = Field(ge=1)
    district_minimum_samples: int = Field(ge=1)
    prior_strength: int = Field(ge=1)


class TargetArea(BaseModel):
    model_config = ConfigDict(frozen=True)

    district: str
    submarkets: tuple[str, ...] = ()


class MarketBaselineConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    calculation_version: str
    configuration_version: str
    windows_days: tuple[int, ...]
    target_areas: tuple[TargetArea, ...]
    area_buckets: tuple[AreaBucket, ...]
    outliers: OutlierConfig
    confidence: ConfidenceConfig
    liquidity: LiquidityConfig
    fallback: FallbackConfig

    @model_validator(mode="after")
    def validate_dimensions(self) -> MarketBaselineConfig:
        if set(self.windows_days) != {30, 90, 180, 365}:
            raise ValueError("P3 requires exactly the 30, 90, 180, and 365 day windows")
        names = [bucket.name for bucket in self.area_buckets]
        if len(names) != len(set(names)):
            raise ValueError("area bucket names must be unique")
        return self

    def area_bucket_for(self, area_sqm: Decimal) -> str:
        for bucket in self.area_buckets:
            if bucket.contains(area_sqm):
                return bucket.name
        raise ValueError(f"area is not covered by configuration: {area_sqm}")

    @staticmethod
    def layout_for(bedrooms: int | None) -> str | None:
        if bedrooms is None:
            return None
        if bedrooms >= 3:
            return "3BR+"
        if bedrooms <= 1:
            return "1BR"
        return "2BR"


@lru_cache
def load_market_config(path: Path = Path("config/market_baseline.yaml")) -> MarketBaselineConfig:
    return MarketBaselineConfig.model_validate(load_yaml_config(path))

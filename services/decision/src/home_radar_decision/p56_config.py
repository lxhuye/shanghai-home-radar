from __future__ import annotations

from decimal import Decimal
from functools import lru_cache
from pathlib import Path

from home_radar_shared.config import load_yaml_config
from pydantic import BaseModel, ConfigDict, Field, model_validator


class ValidationDatasetConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    target_sample_size: int = Field(ge=1)
    minimum_sample_size: int = Field(ge=1)
    maximum_sample_size: int = Field(ge=1)
    minimum_price_rmb: Decimal = Field(gt=0)
    maximum_price_rmb: Decimal = Field(gt=0)
    required_geography_buckets: tuple[str, ...]
    minimum_distinct_archetypes: int = Field(ge=1)
    maximum_single_archetype_share: Decimal = Field(gt=0, le=1)

    @model_validator(mode="after")
    def validate_ranges(self) -> ValidationDatasetConfig:
        if not self.minimum_sample_size <= self.target_sample_size <= self.maximum_sample_size:
            raise ValueError("target sample size must be within the configured range")
        if self.minimum_price_rmb >= self.maximum_price_rmb:
            raise ValueError("validation price range is invalid")
        if len(set(self.required_geography_buckets)) != len(self.required_geography_buckets):
            raise ValueError("required geography buckets must be unique")
        return self


class ValidationGateConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    precision_at_5: Decimal = Field(ge=0, le=1)
    precision_at_10: Decimal = Field(ge=0, le=1)
    human_acceptance_at_10: Decimal = Field(ge=0, le=1)
    maximum_value_traps_at_10: int = Field(ge=0)
    human_view_recall: Decimal = Field(ge=0, le=1)
    maximum_why_ranked_major_contradiction_rate: Decimal = Field(ge=0, le=1)
    maximum_regret_at_5: int = Field(ge=0, le=5)


class RealWorldValidationConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    schema_version: int
    protocol_version: str
    configuration_version: str
    dataset: ValidationDatasetConfig
    gate: ValidationGateConfig


@lru_cache
def load_real_world_validation_config(
    path: Path = Path("config/validation.yaml"),
) -> RealWorldValidationConfig:
    return RealWorldValidationConfig.model_validate(load_yaml_config(path))

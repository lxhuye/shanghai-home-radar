from __future__ import annotations

from decimal import Decimal
from functools import lru_cache
from pathlib import Path

from home_radar_shared.config import load_yaml_config
from pydantic import BaseModel, ConfigDict, Field, model_validator

CONFIDENCE_LEVELS = ("insufficient", "low", "medium", "high")


class ClassificationConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    high_value_score: Decimal = Field(ge=0, le=100)
    high_future_score: Decimal = Field(ge=0, le=100)
    minimum_future_coverage: Decimal = Field(ge=0, le=1)
    minimum_obsolescence_coverage: Decimal = Field(ge=0, le=1)


class EligibilityConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    minimum_confidence: str
    maximum_obsolescence_risk: Decimal = Field(ge=0, le=100)
    hard_risk_warnings: tuple[str, ...]

    @model_validator(mode="after")
    def validate_confidence(self) -> EligibilityConfig:
        if self.minimum_confidence not in CONFIDENCE_LEVELS:
            raise ValueError("minimum confidence is invalid")
        return self


class WorkflowConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    attack_enabled: bool
    view_minimum_value_score: Decimal = Field(ge=0, le=100)
    view_minimum_future_score: Decimal = Field(ge=0, le=100)
    view_minimum_liquidity_score: Decimal = Field(ge=0, le=100)
    view_maximum_obsolescence_risk: Decimal = Field(ge=0, le=100)
    contact_minimum_liquidity_score: Decimal = Field(ge=0, le=100)

    @model_validator(mode="after")
    def disallow_attack(self) -> WorkflowConfig:
        if self.attack_enabled:
            raise ValueError("ATTACK is disabled until live transaction backtesting passes")
        return self


class RankingConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    classification_priority: dict[str, int]
    confidence_priority: dict[str, int]

    @model_validator(mode="after")
    def validate_priorities(self) -> RankingConfig:
        if set(self.classification_priority) != {
            "QUALITY_AT_DISCOUNT",
            "GOOD_BUT_EXPENSIVE",
        }:
            raise ValueError("ranking classification priorities are incomplete")
        if set(self.confidence_priority) != set(CONFIDENCE_LEVELS):
            raise ValueError("ranking confidence priorities are incomplete")
        return self


class ExplanationConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    high_liquidity_score: Decimal = Field(ge=0, le=100)
    positive_structural_alpha: Decimal = Field(ge=-100, le=100)
    maximum_positive_reasons: int = Field(ge=1, le=10)
    maximum_negative_reasons: int = Field(ge=1, le=10)


class ValidationConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    target_sample_size: int = Field(ge=1)
    accepted_labels: tuple[str, ...]
    strict_positive_label: str

    @model_validator(mode="after")
    def validate_labels(self) -> ValidationConfig:
        if self.strict_positive_label not in self.accepted_labels:
            raise ValueError("strict positive label must also be accepted")
        return self


class DecisionConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    schema_version: int
    decision_model_version: str
    configuration_version: str
    classification: ClassificationConfig
    eligibility: EligibilityConfig
    workflow: WorkflowConfig
    ranking: RankingConfig
    explanations: ExplanationConfig
    validation: ValidationConfig


@lru_cache
def load_decision_config(path: Path = Path("config/decision.yaml")) -> DecisionConfig:
    return DecisionConfig.model_validate(load_yaml_config(path))

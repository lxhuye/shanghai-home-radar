from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, Field


class FutureFactorRead(BaseModel):
    factor: str
    current_score: Decimal | None = Field(default=None, ge=0, le=100)
    future_score: Decimal | None = Field(default=None, ge=0, le=100)
    delta: Decimal | None
    confidence: Decimal = Field(ge=0, le=1)
    source: str
    source_timestamp: datetime | None
    data_mode: str
    explanation: str
    metadata: dict[str, Any]


class FutureScenarioRead(BaseModel):
    horizon_years: int
    scenario: str
    price_range_low: Decimal
    price_range_high: Decimal
    nominal_return_low: Decimal
    nominal_return_high: Decimal
    cagr_low: Decimal
    cagr_high: Decimal
    probability: Decimal
    confidence: str
    market_beta_cagr_low: Decimal
    market_beta_cagr_high: Decimal
    structural_alpha_annual_adjustment: Decimal


class FutureRead(BaseModel):
    listing_id: uuid.UUID
    data_mode: str
    data_notice: str
    recommendation_status: str
    cache_hit: bool = False
    relative_outlook: str
    investment_conclusion: None = None
    future_score: Decimal | None
    future_score_coverage: Decimal
    obsolescence_risk: Decimal | None
    obsolescence_coverage: Decimal
    structural_alpha: Decimal
    quality_value_quadrant: str | None
    fair_value_anchor: Decimal
    value_score: Decimal
    scenarios: list[FutureScenarioRead]
    factors: list[FutureFactorRead]
    confidence: str
    confidence_score: Decimal
    calibration_state: str
    warnings: list[str]
    missing_inputs: list[str]
    risk_breakdown: dict[str, Any]
    structural_components: dict[str, Any]
    provenance: dict[str, Any]
    future_model_version: str
    scenario_model_version: str
    valuation_version: str
    baseline_version: str
    configuration_version: str
    data_version: str
    data_timestamp: datetime | None
    input_fingerprint: str
    future_assessment_version: str
    generated_at: datetime


class FutureFactorPage(BaseModel):
    listing_id: uuid.UUID
    future_assessment_version: str
    data_mode: str
    factors: list[FutureFactorRead]
    confidence: str
    calibration_state: str


class FutureScenarioPage(BaseModel):
    listing_id: uuid.UUID
    future_assessment_version: str
    data_mode: str
    fair_value_anchor: Decimal
    structural_alpha: Decimal
    scenarios: list[FutureScenarioRead]
    confidence: str
    calibration_state: str


class FutureRiskRead(BaseModel):
    listing_id: uuid.UUID
    future_assessment_version: str
    data_mode: str
    obsolescence_risk: Decimal | None
    obsolescence_coverage: Decimal
    risk_breakdown: dict[str, Any]
    warnings: list[str]
    confidence: str
    calibration_state: str

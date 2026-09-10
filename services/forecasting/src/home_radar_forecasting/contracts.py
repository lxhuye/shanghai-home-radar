from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Protocol

from pydantic import BaseModel, Field, model_validator

from home_radar_forecasting.domain import FutureEvaluation, FutureInputs


class FutureFactorContract(BaseModel):
    factor: str
    current_score: Decimal | None = Field(default=None, ge=0, le=100)
    future_score: Decimal | None = Field(default=None, ge=0, le=100)
    delta: Decimal | None
    confidence: Decimal = Field(ge=0, le=1)
    source: str
    source_timestamp: datetime | None
    data_mode: str
    explanation: str


class ScenarioContract(BaseModel):
    horizon_years: int = Field(ge=1)
    scenario: str
    price_range_low: Decimal = Field(gt=0)
    price_range_high: Decimal = Field(gt=0)
    nominal_return_low: Decimal
    nominal_return_high: Decimal
    cagr_low: Decimal
    cagr_high: Decimal
    probability: Decimal = Field(ge=0, le=1)
    confidence: str

    @model_validator(mode="after")
    def validate_ranges(self) -> ScenarioContract:
        if self.price_range_low > self.price_range_high:
            raise ValueError("scenario price range must be ordered")
        if self.nominal_return_low > self.nominal_return_high:
            raise ValueError("scenario return range must be ordered")
        return self


class ForecastRequest(BaseModel):
    listing_id: uuid.UUID
    as_of: datetime | None = None


class FutureAssessmentContract(BaseModel):
    listing_id: uuid.UUID
    data_mode: str
    future_score: Decimal | None = Field(default=None, ge=0, le=100)
    obsolescence_risk: Decimal | None = Field(default=None, ge=0, le=100)
    structural_alpha: Decimal = Field(ge=-100, le=100)
    scenarios: list[ScenarioContract]
    factors: list[FutureFactorContract]
    confidence: str
    calibration_state: str
    warnings: list[str]
    investment_conclusion: None = None


class ForecastEngine(Protocol):
    def evaluate(self, inputs: FutureInputs, as_of: datetime) -> FutureEvaluation: ...


HorizonScenario = ScenarioContract
ForecastResult = FutureAssessmentContract

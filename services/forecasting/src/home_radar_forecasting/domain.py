from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Any, Literal

ScenarioName = Literal["bear", "base", "bull"]
ConfidenceLevel = Literal["high", "medium", "low", "insufficient"]
CalibrationState = Literal["calibrated", "partially_calibrated", "uncalibrated"]

FUTURE_SCORE_FACTORS = (
    "employment_accessibility",
    "transport_accessibility",
    "supply_scarcity",
    "buyer_pool_depth",
    "community_competitiveness",
    "urban_renewal",
    "rental_demand",
    "public_services",
    "planning_realization",
)

ALL_FUTURE_FACTORS = FUTURE_SCORE_FACTORS + (
    "market_cycle",
    "building_aging",
    "product_obsolescence",
)


@dataclass(frozen=True)
class EmploymentCenterInput:
    name: str
    category: str
    longitude: Decimal
    latitude: Decimal
    current_employment_weight: Decimal
    future_employment_weight: Decimal
    source: str
    source_timestamp: datetime
    confidence: Decimal


@dataclass(frozen=True)
class FutureProjectInput:
    name: str
    project_type: str
    status: str
    longitude: Decimal | None
    latitude: Decimal | None
    expected_completion: datetime | None
    source: str
    source_date: datetime
    confidence: Decimal
    probability: Decimal | None = None


@dataclass(frozen=True)
class FactorEvidence:
    factor: str
    current_score: Decimal | None
    future_score: Decimal | None
    confidence: Decimal
    source: str
    source_timestamp: datetime | None
    data_mode: str
    explanation: str
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def delta(self) -> Decimal | None:
        if self.current_score is None or self.future_score is None:
            return None
        return self.future_score - self.current_score


@dataclass(frozen=True)
class FutureProperty:
    listing_id: uuid.UUID
    district: str
    submarket: str
    community: str
    area_sqm: Decimal
    fair_value: Decimal
    value_score: Decimal
    valuation_version: str
    baseline_version: str
    baseline_confidence: str
    valuation_confidence: str
    transaction_support: str
    data_mode: str
    longitude: Decimal | None = None
    latitude: Decimal | None = None
    year_built: int | None = None
    elevator: bool | None = None
    building_type: str | None = None
    bedrooms: int | None = None
    layout: str | None = None
    parking_quality: str | None = None
    property_management_quality: str | None = None
    maintenance_quality: str | None = None
    layout_mainstreamness: str | None = None


@dataclass(frozen=True)
class CalibrationEvidence:
    verified_case_count: int = 0
    transaction_calibration_available: bool = False
    employment_coverage_sufficient: bool = False
    supply_coverage_sufficient: bool = False
    live_isolation_verified: bool = False
    historical_continuity_score: Decimal = Decimal("0")


@dataclass(frozen=True)
class FutureInputs:
    property: FutureProperty
    factors: tuple[FactorEvidence, ...]
    employment_centers: tuple[EmploymentCenterInput, ...] = ()
    projects: tuple[FutureProjectInput, ...] = ()
    hierarchical_components: dict[str, Decimal | None] = field(default_factory=dict)
    calibration: CalibrationEvidence = field(default_factory=CalibrationEvidence)
    data_version: str = "unversioned"
    data_timestamp: datetime | None = None


@dataclass(frozen=True)
class WeightedScoreResult:
    score: Decimal | None
    coverage: Decimal
    contributions: dict[str, Decimal]
    missing_factors: tuple[str, ...]


@dataclass(frozen=True)
class ObsolescenceResult:
    score: Decimal | None
    coverage: Decimal
    contributions: dict[str, Decimal]
    component_scores: dict[str, Decimal]
    missing_components: tuple[str, ...]


@dataclass(frozen=True)
class StructuralAlphaResult:
    index: Decimal
    components: dict[str, Decimal]
    missing_components: tuple[str, ...]


@dataclass(frozen=True)
class ScenarioOutcome:
    horizon_years: int
    scenario: ScenarioName
    price_range_low: Decimal
    price_range_high: Decimal
    nominal_return_low: Decimal
    nominal_return_high: Decimal
    cagr_low: Decimal
    cagr_high: Decimal
    probability: Decimal
    confidence: ConfidenceLevel
    market_beta_cagr_low: Decimal
    market_beta_cagr_high: Decimal
    structural_alpha_annual_adjustment: Decimal


@dataclass(frozen=True)
class ConfidenceResult:
    score: Decimal
    level: ConfidenceLevel
    components: dict[str, Decimal]


@dataclass(frozen=True)
class FutureEvaluation:
    listing_id: uuid.UUID
    data_mode: str
    data_notice: str
    recommendation_status: str
    relative_outlook: str
    investment_conclusion: None
    future_score: Decimal | None
    future_score_coverage: Decimal
    obsolescence_risk: Decimal | None
    obsolescence_coverage: Decimal
    structural_alpha: Decimal
    quality_value_quadrant: str | None
    scenarios: tuple[ScenarioOutcome, ...]
    factors: tuple[FactorEvidence, ...]
    confidence: ConfidenceLevel
    confidence_score: Decimal
    calibration_state: CalibrationState
    warnings: tuple[str, ...]
    missing_inputs: tuple[str, ...]
    score_components: dict[str, Any]
    obsolescence_components: dict[str, Any]
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

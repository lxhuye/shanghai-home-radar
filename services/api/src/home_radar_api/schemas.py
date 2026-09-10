from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator


class SnapshotRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    crawl_run_id: uuid.UUID | None
    snapshot_at: datetime
    total_price: Decimal
    unit_price: Decimal
    status: str
    raw_payload: dict[str, Any]


class EventRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    crawl_run_id: uuid.UUID | None
    event_type: str
    occurred_at: datetime
    previous_value: str | None
    current_value: str | None
    details: dict[str, Any]


class ListingRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    source: str
    source_listing_id: str
    source_url: str
    district: str
    submarket: str
    community: str
    longitude: Decimal | None
    latitude: Decimal | None
    total_price: Decimal
    unit_price: Decimal
    area_sqm: Decimal
    bedrooms: int | None
    living_rooms: int | None
    floor: str | None
    total_floors: int | None
    orientation: str | None
    year_built: int | None
    elevator: bool | None
    building_type: str | None
    first_seen_at: datetime
    last_seen_at: datetime
    status: str


class ListingDetail(ListingRead):
    snapshots: list[SnapshotRead]
    events: list[EventRead]


class ListingPage(BaseModel):
    items: list[ListingRead]
    total: int
    limit: int
    offset: int


class CrawlRunRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    source: str
    data_mode: str
    scope_key: str
    status: str
    completeness: str
    started_at: datetime
    finished_at: datetime | None
    raw_item_count: int
    normalized_item_count: int
    parse_error_count: int
    error_type: str | None
    error_message: str | None
    metadata: dict[str, Any] = Field(validation_alias="run_metadata")
    created_at: datetime


class CrawlRunPage(BaseModel):
    items: list[CrawlRunRead]
    total: int
    limit: int
    offset: int


class CrawlJobRead(BaseModel):
    run_id: uuid.UUID
    job_id: str
    queue: str
    source: str
    scope_key: str


class ListingHistoryRead(BaseModel):
    listing_id: uuid.UUID
    snapshots: list[SnapshotRead]
    events: list[EventRead]


class MarketBaselineRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    materialization_run_id: uuid.UUID
    as_of_date: date
    generated_at: datetime
    data_mode: str
    observation_type: str
    window_days: int
    level: str
    district: str | None
    submarket: str | None
    community: str | None
    area_bucket: str | None
    layout: str | None
    observation_count: int
    outlier_count: int
    effective_price_sample_count: int
    effective_unit_price_sample_count: int
    price_p10: Decimal | None
    price_p25: Decimal | None
    price_p50: Decimal | None
    price_p75: Decimal | None
    price_p90: Decimal | None
    unit_price_p10: Decimal | None
    unit_price_p25: Decimal | None
    unit_price_p50: Decimal | None
    unit_price_p75: Decimal | None
    unit_price_p90: Decimal | None
    active_inventory: int
    new_listings: int
    price_cut_count: int
    price_cut_ratio: Decimal | None
    median_initial_ask: Decimal | None
    median_current_ask: Decimal | None
    median_price_cut_pct: Decimal | None
    median_days_on_market: Decimal | None
    relisting_rate: Decimal | None
    missing_candidate_count: int
    inactive_count: int
    ask_price_change_30d: Decimal | None
    ask_price_change_90d: Decimal | None
    inventory_change_30d: Decimal | None
    inventory_change_90d: Decimal | None
    median_monthly_rent: Decimal | None
    rent_per_sqm: Decimal | None
    gross_rental_yield: Decimal | None
    rental_listing_liquidity: Decimal | None
    liquidity_score: Decimal | None
    liquidity_confidence: Decimal | None
    liquidity_components: dict[str, Any]
    confidence_level: str
    confidence_score: Decimal
    confidence_components: dict[str, Any]
    calculation_version: str
    configuration_version: str
    baseline_version: str
    provenance: dict[str, Any]


class MarketBaselinePage(BaseModel):
    available: bool
    data_mode: str
    data_notice: str
    baseline_label: str
    materialization_run_id: uuid.UUID | None
    items: list[MarketBaselineRead]
    total: int
    limit: int
    offset: int


class CommunityBaselineRead(BaseModel):
    available: bool
    data_mode: str
    data_notice: str
    baseline_label: str
    community_id: uuid.UUID
    observation_type: str
    window_days: int
    level_used: str
    confidence: str
    sample_count: int
    quantiles: dict[str, Decimal | None]
    baseline_versions: list[str]
    fallback_path: list[str]
    fallback_reason: str | None
    local_weight: Decimal | None


class LiquidityRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    baseline_version: str
    level: str
    district: str | None
    submarket: str | None
    community: str | None
    area_bucket: str | None
    layout: str | None
    window_days: int
    liquidity_score: Decimal | None
    liquidity_confidence: Decimal | None
    liquidity_components: dict[str, Any]


class LiquidityPage(BaseModel):
    available: bool
    data_mode: str
    data_notice: str
    baseline_label: str = "Listing Market Liquidity V1"
    materialization_run_id: uuid.UUID | None
    items: list[LiquidityRead]


class MarketDataQualityRead(BaseModel):
    data_mode: str
    data_notice: str
    latest_materialization_run_id: uuid.UUID | None
    crawl_runs: list[dict[str, Any]]
    observation_counts: dict[str, int]
    area_coverage: list[dict[str, Any]]
    latest_observation_at: datetime | None
    target_area_gaps: list[dict[str, str]]
    low_confidence_communities: list[dict[str, Any]]


class StructuredPriceHistoryInput(BaseModel):
    original_ask: Decimal = Field(gt=0)
    price_cut_count: int = Field(default=0, ge=0)
    days_since_last_cut: int | None = Field(default=None, ge=0)
    days_on_market: int = Field(default=0, ge=0)
    relisting_flag: bool = False


class ValuationTargetInput(BaseModel):
    district: str = Field(min_length=1, max_length=80)
    submarket: str = Field(min_length=1, max_length=120)
    community: str = Field(min_length=1, max_length=160)
    area_sqm: Decimal = Field(gt=0, le=1000)
    current_ask: Decimal = Field(gt=0)
    bedrooms: int | None = Field(default=None, ge=1, le=20)
    layout: str | None = Field(default=None, max_length=40)
    floor: str | None = Field(default=None, max_length=80)
    total_floors: int | None = Field(default=None, ge=1, le=200)
    orientation: str | None = Field(default=None, max_length=80)
    year_built: int | None = Field(default=None, ge=1800, le=2200)
    elevator: bool | None = None
    building_type: str | None = Field(default=None, max_length=80)
    metro_distance_m: int | None = Field(default=None, ge=0, le=100000)
    employment_accessibility: Decimal | None = Field(default=None, ge=0, le=100)
    mature_amenity_accessibility: Decimal | None = Field(default=None, ge=0, le=100)
    layout_quality: str = Field(default="unknown", max_length=40)
    road_noise_exposure: str = Field(default="unknown", max_length=40)
    hard_defects: tuple[str, ...] = ()
    hard_defects_known: bool = False
    documented_executable_price: Decimal | None = Field(default=None, gt=0)
    executable_price_evidence: str | None = Field(default=None, max_length=500)

    @model_validator(mode="after")
    def validate_executable_evidence(self) -> ValuationTargetInput:
        if self.documented_executable_price is not None and not self.executable_price_evidence:
            raise ValueError("documented executable price requires structured evidence")
        return self


class ValuationEvaluateRequest(BaseModel):
    target: ValuationTargetInput
    price_history: StructuredPriceHistoryInput | None = None
    as_of: datetime | None = None


class ValuationAdjustmentRead(BaseModel):
    factor: str
    rate: Decimal
    amount: Decimal
    reason: str
    evidence: str


class ValuationComparableRead(BaseModel):
    observation_id: uuid.UUID
    listing_id: uuid.UUID | None
    observation_type: str
    source: str
    observed_at: datetime
    tier: str
    similarity_score: Decimal
    weight: Decimal
    reason: list[str]
    area_sqm: Decimal
    raw_total_price: Decimal
    raw_unit_price: Decimal
    adjusted_unit_price: Decimal
    adjusted_value: Decimal
    total_adjustment_rate: Decimal
    adjustments: list[ValuationAdjustmentRead]


class ValuationRead(BaseModel):
    listing_id: uuid.UUID | None
    data_mode: str
    data_notice: str
    recommendation_status: str
    cache_hit: bool = False
    fair_value: Decimal
    fair_value_low: Decimal
    fair_value_high: Decimal
    valuation_basis: str
    valuation_confidence: str
    valuation_confidence_score: Decimal
    transaction_support: str
    current_ask: Decimal
    ask_discount_to_fair_value: Decimal
    estimated_executable_price: Decimal | None
    executable_discount_to_fair_value: Decimal | None
    value_score: Decimal
    decision: str
    comparable_count: int
    effective_comparable_count: Decimal
    baseline_level_used: str
    baseline_confidence: str
    baseline_version: str
    fallback_reason: str | None
    adjustments: list[ValuationAdjustmentRead]
    comparables: list[ValuationComparableRead]
    warnings: list[str]
    score_components: dict[str, Any]
    price_history: dict[str, Any]
    provenance: dict[str, Any]
    valuation_model_version: str
    scoring_model_version: str
    configuration_version: str
    valuation_version: str
    input_fingerprint: str
    generated_at: datetime
    baseline_generated_at: datetime | None


class ValuationComparablesRead(BaseModel):
    listing_id: uuid.UUID
    valuation_version: str
    comparable_count: int
    comparables: list[ValuationComparableRead]


class ValuationExplanationRead(BaseModel):
    listing_id: uuid.UUID
    valuation_version: str
    fair_value: Decimal
    fair_value_low: Decimal
    fair_value_high: Decimal
    baseline_level_used: str
    baseline_confidence: str
    fallback_reason: str | None
    adjustments: list[ValuationAdjustmentRead]
    warnings: list[str]
    provenance: dict[str, Any]

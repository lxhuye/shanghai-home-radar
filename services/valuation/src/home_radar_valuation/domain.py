from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Any


@dataclass(frozen=True)
class TargetProperty:
    listing_id: uuid.UUID | None
    district: str
    submarket: str
    community: str
    area_sqm: Decimal
    current_ask: Decimal
    bedrooms: int | None = None
    layout: str | None = None
    floor: str | None = None
    total_floors: int | None = None
    orientation: str | None = None
    year_built: int | None = None
    elevator: bool | None = None
    building_type: str | None = None
    metro_distance_m: int | None = None
    employment_accessibility: Decimal | None = None
    mature_amenity_accessibility: Decimal | None = None
    layout_quality: str = "unknown"
    road_noise_exposure: str = "unknown"
    hard_defects: tuple[str, ...] = ()
    hard_defects_known: bool = False
    documented_executable_price: Decimal | None = None
    executable_price_evidence: str | None = None
    data_mode: str = "sample"

    @property
    def resolved_layout(self) -> str | None:
        return self.layout or layout_from_bedrooms(self.bedrooms)


@dataclass(frozen=True)
class ComparableRecord:
    observation_id: uuid.UUID
    listing_id: uuid.UUID | None
    observation_type: str
    source: str
    source_record_id: str
    observed_at: datetime
    source_confidence: Decimal
    district: str | None
    submarket: str | None
    community: str | None
    area_sqm: Decimal
    total_price: Decimal
    unit_price: Decimal
    bedrooms: int | None = None
    layout: str | None = None
    floor: str | None = None
    total_floors: int | None = None
    orientation: str | None = None
    year_built: int | None = None
    elevator: bool | None = None
    building_type: str | None = None
    metro_distance_m: int | None = None
    layout_quality: str = "unknown"
    road_noise_exposure: str = "unknown"
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def resolved_layout(self) -> str | None:
        return self.layout or layout_from_bedrooms(self.bedrooms)

    @property
    def entity_key(self) -> str:
        if self.listing_id is not None:
            return f"{self.observation_type}:listing:{self.listing_id}"
        return f"{self.observation_type}:source:{self.source}:{self.source_record_id}"


@dataclass(frozen=True)
class SelectedComparable:
    record: ComparableRecord
    tier: str
    similarity_score: Decimal
    raw_weight: Decimal
    weight: Decimal
    freshness_days: int
    reasons: tuple[str, ...]


@dataclass(frozen=True)
class ComparableSelection:
    comparables: tuple[SelectedComparable, ...]
    window_days: int
    effective_count: Decimal
    fallback_reason: str | None


@dataclass(frozen=True)
class BaselineEvidence:
    observation_type: str
    level_used: str
    confidence: str
    sample_count: int
    unit_price_p25: Decimal | None
    unit_price_p50: Decimal | None
    unit_price_p75: Decimal | None
    price_p50: Decimal | None
    liquidity_score: Decimal | None
    baseline_versions: tuple[str, ...]
    fallback_path: tuple[str, ...]
    fallback_reason: str | None
    generated_at: datetime | None


@dataclass(frozen=True)
class ValuationAdjustment:
    factor: str
    rate: Decimal
    amount: Decimal
    reason: str
    evidence: str


@dataclass(frozen=True)
class AdjustedComparable:
    selected: SelectedComparable
    adjusted_unit_price: Decimal
    adjusted_value: Decimal
    total_adjustment_rate: Decimal
    adjustments: tuple[ValuationAdjustment, ...]
    outlier: bool = False


@dataclass(frozen=True)
class PriceHistory:
    original_ask: Decimal
    current_ask: Decimal
    absolute_reduction: Decimal
    percentage_reduction: Decimal
    price_cut_count: int
    days_since_last_cut: int | None
    days_on_market: int
    relisting_flag: bool


@dataclass(frozen=True)
class FairValueAggregation:
    fair_value: Decimal
    fair_value_low: Decimal
    fair_value_high: Decimal
    dispersion_rate: Decimal
    interval_rate: Decimal
    retained_comparables: tuple[AdjustedComparable, ...]
    baseline_included: bool


def layout_from_bedrooms(bedrooms: int | None) -> str | None:
    if bedrooms is None:
        return None
    if bedrooms <= 1:
        return "1BR"
    if bedrooms == 2:
        return "2BR"
    return "3BR+"


def normalized_orientation(value: str | None) -> str:
    if value is None:
        return "unknown"
    normalized = value.strip().lower().replace(" ", "").replace("-", "_")
    if "南北" in normalized or "southnorth" in normalized or "south_north" in normalized:
        return "south_north"
    mappings = (
        (("南", "south"), "south"),
        (("东", "east"), "east"),
        (("西", "west"), "west"),
        (("北", "north"), "north"),
        (("混", "mixed"), "mixed"),
    )
    for tokens, result in mappings:
        if any(token in normalized for token in tokens):
            return result
    return "unknown"


def floor_category(floor: str | None, total_floors: int | None) -> str:
    if floor is None:
        return "unknown"
    normalized = floor.strip().lower()
    if any(token in normalized for token in ("顶", "top")):
        return "top"
    if any(token in normalized for token in ("底", "ground")):
        return "ground"
    if any(token in normalized for token in ("低", "low")):
        return "low"
    if any(token in normalized for token in ("中", "middle", "mid")):
        return "middle"
    if any(token in normalized for token in ("高", "high")):
        return "high"
    match = re.search(r"\d+", normalized)
    if match is None:
        return "unknown"
    current = int(match.group())
    if current <= 1:
        return "ground"
    if total_floors is None or total_floors <= 0:
        return "unknown"
    if current >= total_floors:
        return "top"
    ratio = Decimal(current) / Decimal(total_floors)
    if ratio <= Decimal("0.33"):
        return "low"
    if ratio <= Decimal("0.67"):
        return "middle"
    return "high"


def building_segment(
    building_type: str | None, elevator: bool | None, total_floors: int | None
) -> str:
    normalized = (building_type or "").lower()
    if elevator is False or any(token in normalized for token in ("walk", "步梯")):
        return "walk_up"
    if (total_floors or 0) >= 12 or any(token in normalized for token in ("high", "高层")):
        return "high_rise"
    return "elevator"


def metro_bucket(
    distance_m: int | None, thresholds_m: tuple[int, int, int] = (500, 800, 1200)
) -> str:
    if distance_m is None:
        return "unknown"
    near, walkable, extended = thresholds_m
    if distance_m <= near:
        return "within_500m"
    if distance_m <= walkable:
        return "500_800m"
    if distance_m <= extended:
        return "800_1200m"
    return "over_1200m"

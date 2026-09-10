from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

from home_radar_models.enums import ValuationDecision

from home_radar_valuation.config import CurvePoint, ValueScoreConfig
from home_radar_valuation.domain import (
    PriceHistory,
    TargetProperty,
    building_segment,
    floor_category,
    metro_bucket,
    normalized_orientation,
)

ZERO = Decimal("0")
HUNDRED = Decimal("100")


@dataclass(frozen=True)
class ValueScoreResult:
    score: Decimal
    decision: str
    components: dict[str, dict[str, Decimal | bool | None]]


def calculate_value_score(
    target: TargetProperty,
    fair_value: Decimal,
    liquidity_score: Decimal | None,
    price_history: PriceHistory,
    local_median_year_built: int | None,
    config: ValueScoreConfig,
) -> ValueScoreResult:
    discount = (fair_value - target.current_ask) / fair_value
    price_edge_contribution = piecewise_linear(discount, config.price_edge_curve)
    liquidity_raw = _liquidity_score(target, liquidity_score, config)
    accessibility_raw, accessibility_coverage = _accessibility_score(target, config)
    quality_raw, quality_coverage = _quality_score(target, local_median_year_built, config)
    seller_raw = _seller_score(price_history, config)

    components: dict[str, dict[str, Decimal | bool | None]] = {
        "price_edge": {
            "raw": discount.quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP),
            "contribution": price_edge_contribution,
            "coverage": True,
        },
        "liquidity": {
            "raw": liquidity_raw,
            "contribution": _contribution(liquidity_raw, config.component_caps["liquidity"]),
            "coverage": liquidity_score is not None,
        },
        "employment_transit": {
            "raw": accessibility_raw,
            "contribution": _contribution(
                accessibility_raw, config.component_caps["employment_transit"]
            ),
            "coverage": accessibility_coverage,
        },
        "property_quality": {
            "raw": quality_raw,
            "contribution": _contribution(quality_raw, config.component_caps["property_quality"]),
            "coverage": quality_coverage,
        },
        "seller_signal": {
            "raw": seller_raw,
            "contribution": _contribution(seller_raw, config.component_caps["seller_signal"]),
            "coverage": True,
        },
    }
    score = sum(
        (
            component["contribution"]
            for component in components.values()
            if isinstance(component["contribution"], Decimal)
        ),
        ZERO,
    ).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return ValueScoreResult(
        score=score,
        decision=decision_for_score(score, config),
        components=components,
    )


def piecewise_linear(discount: Decimal, points: tuple[CurvePoint, ...]) -> Decimal:
    curve = list(points)
    if discount <= curve[0].x:
        return curve[0].y
    if discount >= curve[-1].x:
        return curve[-1].y
    for left, right in zip(curve, curve[1:], strict=False):
        if left.x <= discount <= right.x:
            ratio = (discount - left.x) / (right.x - left.x)
            return (left.y + ratio * (right.y - left.y)).quantize(
                Decimal("0.01"), rounding=ROUND_HALF_UP
            )
    return curve[-1].y


def decision_for_score(score: Decimal, config: ValueScoreConfig) -> str:
    result = ValuationDecision.PASS.value
    for name in ("watch", "contact", "view", "attack"):
        if score >= config.decision_thresholds[name]:
            result = name
    return result


def _liquidity_score(
    target: TargetProperty,
    baseline_score: Decimal | None,
    config: ValueScoreConfig,
) -> Decimal:
    if baseline_score is None:
        return ZERO
    score = baseline_score
    modifiers = config.liquidity_modifiers
    if config.mainstream_total_price_min <= target.current_ask <= config.mainstream_total_price_max:
        score += modifiers["mainstream_total_price"]
    if config.mainstream_area_min <= target.area_sqm <= config.mainstream_area_max:
        score += modifiers["mainstream_area"]
    if target.resolved_layout in {"1BR", "2BR", "3BR+"}:
        score += modifiers["mainstream_layout"]
    if floor_category(target.floor, target.total_floors) in {"ground", "top"}:
        score += modifiers["extreme_floor"]
    building_type = (target.building_type or "").lower()
    if any(token.lower() in building_type for token in config.unusual_property_types):
        score += modifiers["unusual_property"]
    return _clamp(score)


def _accessibility_score(
    target: TargetProperty, config: ValueScoreConfig
) -> tuple[Decimal, Decimal]:
    values: dict[str, Decimal | None] = {
        "metro": (
            config.metro_scores[
                metro_bucket(target.metro_distance_m, config.metro_distance_thresholds_m)
            ]
            if target.metro_distance_m is not None
            else None
        ),
        "employment": target.employment_accessibility,
        "mature_amenities": target.mature_amenity_accessibility,
    }
    return _available_weighted(values, config.accessibility_weights)


def _quality_score(
    target: TargetProperty,
    local_median_year_built: int | None,
    config: ValueScoreConfig,
) -> tuple[Decimal, Decimal]:
    floor = floor_category(target.floor, target.total_floors)
    floor_value = None
    if floor != "unknown" and target.elevator is not None:
        segment = building_segment(target.building_type, target.elevator, target.total_floors)
        floor_value = config.floor_elevator_scores[segment][floor]
    orientation = normalized_orientation(target.orientation)
    orientation_value = (
        config.orientation_scores.get(orientation) if orientation != "unknown" else None
    )
    age_value = None
    if target.year_built is not None and local_median_year_built is not None:
        older_years = max(0, local_median_year_built - target.year_built)
        age_value = max(
            config.building_age_minimum_score,
            HUNDRED - Decimal(older_years) * config.building_age_points_per_year,
        )
    layout_value = (
        config.layout_quality_scores.get(target.layout_quality)
        if target.layout_quality != "unknown"
        else None
    )
    defects_value = None
    if target.hard_defects_known:
        defects_value = max(
            ZERO,
            HUNDRED - Decimal(len(target.hard_defects)) * config.hard_defect_penalty,
        )
    values = {
        "floor_elevator": floor_value,
        "orientation": orientation_value,
        "building_age": age_value,
        "layout_quality": layout_value,
        "hard_defects": defects_value,
    }
    return _available_weighted(values, config.quality_weights)


def _seller_score(history: PriceHistory, config: ValueScoreConfig) -> Decimal:
    settings = config.seller_signal
    cut_count = min(
        HUNDRED,
        Decimal(history.price_cut_count) / settings["cut_count_target"] * HUNDRED,
    )
    cumulative = min(
        HUNDRED,
        history.percentage_reduction / settings["cumulative_cut_target"] * HUNDRED,
    )
    dom = min(HUNDRED, Decimal(history.days_on_market) / settings["long_dom_days"] * HUNDRED)
    recency = (
        HUNDRED
        if history.days_since_last_cut is not None
        and history.days_since_last_cut <= settings["recent_cut_days"]
        else ZERO
    )
    relisting = HUNDRED if history.relisting_flag else ZERO
    score = (
        cut_count * settings["cut_count_weight"]
        + cumulative * settings["cumulative_cut_weight"]
        + dom * settings["dom_weight"]
        + recency * settings["recency_weight"]
        + relisting * settings["relisting_weight"]
    )
    return _clamp(score)


def _available_weighted(
    values: dict[str, Decimal | None], weights: dict[str, Decimal]
) -> tuple[Decimal, Decimal]:
    available_weight = sum(
        (weights[name] for name, value in values.items() if value is not None), ZERO
    )
    if available_weight == 0:
        return ZERO, ZERO
    score = (
        sum(
            (value * weights[name] for name, value in values.items() if value is not None),
            ZERO,
        )
        / available_weight
    )
    return _clamp(score), available_weight


def _contribution(raw: Decimal, cap: Decimal) -> Decimal:
    return (raw / HUNDRED * cap).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def _clamp(value: Decimal) -> Decimal:
    return min(HUNDRED, max(ZERO, value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

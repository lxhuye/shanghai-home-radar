from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal

from home_radar_valuation.config import AdjustmentConfig
from home_radar_valuation.domain import (
    AdjustedComparable,
    SelectedComparable,
    TargetProperty,
    ValuationAdjustment,
    building_segment,
    floor_category,
    metro_bucket,
    normalized_orientation,
)

ZERO = Decimal("0")


def adjust_comparable(
    target: TargetProperty,
    comparable: SelectedComparable,
    config: AdjustmentConfig,
) -> AdjustedComparable:
    """Normalize one comparable to target residual features without location double count."""
    record = comparable.record
    rates: list[tuple[str, Decimal, str, str]] = []

    floor_rate = _floor_elevator_delta(target, comparable, config)
    if floor_rate is not None and floor_rate != ZERO:
        rates.append(
            (
                "floor_elevator_interaction",
                floor_rate,
                "combined floor and elevator residual; no additive double counting",
                "canonical listing features",
            )
        )

    orientation_rate = _rate_delta(
        config.orientation_rates,
        normalized_orientation(target.orientation),
        normalized_orientation(record.orientation),
        require_target=True,
    )
    if orientation_rate is not None and orientation_rate != ZERO:
        rates.append(
            (
                "orientation",
                orientation_rate,
                "target orientation relative to comparable orientation",
                "canonical listing features",
            )
        )

    if target.year_built is not None and record.year_built is not None:
        year_delta = target.year_built - record.year_built
        age_rate = _bounded(
            Decimal(year_delta) * config.building_age_rate_per_year,
            config.building_age_maximum_rate,
        )
        if age_rate != ZERO:
            rates.append(
                (
                    "building_age_relative",
                    age_rate,
                    f"target is {year_delta:+d} construction years from comparable",
                    "relative age, not a Shanghai-wide depreciation",
                )
            )

    metro_rate = _metro_delta(target, comparable, config)
    if metro_rate is not None and metro_rate != ZERO:
        rates.append(
            (
                "intra_community_metro",
                metro_rate,
                "meaningful metro-distance difference inside the same community",
                "community location premium remains in the P3 baseline",
            )
        )

    layout_rate = _rate_delta(
        config.layout_quality_rates,
        target.layout_quality,
        record.layout_quality,
        require_target=True,
    )
    if layout_rate is not None and layout_rate != ZERO:
        rates.append(
            (
                "layout_quality_residual",
                layout_rate,
                "explicit layout-quality classification difference",
                "stored deterministic classification",
            )
        )

    noise_rate = _rate_delta(
        config.road_noise_rates,
        target.road_noise_exposure,
        record.road_noise_exposure,
        require_target=True,
    )
    if noise_rate is not None and noise_rate != ZERO:
        rates.append(
            (
                "road_noise_residual",
                noise_rate,
                "explicit road/noise exposure difference",
                "stored structured evidence",
            )
        )

    raw_total = sum((rate for _, rate, _, _ in rates), ZERO)
    total_rate = _bounded(raw_total, config.maximum_absolute_total_rate)
    if raw_total != ZERO and total_rate != raw_total:
        scaling = total_rate / raw_total
        rates = [
            (factor, rate * scaling, reason, evidence) for factor, rate, reason, evidence in rates
        ]

    adjusted_unit = (record.unit_price * (Decimal("1") + total_rate)).quantize(
        Decimal("0.01"), rounding=ROUND_HALF_UP
    )
    adjusted_value = (adjusted_unit * target.area_sqm).quantize(
        Decimal("0.01"), rounding=ROUND_HALF_UP
    )
    adjustments = tuple(
        ValuationAdjustment(
            factor=factor,
            rate=rate.quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP),
            amount=(record.unit_price * target.area_sqm * rate).quantize(
                Decimal("0.01"), rounding=ROUND_HALF_UP
            ),
            reason=reason,
            evidence=evidence,
        )
        for factor, rate, reason, evidence in rates
    )
    return AdjustedComparable(
        selected=comparable,
        adjusted_unit_price=adjusted_unit,
        adjusted_value=adjusted_value,
        total_adjustment_rate=total_rate.quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP),
        adjustments=adjustments,
    )


def target_missing_attributes(target: TargetProperty) -> tuple[str, ...]:
    values = {
        "layout": target.resolved_layout,
        "floor": target.floor,
        "total_floors": target.total_floors,
        "orientation": target.orientation,
        "year_built": target.year_built,
        "elevator": target.elevator,
        "building_type": target.building_type,
        "metro_distance_m": target.metro_distance_m,
        "employment_accessibility": target.employment_accessibility,
        "mature_amenity_accessibility": target.mature_amenity_accessibility,
        "layout_quality": None if target.layout_quality == "unknown" else target.layout_quality,
        "road_noise_exposure": (
            None if target.road_noise_exposure == "unknown" else target.road_noise_exposure
        ),
    }
    return tuple(name for name, value in values.items() if value is None)


def _floor_elevator_delta(
    target: TargetProperty,
    comparable: SelectedComparable,
    config: AdjustmentConfig,
) -> Decimal | None:
    record = comparable.record
    target_floor = floor_category(target.floor, target.total_floors)
    comparable_floor = floor_category(record.floor, record.total_floors)
    if (
        target_floor == "unknown"
        or comparable_floor == "unknown"
        or target.elevator is None
        or record.elevator is None
    ):
        return None
    target_segment = building_segment(target.building_type, target.elevator, target.total_floors)
    comparable_segment = building_segment(
        record.building_type, record.elevator, record.total_floors
    )
    target_rate = config.floor_elevator_rates[target_segment][target_floor]
    comparable_rate = config.floor_elevator_rates[comparable_segment][comparable_floor]
    return target_rate - comparable_rate


def _metro_delta(
    target: TargetProperty,
    comparable: SelectedComparable,
    config: AdjustmentConfig,
) -> Decimal | None:
    record = comparable.record
    if (
        record.community != target.community
        or target.metro_distance_m is None
        or record.metro_distance_m is None
    ):
        return None
    if (
        abs(target.metro_distance_m - record.metro_distance_m)
        < config.metro_minimum_intra_community_difference_m
    ):
        return None
    return (
        config.metro_bucket_rates[
            metro_bucket(target.metro_distance_m, config.metro_distance_thresholds_m)
        ]
        - config.metro_bucket_rates[
            metro_bucket(record.metro_distance_m, config.metro_distance_thresholds_m)
        ]
    )


def _rate_delta(
    rates: dict[str, Decimal],
    target: str,
    comparable: str,
    *,
    require_target: bool,
) -> Decimal | None:
    if require_target and target == "unknown":
        return None
    if comparable == "unknown":
        return None
    return rates[target] - rates[comparable]


def _bounded(value: Decimal, maximum: Decimal) -> Decimal:
    return min(max(value, -maximum), maximum)

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal

from home_radar_valuation.config import AdjustmentConfig, ComparableConfig
from home_radar_valuation.domain import (
    AdjustedComparable,
    BaselineEvidence,
    FairValueAggregation,
    TargetProperty,
)

ZERO = Decimal("0")
ONE = Decimal("1")


class InsufficientValuationEvidenceError(ValueError):
    pass


def aggregate_fair_value(
    target: TargetProperty,
    comparables: tuple[AdjustedComparable, ...],
    baseline: BaselineEvidence | None,
    comparable_config: ComparableConfig,
    adjustment_config: AdjustmentConfig,
    missing_attribute_count: int,
) -> FairValueAggregation:
    retained = _remove_outliers(comparables, comparable_config)
    points: list[tuple[Decimal, Decimal]] = [
        (item.adjusted_value, item.selected.weight) for item in retained
    ]
    baseline_included = baseline is not None and (
        baseline.unit_price_p50 is not None or baseline.price_p50 is not None
    )
    if baseline_included and baseline is not None:
        baseline_value = (
            baseline.unit_price_p50 * target.area_sqm
            if baseline.unit_price_p50 is not None
            else baseline.price_p50
        )
        if baseline_value is not None:
            points.append((baseline_value, comparable_config.baseline_weight))
    if not points:
        raise InsufficientValuationEvidenceError("no usable comparable observation or P3 baseline")

    points = _normalized_points(points)
    central = weighted_quantile(points, Decimal("0.50"))
    p25 = weighted_quantile(points, Decimal("0.25"))
    p75 = weighted_quantile(points, Decimal("0.75"))
    dispersion = (p75 - p25) / central if central > 0 else ONE
    sample_ratio = min(
        ONE,
        Decimal(len(retained)) / Decimal(comparable_config.target_comparables),
    )
    uncertainty = (
        adjustment_config.base_uncertainty_rate
        + adjustment_config.missing_attribute_uncertainty_rate * Decimal(missing_attribute_count)
        + dispersion / Decimal("2")
        + adjustment_config.base_uncertainty_rate * (ONE - sample_ratio)
    )
    interval_rate = min(uncertainty, adjustment_config.maximum_interval_rate)
    low = min(p25, central * (ONE - interval_rate))
    high = max(p75, central * (ONE + interval_rate))
    if baseline is not None:
        if baseline.unit_price_p25 is not None:
            low = min(low, baseline.unit_price_p25 * target.area_sqm)
        if baseline.unit_price_p75 is not None:
            high = max(high, baseline.unit_price_p75 * target.area_sqm)
    return FairValueAggregation(
        fair_value=_money(central),
        fair_value_low=_money(low),
        fair_value_high=_money(high),
        dispersion_rate=dispersion.quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP),
        interval_rate=interval_rate.quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP),
        retained_comparables=retained,
        baseline_included=baseline_included,
    )


def weighted_quantile(points: list[tuple[Decimal, Decimal]], quantile: Decimal) -> Decimal:
    if not points:
        raise ValueError("weighted quantile requires at least one point")
    ordered = sorted(points, key=lambda item: item[0])
    threshold = quantile * sum((weight for _, weight in ordered), ZERO)
    cumulative = ZERO
    for value, weight in ordered:
        cumulative += weight
        if cumulative >= threshold:
            return value
    return ordered[-1][0]


def _normalized_points(
    points: list[tuple[Decimal, Decimal]],
) -> list[tuple[Decimal, Decimal]]:
    total = sum((weight for _, weight in points), ZERO)
    if total <= 0:
        raise InsufficientValuationEvidenceError("valuation evidence has zero weight")
    return [(value, weight / total) for value, weight in points]


def _remove_outliers(
    comparables: tuple[AdjustedComparable, ...], config: ComparableConfig
) -> tuple[AdjustedComparable, ...]:
    if len(comparables) < config.outlier_minimum_samples:
        return comparables
    values = sorted(item.adjusted_unit_price for item in comparables)
    median = _median(values)
    deviations = sorted(abs(value - median) for value in values)
    mad = _median(deviations)
    if mad == 0:
        non_median = tuple(item for item in comparables if item.adjusted_unit_price != median)
        if not non_median:
            return comparables
        retained_at_median = tuple(
            item for item in comparables if item.adjusted_unit_price == median
        )
        return retained_at_median or comparables
    retained = tuple(
        item
        for item in comparables
        if Decimal("0.6745") * abs(item.adjusted_unit_price - median) / mad
        <= config.outlier_mad_threshold
    )
    return retained or comparables


def _median(values: list[Decimal]) -> Decimal:
    middle = len(values) // 2
    if len(values) % 2:
        return values[middle]
    return (values[middle - 1] + values[middle]) / Decimal("2")


def _money(value: Decimal) -> Decimal:
    return value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

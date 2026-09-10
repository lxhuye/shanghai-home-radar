from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from decimal import ROUND_FLOOR, Decimal

from home_radar_market.config import OutlierConfig

PERCENTILES = (
    Decimal("0.10"),
    Decimal("0.25"),
    Decimal("0.50"),
    Decimal("0.75"),
    Decimal("0.90"),
)


@dataclass(frozen=True)
class PercentileSet:
    p10: Decimal | None
    p25: Decimal | None
    p50: Decimal | None
    p75: Decimal | None
    p90: Decimal | None


@dataclass(frozen=True)
class OutlierDecision:
    outlier_indexes: frozenset[int]
    method: str
    lower_bound: Decimal | None
    upper_bound: Decimal | None
    degenerate_dispersion: bool = False


def percentile(values: Sequence[Decimal], probability: Decimal) -> Decimal | None:
    """Deterministic Type-7 linear interpolation using Decimal arithmetic."""
    if not values:
        return None
    if probability < 0 or probability > 1:
        raise ValueError("probability must be between zero and one")
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    position = Decimal(len(ordered) - 1) * probability
    lower_index = int(position.to_integral_value(rounding=ROUND_FLOOR))
    upper_index = min(lower_index + 1, len(ordered) - 1)
    fraction = position - Decimal(lower_index)
    return ordered[lower_index] + (ordered[upper_index] - ordered[lower_index]) * fraction


def percentile_set(values: Sequence[Decimal]) -> PercentileSet:
    results = [percentile(values, probability) for probability in PERCENTILES]
    return PercentileSet(*results)


def detect_outliers(values: Sequence[Decimal], config: OutlierConfig) -> OutlierDecision:
    """MAD first, then IQR when dispersion around the median degenerates."""
    if len(values) < config.minimum_sample_size:
        return OutlierDecision(frozenset(), "not_evaluated_small_sample", None, None)

    median = percentile(values, Decimal("0.5"))
    assert median is not None
    deviations = [abs(value - median) for value in values]
    mad = percentile(deviations, Decimal("0.5"))
    assert mad is not None
    if mad > 0:
        outliers = frozenset(
            index
            for index, value in enumerate(values)
            if Decimal("0.67448975") * abs(value - median) / mad > config.robust_z_threshold
        )
        return OutlierDecision(outliers, "mad_robust_z", None, None)

    q1 = percentile(values, Decimal("0.25"))
    q3 = percentile(values, Decimal("0.75"))
    assert q1 is not None and q3 is not None
    iqr = q3 - q1
    if iqr == 0:
        return OutlierDecision(frozenset(), "iqr", q1, q3, degenerate_dispersion=True)
    lower_bound = q1 - config.iqr_multiplier * iqr
    upper_bound = q3 + config.iqr_multiplier * iqr
    outliers = frozenset(
        index for index, value in enumerate(values) if value < lower_bound or value > upper_bound
    )
    return OutlierDecision(outliers, "iqr", lower_bound, upper_bound)


def without_indexes(values: Sequence[Decimal], excluded: frozenset[int]) -> list[Decimal]:
    return [value for index, value in enumerate(values) if index not in excluded]

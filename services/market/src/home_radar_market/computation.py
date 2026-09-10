from __future__ import annotations

from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from home_radar_models.enums import MarketObservationType

from home_radar_market.confidence import (
    ConfidenceInputs,
    ConfidenceResult,
    calculate_confidence,
)
from home_radar_market.config import MarketBaselineConfig
from home_radar_market.domain import BaselineSliceKey, ObservationRecord
from home_radar_market.liquidity import (
    LiquidityInputs,
    LiquidityResult,
    calculate_liquidity,
)
from home_radar_market.listing_metrics import ListingMetrics, calculate_listing_metrics
from home_radar_market.statistics import (
    OutlierDecision,
    PercentileSet,
    detect_outliers,
    percentile,
    percentile_set,
    without_indexes,
)


@dataclass(frozen=True)
class SliceComputation:
    observation_count: int
    outlier_count: int
    effective_price_sample_count: int
    effective_unit_price_sample_count: int
    price: PercentileSet
    unit_price: PercentileSet
    median_monthly_rent: Decimal | None
    rent_per_sqm: Decimal | None
    index_value: Decimal | None
    confidence: ConfidenceResult
    liquidity: LiquidityResult
    listing_metrics: ListingMetrics | None
    sources: tuple[str, ...]
    observation_start: datetime | None
    observation_end: datetime | None
    provenance: dict[str, object]


def compute_slice(
    records: list[ObservationRecord],
    all_history: list[ObservationRecord],
    key: BaselineSliceKey,
    start_at: datetime,
    end_at: datetime,
    config: MarketBaselineConfig,
) -> SliceComputation:
    price_pairs = _metric_pairs(records, lambda item: item.total_price)
    unit_pairs = _metric_pairs(records, lambda item: item.unit_price)
    price_values = [value for _, value in price_pairs]
    unit_values = [value for _, value in unit_pairs]
    price_outliers = detect_outliers(price_values, config.outliers)
    unit_outliers = detect_outliers(unit_values, config.outliers)
    clean_prices = without_indexes(price_values, price_outliers.outlier_indexes)
    clean_unit_prices = without_indexes(unit_values, unit_outliers.outlier_indexes)
    outlier_ids = {str(price_pairs[index][0].id) for index in price_outliers.outlier_indexes} | {
        str(unit_pairs[index][0].id) for index in unit_outliers.outlier_indexes
    }

    source_counts = Counter(record.source for record in records)
    ages = [
        max(Decimal(), Decimal(str((end_at - record.observed_at).total_seconds() / 86_400)))
        for record in records
    ]
    median_age = percentile(ages, Decimal("0.5")) or Decimal()
    bins = {(record.observed_at.year, record.observed_at.month) for record in records}
    expected_bins = max(1, (key.window_days + 29) // 30)
    has_recent_complete = any(
        record.coverage_complete
        and (end_at - record.observed_at).total_seconds() <= min(key.window_days, 30) * 86_400
        for record in records
    )
    confidence = calculate_confidence(
        ConfidenceInputs(
            sample_size=len(records),
            median_age_days=median_age,
            window_days=key.window_days,
            source_counts=source_counts,
            complete_count=sum(record.coverage_complete for record in records),
            outlier_count=len(outlier_ids),
            represented_time_bins=len(bins),
            expected_time_bins=expected_bins,
            has_recent_complete_run=has_recent_complete,
        ),
        config.confidence,
    )
    listing_metrics = (
        calculate_listing_metrics(all_history, key, start_at, end_at)
        if key.observation_type == MarketObservationType.LISTING.value
        else None
    )
    liquidity = calculate_liquidity(
        LiquidityInputs(
            active_inventory=(
                listing_metrics.active_inventory if listing_metrics is not None else None
            ),
            new_listings=listing_metrics.new_listings if listing_metrics is not None else None,
            listing_exits=listing_metrics.listing_exits if listing_metrics is not None else None,
            median_days_on_market=(
                listing_metrics.median_days_on_market if listing_metrics is not None else None
            ),
            price_cut_ratio=(
                listing_metrics.price_cut_ratio if listing_metrics is not None else None
            ),
            relisting_rate=(
                listing_metrics.relisting_rate if listing_metrics is not None else None
            ),
            buyer_pool_ratio=None,
            baseline_confidence=confidence.score,
        ),
        config.liquidity,
    )
    monthly_rents = [value for _, value in _metric_pairs(records, lambda item: item.monthly_rent)]
    rents_per_sqm = [value for _, value in _metric_pairs(records, lambda item: item.rent_per_sqm)]
    index_values = [value for _, value in _metric_pairs(records, lambda item: item.index_value)]
    return SliceComputation(
        observation_count=len(records),
        outlier_count=len(outlier_ids),
        effective_price_sample_count=len(clean_prices),
        effective_unit_price_sample_count=len(clean_unit_prices),
        price=percentile_set(clean_prices),
        unit_price=percentile_set(clean_unit_prices),
        median_monthly_rent=percentile(monthly_rents, Decimal("0.5")),
        rent_per_sqm=percentile(rents_per_sqm, Decimal("0.5")),
        index_value=percentile(index_values, Decimal("0.5")),
        confidence=confidence,
        liquidity=liquidity,
        listing_metrics=listing_metrics,
        sources=tuple(sorted(source_counts)),
        observation_start=min((record.observed_at for record in records), default=None),
        observation_end=max((record.observed_at for record in records), default=None),
        provenance={
            "observation_ids": [str(record.id) for record in records],
            "entity_deduplication": "latest_in_window",
            "percentile_method": "type_7_decimal_linear",
            "price_outlier": _outlier_provenance(price_outliers),
            "unit_price_outlier": _outlier_provenance(unit_outliers),
            "outlier_observation_ids": sorted(outlier_ids),
            "index_value_p50": str(percentile(index_values, Decimal("0.5")))
            if index_values
            else None,
        },
    )


def _metric_pairs(
    records: list[ObservationRecord],
    accessor: Callable[[ObservationRecord], Decimal | None],
) -> list[tuple[ObservationRecord, Decimal]]:
    return [(record, value) for record in records if (value := accessor(record)) is not None]


def _outlier_provenance(decision: OutlierDecision) -> dict[str, object]:
    return {
        "method": decision.method,
        "excluded_indexes": sorted(decision.outlier_indexes),
        "lower_bound": str(decision.lower_bound) if decision.lower_bound is not None else None,
        "upper_bound": str(decision.upper_bound) if decision.upper_bound is not None else None,
        "degenerate_dispersion": decision.degenerate_dispersion,
    }

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

from home_radar_models.enums import BaselineConfidence, TransactionSupport

from home_radar_valuation.config import ComparableConfig, ValuationConfidenceConfig
from home_radar_valuation.domain import (
    AdjustedComparable,
    BaselineEvidence,
    ComparableSelection,
)

ZERO = Decimal("0")
ONE = Decimal("1")


@dataclass(frozen=True)
class ValuationConfidenceResult:
    score: Decimal
    level: str
    transaction_support: str
    components: dict[str, Decimal]


def calculate_confidence(
    selection: ComparableSelection,
    retained: tuple[AdjustedComparable, ...],
    baseline: BaselineEvidence | None,
    dispersion_rate: Decimal,
    missing_attribute_count: int,
    config: ValuationConfidenceConfig,
    comparable_config: ComparableConfig,
) -> ValuationConfidenceResult:
    transaction_support = classify_transaction_support(
        retained, baseline, comparable_config.strong_transaction_count
    )
    transaction_factor = {
        TransactionSupport.NONE.value: ZERO,
        TransactionSupport.WEAK.value: Decimal("0.5"),
        TransactionSupport.STRONG.value: ONE,
    }[transaction_support]
    sources = {item.selected.record.source for item in retained}
    tier_quality = _weighted_average(
        (
            comparable_config.tier_multipliers[item.selected.tier],
            item.selected.weight,
        )
        for item in retained
    )
    freshness = _weighted_average(
        (
            max(
                ZERO,
                ONE - Decimal(item.selected.freshness_days) / Decimal(selection.window_days),
            ),
            item.selected.weight,
        )
        for item in retained
    )
    components = {
        "comparable_count": min(
            ONE, Decimal(len(retained)) / Decimal(config.target_comparable_count)
        ),
        "effective_weight": min(ONE, selection.effective_count / config.target_effective_count),
        "tier_quality": tier_quality,
        "baseline_confidence": _baseline_confidence_score(baseline),
        "freshness": freshness,
        "source_diversity": min(ONE, Decimal(len(sources)) / Decimal(config.target_source_count)),
        "dispersion": max(ZERO, ONE - dispersion_rate / config.maximum_dispersion_rate),
        "target_completeness": max(
            ZERO,
            ONE - Decimal(missing_attribute_count) / Decimal(config.maximum_missing_attributes),
        ),
        "transaction_support": transaction_factor,
    }
    score = sum(
        (components[name] * weight for name, weight in config.weights.items()),
        ZERO,
    ).quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP)
    return ValuationConfidenceResult(
        score=score,
        level=_level(score, config),
        transaction_support=transaction_support,
        components=components,
    )


def classify_transaction_support(
    comparables: tuple[AdjustedComparable, ...],
    baseline: BaselineEvidence | None,
    strong_count: int,
) -> str:
    count = sum(item.selected.record.observation_type == "transaction" for item in comparables)
    baseline_count = (
        baseline.sample_count
        if baseline is not None and baseline.observation_type == "transaction"
        else 0
    )
    if count >= strong_count or baseline_count >= strong_count:
        return TransactionSupport.STRONG.value
    if count > 0 or baseline_count > 0:
        return TransactionSupport.WEAK.value
    return TransactionSupport.NONE.value


def _baseline_confidence_score(baseline: BaselineEvidence | None) -> Decimal:
    if baseline is None:
        return ZERO
    return {
        BaselineConfidence.HIGH.value: ONE,
        BaselineConfidence.MEDIUM.value: Decimal("0.72"),
        BaselineConfidence.LOW.value: Decimal("0.42"),
        BaselineConfidence.INSUFFICIENT.value: ZERO,
    }.get(baseline.confidence, ZERO)


def _weighted_average(values: Iterable[tuple[Decimal, Decimal]]) -> Decimal:
    pairs = list(values)
    total = sum((weight for _, weight in pairs), ZERO)
    if total == 0:
        return ZERO
    return sum((value * weight for value, weight in pairs), ZERO) / total


def _level(score: Decimal, config: ValuationConfidenceConfig) -> str:
    if score >= config.thresholds.high:
        return BaselineConfidence.HIGH.value
    if score >= config.thresholds.medium:
        return BaselineConfidence.MEDIUM.value
    if score >= config.thresholds.low:
        return BaselineConfidence.LOW.value
    return BaselineConfidence.INSUFFICIENT.value

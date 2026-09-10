from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal

from home_radar_models.enums import BaselineConfidence

from home_radar_market.config import ConfidenceConfig


def _bounded(value: Decimal) -> Decimal:
    return min(max(value, Decimal()), Decimal("1"))


@dataclass(frozen=True)
class ConfidenceInputs:
    sample_size: int
    median_age_days: Decimal
    window_days: int
    source_counts: Mapping[str, int]
    complete_count: int
    outlier_count: int
    represented_time_bins: int
    expected_time_bins: int
    has_recent_complete_run: bool


@dataclass(frozen=True)
class ConfidenceResult:
    score: Decimal
    level: BaselineConfidence
    components: dict[str, Decimal]
    caps: tuple[str, ...]


def calculate_confidence(inputs: ConfidenceInputs, config: ConfidenceConfig) -> ConfidenceResult:
    sample = _bounded(Decimal(inputs.sample_size) / Decimal(config.target_sample_size))
    freshness = _bounded(
        Decimal("1") - inputs.median_age_days / Decimal(max(inputs.window_days, 1))
    )
    total_sources = sum(inputs.source_counts.values())
    if total_sources:
        shares = [
            Decimal(count) / Decimal(total_sources) for count in inputs.source_counts.values()
        ]
        effective_sources = Decimal("1") / sum((share * share for share in shares), Decimal())
        diversity = _bounded(effective_sources / Decimal(config.target_source_count))
    else:
        diversity = Decimal()
    coverage = (
        _bounded(Decimal(inputs.complete_count) / Decimal(inputs.sample_size))
        if inputs.sample_size
        else Decimal()
    )
    outlier_stability = (
        _bounded(Decimal("1") - Decimal(inputs.outlier_count) / Decimal(inputs.sample_size))
        if inputs.sample_size
        else Decimal()
    )
    continuity = (
        _bounded(Decimal(inputs.represented_time_bins) / Decimal(inputs.expected_time_bins))
        if inputs.expected_time_bins
        else Decimal()
    )
    components = {
        "sample_size": sample,
        "freshness": freshness,
        "source_diversity": diversity,
        "coverage": coverage,
        "outlier_stability": outlier_stability,
        "continuity": continuity,
    }
    score = sum((components[name] * weight for name, weight in config.weights.items()), Decimal())
    if inputs.sample_size < config.absolute_minimum_samples:
        return ConfidenceResult(
            score, BaselineConfidence.INSUFFICIENT, components, ("minimum_sample_size",)
        )

    level = _level_for_score(score, config)
    caps: list[str] = []
    if coverage < Decimal("0.5") or not inputs.has_recent_complete_run:
        level = _cap(level, BaselineConfidence.LOW)
        caps.append("coverage_or_fresh_complete_run")
    if len(inputs.source_counts) <= 1:
        level = _cap(level, BaselineConfidence.MEDIUM)
        caps.append("single_source")
    return ConfidenceResult(score, level, components, tuple(caps))


def _level_for_score(score: Decimal, config: ConfidenceConfig) -> BaselineConfidence:
    if score >= config.thresholds.high:
        return BaselineConfidence.HIGH
    if score >= config.thresholds.medium:
        return BaselineConfidence.MEDIUM
    if score >= config.thresholds.low:
        return BaselineConfidence.LOW
    return BaselineConfidence.INSUFFICIENT


def _cap(level: BaselineConfidence, maximum: BaselineConfidence) -> BaselineConfidence:
    order = {
        BaselineConfidence.INSUFFICIENT: 0,
        BaselineConfidence.LOW: 1,
        BaselineConfidence.MEDIUM: 2,
        BaselineConfidence.HIGH: 3,
    }
    return maximum if order[level] > order[maximum] else level

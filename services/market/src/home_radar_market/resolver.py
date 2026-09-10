from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal

from home_radar_models.enums import BaselineConfidence, BaselineLevel

from home_radar_market.config import FallbackConfig


@dataclass(frozen=True)
class BaselineCandidate:
    level: BaselineLevel
    sample_count: int
    confidence: BaselineConfidence
    quantiles: Mapping[str, Decimal | None]
    baseline_version: str


@dataclass(frozen=True)
class ResolvedBaseline:
    level_used: str
    confidence: BaselineConfidence
    sample_count: int
    quantiles: dict[str, Decimal | None]
    baseline_versions: tuple[str, ...]
    fallback_path: tuple[str, ...]
    fallback_reason: str | None
    local_weight: Decimal | None = None


def resolve_hierarchy(
    candidates: Mapping[BaselineLevel, BaselineCandidate], config: FallbackConfig
) -> ResolvedBaseline:
    """Resolve without silently widening the requested area or layout segment."""
    community = candidates.get(BaselineLevel.COMMUNITY)
    if community is not None and _usable(community, config.community_minimum_samples):
        return _direct(community, (BaselineLevel.COMMUNITY.value,), None)

    submarket = candidates.get(BaselineLevel.SUBMARKET)
    if (
        community is not None
        and community.sample_count > 0
        and submarket is not None
        and _usable(submarket, config.submarket_minimum_samples)
    ):
        weight = Decimal(community.sample_count) / Decimal(
            community.sample_count + config.prior_strength
        )
        quantiles = {
            key: _blend(community.quantiles.get(key), submarket.quantiles.get(key), weight)
            for key in community.quantiles.keys() | submarket.quantiles.keys()
        }
        confidence = min((community.confidence, submarket.confidence), key=_confidence_order)
        return ResolvedBaseline(
            level_used="community_submarket_shrunk",
            confidence=confidence,
            sample_count=community.sample_count,
            quantiles=quantiles,
            baseline_versions=(community.baseline_version, submarket.baseline_version),
            fallback_path=(BaselineLevel.COMMUNITY.value, BaselineLevel.SUBMARKET.value),
            fallback_reason="community_sample_below_threshold",
            local_weight=weight,
        )

    path: list[str] = [BaselineLevel.COMMUNITY.value]
    for level, minimum in (
        (BaselineLevel.SUBMARKET, config.submarket_minimum_samples),
        (BaselineLevel.DISTRICT, config.district_minimum_samples),
        (BaselineLevel.SHANGHAI, config.district_minimum_samples),
    ):
        path.append(level.value)
        candidate = candidates.get(level)
        if candidate is not None and _usable(candidate, minimum):
            return _direct(candidate, tuple(path), "requested_scope_insufficient")
    return ResolvedBaseline(
        level_used="none",
        confidence=BaselineConfidence.INSUFFICIENT,
        sample_count=0,
        quantiles={},
        baseline_versions=(),
        fallback_path=tuple(path),
        fallback_reason="no_usable_baseline_in_hierarchy",
    )


def _usable(candidate: BaselineCandidate, minimum: int) -> bool:
    return (
        candidate.sample_count >= minimum
        and candidate.confidence is not BaselineConfidence.INSUFFICIENT
    )


def _direct(
    candidate: BaselineCandidate, path: tuple[str, ...], reason: str | None
) -> ResolvedBaseline:
    return ResolvedBaseline(
        level_used=candidate.level.value,
        confidence=candidate.confidence,
        sample_count=candidate.sample_count,
        quantiles=dict(candidate.quantiles),
        baseline_versions=(candidate.baseline_version,),
        fallback_path=path,
        fallback_reason=reason,
    )


def _blend(local: Decimal | None, parent: Decimal | None, local_weight: Decimal) -> Decimal | None:
    if local is None:
        return parent
    if parent is None:
        return local
    return local_weight * local + (Decimal("1") - local_weight) * parent


def _confidence_order(value: BaselineConfidence) -> int:
    return {
        BaselineConfidence.INSUFFICIENT: 0,
        BaselineConfidence.LOW: 1,
        BaselineConfidence.MEDIUM: 2,
        BaselineConfidence.HIGH: 3,
    }[value]

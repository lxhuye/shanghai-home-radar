from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal

from home_radar_valuation.config import ComparableConfig
from home_radar_valuation.domain import (
    ComparableRecord,
    ComparableSelection,
    SelectedComparable,
    TargetProperty,
    floor_category,
    normalized_orientation,
)

ONE = Decimal("1")
ZERO = Decimal("0")


def select_comparables(
    target: TargetProperty,
    candidates: list[ComparableRecord],
    as_of: datetime,
    config: ComparableConfig,
) -> ComparableSelection:
    """Select the narrowest fresh tier set that reaches the configured minimum."""
    deduplicated = _latest_per_entity(candidates)
    final: list[SelectedComparable] = []
    final_window = config.windows_days[-1]
    for window_days in config.windows_days:
        eligible = [
            candidate
            for candidate in deduplicated
            if 0 <= (as_of - candidate.observed_at).days < window_days
            and candidate.listing_id != target.listing_id
        ]
        selected = _select_tiers(target, eligible, as_of, window_days, config)
        final = selected
        final_window = window_days
        if len(selected) >= config.minimum_comparables:
            break

    final = sorted(
        final,
        key=lambda item: (
            -item.raw_weight,
            -item.record.observed_at.timestamp(),
            str(item.record.observation_id),
        ),
    )[: config.maximum_comparables]
    normalized = _normalize_weights(final)
    effective = effective_comparable_count(item.weight for item in normalized)
    reason = None
    if not normalized:
        reason = "no_matching_observation_comparables"
    elif len(normalized) < config.minimum_comparables:
        reason = "comparable_sample_below_minimum"
    elif final_window > config.windows_days[0]:
        reason = f"freshness_expanded_to_{final_window}d"
    elif any(item.tier != "tier_1" for item in normalized):
        reason = "narrow_tier_sample_below_minimum"
    return ComparableSelection(
        comparables=tuple(normalized),
        window_days=final_window,
        effective_count=effective,
        fallback_reason=reason,
    )


def effective_comparable_count(weights: Iterable[Decimal]) -> Decimal:
    values = list(weights)
    denominator = sum((value * value for value in values), ZERO)
    if denominator == 0:
        return ZERO
    return (ONE / denominator).quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)


def _latest_per_entity(candidates: list[ComparableRecord]) -> list[ComparableRecord]:
    latest: dict[str, ComparableRecord] = {}
    for candidate in candidates:
        existing = latest.get(candidate.entity_key)
        if existing is None or (
            candidate.observed_at,
            str(candidate.observation_id),
        ) > (existing.observed_at, str(existing.observation_id)):
            latest[candidate.entity_key] = candidate
    return list(latest.values())


def _select_tiers(
    target: TargetProperty,
    eligible: list[ComparableRecord],
    as_of: datetime,
    window_days: int,
    config: ComparableConfig,
) -> list[SelectedComparable]:
    tiers: dict[str, list[SelectedComparable]] = {
        "tier_1": [],
        "tier_2": [],
        "tier_3": [],
    }
    for candidate in eligible:
        tier = _tier(target, candidate, config)
        if tier is None:
            continue
        tiers[tier].append(_score(target, candidate, tier, as_of, window_days, config))

    selected: list[SelectedComparable] = []
    for tier in ("tier_1", "tier_2", "tier_3"):
        selected.extend(tiers[tier])
        if len(selected) >= config.minimum_comparables:
            break
    return selected


def _tier(
    target: TargetProperty, candidate: ComparableRecord, config: ComparableConfig
) -> str | None:
    area_difference = _area_difference(target.area_sqm, candidate.area_sqm)
    exact_layout = target.resolved_layout == candidate.resolved_layout
    adjacent_layout = _adjacent_layout(target.resolved_layout, candidate.resolved_layout, config)
    same_community = candidate.community == target.community
    same_submarket = candidate.submarket == target.submarket
    age_difference = _age_difference(target.year_built, candidate.year_built)

    if (
        same_community
        and exact_layout
        and area_difference <= config.tier_1_area_tolerance
        and age_difference is not None
        and age_difference <= config.tier_1_building_age_years
    ):
        return "tier_1"
    if (
        same_community
        and (exact_layout or adjacent_layout)
        and area_difference <= config.tier_2_area_tolerance
        and (age_difference is None or age_difference <= config.tier_2_building_age_years)
    ):
        return "tier_2"
    building_match = (
        target.building_type is None
        or candidate.building_type is None
        or target.building_type == candidate.building_type
    )
    if (
        same_submarket
        and building_match
        and (exact_layout or adjacent_layout)
        and area_difference <= config.tier_3_area_tolerance
    ):
        return "tier_3"
    return None


def _score(
    target: TargetProperty,
    candidate: ComparableRecord,
    tier: str,
    as_of: datetime,
    window_days: int,
    config: ComparableConfig,
) -> SelectedComparable:
    age_difference = _age_difference(target.year_built, candidate.year_built)
    factors = {
        "geography": config.tier_multipliers[tier],
        "community": (
            ONE if candidate.community == target.community else config.cross_community_similarity
        ),
        "area": max(ZERO, ONE - _area_difference(target.area_sqm, candidate.area_sqm)),
        "layout": _layout_similarity(target.resolved_layout, candidate.resolved_layout, config),
        "building_age": (
            config.unknown_similarity
            if age_difference is None
            else max(
                ZERO,
                ONE
                - Decimal(age_difference) / Decimal(config.building_age_similarity_horizon_years),
            )
        ),
        "floor": _exact_or_unknown(
            floor_category(target.floor, target.total_floors),
            floor_category(candidate.floor, candidate.total_floors),
            config.unknown_similarity,
        ),
        "elevator": _exact_or_unknown(
            target.elevator, candidate.elevator, config.unknown_similarity
        ),
        "orientation": _exact_or_unknown(
            normalized_orientation(target.orientation),
            normalized_orientation(candidate.orientation),
            config.unknown_similarity,
        ),
        "freshness": max(
            ZERO,
            ONE - Decimal(max(0, (as_of - candidate.observed_at).days)) / window_days,
        ),
        "source_confidence": min(ONE, max(ZERO, candidate.source_confidence)),
    }
    similarity = sum(
        (factors[name] * weight for name, weight in config.weights.items()),
        ZERO,
    ).quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)
    source_multiplier = (
        config.transaction_weight_multiplier if candidate.observation_type == "transaction" else ONE
    )
    raw_weight = (similarity * config.tier_multipliers[tier] * source_multiplier).quantize(
        Decimal("0.000001"), rounding=ROUND_HALF_UP
    )
    reasons = (
        f"geography={candidate.community or candidate.submarket}",
        f"area_difference={_area_difference(target.area_sqm, candidate.area_sqm):.4f}",
        f"layout={candidate.resolved_layout or 'unknown'}",
        f"age_difference={age_difference if age_difference is not None else 'unknown'}",
        f"freshness_days={max(0, (as_of - candidate.observed_at).days)}",
        f"evidence={candidate.observation_type}",
    )
    return SelectedComparable(
        record=candidate,
        tier=tier,
        similarity_score=similarity,
        raw_weight=raw_weight,
        weight=ZERO,
        freshness_days=max(0, (as_of - candidate.observed_at).days),
        reasons=reasons,
    )


def _normalize_weights(values: list[SelectedComparable]) -> list[SelectedComparable]:
    total = sum((item.raw_weight for item in values), ZERO)
    if total <= 0:
        return []
    return [
        SelectedComparable(
            record=item.record,
            tier=item.tier,
            similarity_score=item.similarity_score,
            raw_weight=item.raw_weight,
            weight=(item.raw_weight / total).quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP),
            freshness_days=item.freshness_days,
            reasons=item.reasons,
        )
        for item in values
    ]


def _area_difference(target: Decimal, candidate: Decimal) -> Decimal:
    return abs(candidate - target) / target


def _age_difference(target: int | None, candidate: int | None) -> int | None:
    if target is None or candidate is None:
        return None
    return abs(target - candidate)


def _adjacent_layout(target: str | None, candidate: str | None, config: ComparableConfig) -> bool:
    if target is None or candidate is None:
        return False
    return candidate in config.adjacent_layouts.get(target, ())


def _layout_similarity(
    target: str | None, candidate: str | None, config: ComparableConfig
) -> Decimal:
    if target is None or candidate is None:
        return config.unknown_similarity
    if target == candidate:
        return ONE
    if _adjacent_layout(target, candidate, config):
        return config.adjacent_layout_similarity
    return ZERO


def _exact_or_unknown(target: object, candidate: object, unknown_similarity: Decimal) -> Decimal:
    if target in (None, "unknown") or candidate in (None, "unknown"):
        return unknown_similarity
    return ONE if target == candidate else ZERO

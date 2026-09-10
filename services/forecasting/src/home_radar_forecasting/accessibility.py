from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal
from itertools import pairwise
from math import asin, cos, radians, sin, sqrt

from home_radar_forecasting.config import (
    AccessibilityConfig,
    TransportAccessibilityConfig,
)
from home_radar_forecasting.domain import (
    EmploymentCenterInput,
    FactorEvidence,
    FutureProjectInput,
)

EARTH_RADIUS_KM = 6371.0088


def employment_accessibility(
    longitude: Decimal | None,
    latitude: Decimal | None,
    centers: tuple[EmploymentCenterInput, ...],
    config: AccessibilityConfig,
    data_mode: str,
) -> FactorEvidence:
    if longitude is None or latitude is None or not centers:
        return _insufficient(
            "employment_accessibility",
            data_mode,
            "Property coordinates or employment-center coverage is unavailable.",
        )
    current = _weighted_center_score(longitude, latitude, centers, config, current=True)
    future = _weighted_center_score(longitude, latitude, centers, config, current=False)
    if current is None or future is None:
        return _insufficient(
            "employment_accessibility",
            data_mode,
            "Employment-center weights are insufficient for accessibility scoring.",
        )
    confidence = _weighted_confidence(centers)
    timestamp = max(center.source_timestamp for center in centers)
    return FactorEvidence(
        factor="employment_accessibility",
        current_score=current,
        future_score=future,
        confidence=confidence,
        source="employment_center_model",
        source_timestamp=timestamp,
        data_mode=data_mode,
        explanation=("Distance-based V0 employment accessibility; it is not transit travel time."),
        metadata={"center_count": len(centers), "routing_method": "haversine_v0"},
    )


def transport_accessibility(
    longitude: Decimal | None,
    latitude: Decimal | None,
    projects: tuple[FutureProjectInput, ...],
    config: TransportAccessibilityConfig,
    realization_weights: dict[str, Decimal],
    data_mode: str,
) -> FactorEvidence:
    located = [
        project
        for project in projects
        if project.project_type == "transport"
        and project.longitude is not None
        and project.latitude is not None
    ]
    if longitude is None or latitude is None or not located:
        return _insufficient(
            "transport_accessibility",
            data_mode,
            "Property coordinates or transport-project evidence is unavailable.",
        )
    current = _best_transport_score(longitude, latitude, located, config, current_only=True)
    future_raw = _best_transport_score(
        longitude,
        latitude,
        located,
        config,
        current_only=False,
        realization_weights=realization_weights,
    )
    future = min(current + config.maximum_planning_uplift, max(current, future_raw))
    planning_share = _planning_share(located, realization_weights)
    return FactorEvidence(
        factor="transport_accessibility",
        current_score=current,
        future_score=_rounded(future),
        confidence=_project_confidence(located, planning_share),
        source="future_project_model",
        source_timestamp=max(project.source_date for project in located),
        data_mode=data_mode,
        explanation=(
            "Transport access uses distance and realization-weighted project status; "
            "employment effects are excluded to avoid double counting."
        ),
        metadata={"project_count": len(located), "planning_share": str(planning_share)},
    )


def planning_realization_factor(
    projects: tuple[FutureProjectInput, ...],
    realization_weights: dict[str, Decimal],
    data_mode: str,
) -> FactorEvidence:
    planned = [project for project in projects if project.status != "current"]
    if not planned:
        return _insufficient(
            "planning_realization",
            data_mode,
            "No dated non-current project evidence is available.",
        )
    weighted = [
        _project_probability(project, realization_weights) * project.confidence
        for project in planned
    ]
    score = _rounded(sum(weighted, Decimal()) / Decimal(len(weighted)) * Decimal("100"))
    return FactorEvidence(
        factor="planning_realization",
        current_score=Decimal("0"),
        future_score=score,
        confidence=sum((project.confidence for project in planned), Decimal())
        / Decimal(len(planned)),
        source="future_project_status",
        source_timestamp=max(project.source_date for project in planned),
        data_mode=data_mode,
        explanation="Non-current projects are probability-weighted by auditable status.",
        metadata={"project_count": len(planned)},
    )


def _weighted_center_score(
    longitude: Decimal,
    latitude: Decimal,
    centers: tuple[EmploymentCenterInput, ...],
    config: AccessibilityConfig,
    *,
    current: bool,
) -> Decimal | None:
    weighted_score = Decimal()
    total_weight = Decimal()
    for center in centers:
        weight = center.current_employment_weight if current else center.future_employment_weight
        if weight <= 0:
            continue
        distance = haversine_km(longitude, latitude, center.longitude, center.latitude)
        weighted_score += interpolate_distance_score(distance, config) * weight
        total_weight += weight
    if total_weight < config.minimum_total_weight:
        return None
    return _rounded(weighted_score / total_weight)


def _best_transport_score(
    longitude: Decimal,
    latitude: Decimal,
    projects: list[FutureProjectInput],
    config: AccessibilityConfig,
    *,
    current_only: bool,
    realization_weights: dict[str, Decimal] | None = None,
) -> Decimal:
    scores: list[Decimal] = []
    for project in projects:
        if current_only and project.status != "current":
            continue
        assert project.longitude is not None and project.latitude is not None
        distance = haversine_km(longitude, latitude, project.longitude, project.latitude)
        score = interpolate_distance_score(distance, config)
        if not current_only:
            assert realization_weights is not None
            score *= _project_probability(project, realization_weights)
        scores.append(score)
    return max(scores, default=Decimal("0"))


def interpolate_distance_score(distance_km: Decimal, config: AccessibilityConfig) -> Decimal:
    points = config.distance_score_points
    if distance_km <= points[0].distance_km:
        return points[0].score
    for left, right in pairwise(points):
        if distance_km <= right.distance_km:
            span = right.distance_km - left.distance_km
            ratio = (distance_km - left.distance_km) / span
            return left.score + ratio * (right.score - left.score)
    return points[-1].score


def haversine_km(
    longitude_a: Decimal,
    latitude_a: Decimal,
    longitude_b: Decimal,
    latitude_b: Decimal,
) -> Decimal:
    lon_a, lat_a, lon_b, lat_b = map(
        radians, map(float, (longitude_a, latitude_a, longitude_b, latitude_b))
    )
    delta_lon = lon_b - lon_a
    delta_lat = lat_b - lat_a
    value = sin(delta_lat / 2) ** 2 + cos(lat_a) * cos(lat_b) * sin(delta_lon / 2) ** 2
    return Decimal(str(2 * EARTH_RADIUS_KM * asin(sqrt(value))))


def _project_probability(project: FutureProjectInput, weights: dict[str, Decimal]) -> Decimal:
    configured = weights.get(project.status, Decimal("0"))
    if project.probability is None:
        return configured
    return min(configured, project.probability)


def _planning_share(projects: list[FutureProjectInput], weights: dict[str, Decimal]) -> Decimal:
    if not projects:
        return Decimal()
    values = [_project_probability(project, weights) for project in projects]
    return sum(values, Decimal()) / Decimal(len(values))


def _project_confidence(projects: list[FutureProjectInput], planning_share: Decimal) -> Decimal:
    authority = sum((project.confidence for project in projects), Decimal()) / Decimal(
        len(projects)
    )
    return min(Decimal("1"), authority * (Decimal("0.5") + planning_share / 2))


def _weighted_confidence(centers: tuple[EmploymentCenterInput, ...]) -> Decimal:
    weights = [center.current_employment_weight for center in centers]
    total = sum(weights, Decimal())
    if total == 0:
        return Decimal("0")
    return (
        sum(
            (center.confidence * weight for center, weight in zip(centers, weights, strict=True)),
            Decimal(),
        )
        / total
    )


def _insufficient(factor: str, data_mode: str, explanation: str) -> FactorEvidence:
    return FactorEvidence(
        factor=factor,
        current_score=None,
        future_score=None,
        confidence=Decimal("0"),
        source="insufficient",
        source_timestamp=None,
        data_mode=data_mode,
        explanation=explanation,
    )


def _rounded(value: Decimal) -> Decimal:
    return value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

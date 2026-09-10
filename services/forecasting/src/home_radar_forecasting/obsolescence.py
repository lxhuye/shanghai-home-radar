from __future__ import annotations

from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal

from home_radar_forecasting.config import ObsolescenceConfig
from home_radar_forecasting.domain import (
    FactorEvidence,
    FutureProperty,
    ObsolescenceResult,
)

QUALITY_RISK = {
    "excellent": Decimal("10"),
    "good": Decimal("25"),
    "average": Decimal("50"),
    "poor": Decimal("82"),
    "very_poor": Decimal("95"),
}


def calculate_obsolescence_risk(
    property_: FutureProperty,
    factors: tuple[FactorEvidence, ...],
    as_of: datetime,
    config: ObsolescenceConfig,
) -> ObsolescenceResult:
    by_name = {factor.factor: factor for factor in factors}
    scores = _component_scores(property_, by_name, as_of, config)
    available = {name: value for name, value in scores.items() if value is not None}
    available_weight = sum((config.weights[name] for name in available), Decimal())
    coverage = available_weight / Decimal("100")
    missing = tuple(name for name in config.weights if name not in available)
    if coverage < config.minimum_coverage or available_weight == 0:
        return ObsolescenceResult(
            score=None,
            coverage=_rounded(coverage, Decimal("0.000001")),
            contributions={},
            component_scores=available,
            missing_components=missing,
        )
    contributions = {
        name: _rounded(value * config.weights[name] / available_weight)
        for name, value in available.items()
    }
    return ObsolescenceResult(
        score=_rounded(sum(contributions.values(), Decimal())),
        coverage=_rounded(coverage, Decimal("0.000001")),
        contributions=contributions,
        component_scores=available,
        missing_components=missing,
    )


def _component_scores(
    property_: FutureProperty,
    factors: dict[str, FactorEvidence],
    as_of: datetime,
    config: ObsolescenceConfig,
) -> dict[str, Decimal | None]:
    scarcity = _future_score(factors, "supply_scarcity")
    buyer_pool = _future_score(factors, "buyer_pool_depth")
    community = _future_score(factors, "community_competitiveness")
    return {
        "building_aging": _building_age_risk(property_, factors, as_of, config),
        "elevator_disadvantage": _elevator_risk(property_, config),
        "parking_deficiency": _quality_risk(property_.parking_quality),
        "layout_obsolescence": _layout_risk(property_),
        "property_management_weakness": _quality_risk(property_.property_management_quality),
        "community_deterioration": _inverse(community),
        "competing_supply": _inverse(scarcity),
        "buyer_pool_shrinkage": _inverse(buyer_pool),
        "total_price_mismatch": _total_price_risk(property_, config),
        "product_replacement": _future_score(factors, "product_obsolescence"),
    }


def _building_age_risk(
    property_: FutureProperty,
    factors: dict[str, FactorEvidence],
    as_of: datetime,
    config: ObsolescenceConfig,
) -> Decimal | None:
    explicit = _future_score(factors, "building_aging")
    if explicit is not None:
        return explicit
    if property_.year_built is None:
        return None
    age_at_five_year_horizon = max(0, as_of.year + 5 - property_.year_built)
    exposed_years = max(0, age_at_five_year_horizon - config.building_age_grace_years)
    return min(Decimal("100"), Decimal(exposed_years) * config.building_age_risk_per_year)


def _elevator_risk(property_: FutureProperty, config: ObsolescenceConfig) -> Decimal:
    if property_.elevator is False:
        return config.walk_up_risk
    if property_.elevator is True:
        return Decimal("15")
    return config.elevator_unknown_risk


def _layout_risk(property_: FutureProperty) -> Decimal | None:
    explicit = _quality_risk(property_.layout_mainstreamness)
    if explicit is not None:
        return explicit
    if property_.bedrooms is None:
        return None
    if property_.bedrooms in {2, 3} and Decimal("45") <= property_.area_sqm <= Decimal("120"):
        return Decimal("20")
    if property_.bedrooms == 1:
        return Decimal("45")
    return Decimal("68")


def _total_price_risk(property_: FutureProperty, config: ObsolescenceConfig) -> Decimal:
    price = property_.fair_value
    if config.mainstream_total_price_min <= price <= config.mainstream_total_price_max:
        return Decimal("20")
    distance = (
        config.mainstream_total_price_min - price
        if price < config.mainstream_total_price_min
        else price - config.mainstream_total_price_max
    )
    boundary = (
        config.mainstream_total_price_min
        if price < config.mainstream_total_price_min
        else config.mainstream_total_price_max
    )
    return min(Decimal("100"), Decimal("50") + distance / boundary * Decimal("50"))


def _quality_risk(value: str | None) -> Decimal | None:
    if value is None:
        return None
    return QUALITY_RISK.get(value.strip().lower())


def _future_score(factors: dict[str, FactorEvidence], name: str) -> Decimal | None:
    factor = factors.get(name)
    return factor.future_score if factor is not None else None


def _inverse(value: Decimal | None) -> Decimal | None:
    return Decimal("100") - value if value is not None else None


def _rounded(value: Decimal, quantum: Decimal = Decimal("0.01")) -> Decimal:
    return value.quantize(quantum, rounding=ROUND_HALF_UP)

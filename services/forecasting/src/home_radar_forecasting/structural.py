from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal

from home_radar_forecasting.config import StructuralAlphaConfig
from home_radar_forecasting.domain import (
    FactorEvidence,
    StructuralAlphaResult,
)


def calculate_structural_alpha(
    factors: tuple[FactorEvidence, ...],
    hierarchy: dict[str, Decimal | None],
    config: StructuralAlphaConfig,
) -> StructuralAlphaResult:
    by_name = {factor.factor: factor for factor in factors}
    values = _component_values(by_name, hierarchy)
    total_weight = sum((abs(weight) for weight in config.component_weights.values()), Decimal())
    contributions: dict[str, Decimal] = {}
    missing: list[str] = []
    for name, weight in config.component_weights.items():
        value = values.get(name)
        if value is None:
            missing.append(name)
            continue
        contributions[name] = _rounded(_clamp(value) * weight / total_weight)
    raw = sum(contributions.values(), Decimal())
    bounded = max(-config.maximum_absolute_index, min(config.maximum_absolute_index, raw))
    return StructuralAlphaResult(
        index=_rounded(bounded),
        components=contributions,
        missing_components=tuple(missing),
    )


def _component_values(
    factors: dict[str, FactorEvidence], hierarchy: dict[str, Decimal | None]
) -> dict[str, Decimal | None]:
    employment = factors.get("employment_accessibility")
    transport = factors.get("transport_accessibility")
    scarcity = _future(factors, "supply_scarcity")
    buyer_pool = factors.get("buyer_pool_depth")
    renewal = _future(factors, "urban_renewal")
    aging = _future(factors, "building_aging")
    product = _future(factors, "product_obsolescence")
    cycle = _future(factors, "market_cycle")
    return {
        "district_alpha": hierarchy.get("district_alpha"),
        "submarket_alpha": hierarchy.get("submarket_alpha"),
        "community_alpha": hierarchy.get("community_alpha"),
        "property_alpha": hierarchy.get("property_alpha"),
        "infrastructure_delta": transport.delta if transport is not None else None,
        "employment_delta": employment.delta if employment is not None else None,
        "supply_scarcity": _centered(scarcity),
        "buyer_pool_delta": buyer_pool.delta if buyer_pool is not None else None,
        "urban_renewal": _centered(renewal),
        "building_aging": aging,
        "product_obsolescence": product,
        "supply_shock": Decimal("100") - scarcity if scarcity is not None else None,
        "market_cycle": _centered(cycle),
    }


def _future(factors: dict[str, FactorEvidence], name: str) -> Decimal | None:
    factor = factors.get(name)
    return factor.future_score if factor is not None else None


def _centered(value: Decimal | None) -> Decimal | None:
    return (value - Decimal("50")) * Decimal("2") if value is not None else None


def _clamp(value: Decimal) -> Decimal:
    return max(Decimal("-100"), min(Decimal("100"), value))


def _rounded(value: Decimal) -> Decimal:
    return value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

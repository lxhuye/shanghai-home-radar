from __future__ import annotations

from decimal import Decimal

from home_radar_forecasting.config import WarningConfig
from home_radar_forecasting.domain import (
    CalibrationState,
    FactorEvidence,
)


def future_warnings(
    factors: tuple[FactorEvidence, ...],
    obsolescence_risk: Decimal | None,
    confidence_score: Decimal,
    calibration: CalibrationState,
    data_mode: str,
    config: WarningConfig,
) -> tuple[str, ...]:
    by_name = {factor.factor: factor for factor in factors}
    warnings: list[str] = []
    _append_if(
        warnings,
        obsolescence_risk is not None and obsolescence_risk >= config.high_obsolescence_risk,
        "HIGH_OBSOLESCENCE_RISK",
    )
    scarcity = _future(by_name, "supply_scarcity")
    _append_if(
        warnings,
        scarcity is not None and Decimal("100") - scarcity >= config.supply_shock_score,
        "SUPPLY_SHOCK_RISK",
    )
    transport_delta = _delta(by_name, "transport_accessibility")
    _append_if(
        warnings,
        transport_delta is not None and transport_delta >= config.planning_dependency_delta,
        "PLANNING_DEPENDENCY",
    )
    employment = _future(by_name, "employment_accessibility")
    _append_if(
        warnings,
        employment is not None and employment < config.employment_access_weakness,
        "EMPLOYMENT_ACCESS_WEAKNESS",
    )
    aging = _future(by_name, "building_aging")
    _append_if(
        warnings,
        aging is not None and aging >= config.aging_product_score,
        "AGING_PRODUCT",
    )
    buyer_delta = _delta(by_name, "buyer_pool_depth")
    _append_if(
        warnings,
        buyer_delta is not None and buyer_delta <= config.liquidity_decay_delta,
        "LIQUIDITY_DECAY_RISK",
    )
    rental = _future(by_name, "rental_demand")
    _append_if(
        warnings,
        rental is None or rental < config.low_rental_support,
        "LOW_RENTAL_SUPPORT",
    )
    _append_if(
        warnings,
        confidence_score < config.low_forecast_confidence,
        "LOW_FORECAST_CONFIDENCE",
    )
    _append_if(
        warnings,
        calibration != "calibrated",
        "UNCALIBRATED_MODEL",
    )
    _append_if(warnings, data_mode != "live", "NON_LIVE_DATA")
    return tuple(warnings)


def _future(factors: dict[str, FactorEvidence], name: str) -> Decimal | None:
    factor = factors.get(name)
    return factor.future_score if factor is not None else None


def _delta(factors: dict[str, FactorEvidence], name: str) -> Decimal | None:
    factor = factors.get(name)
    return factor.delta if factor is not None else None


def _append_if(values: list[str], condition: bool, warning: str) -> None:
    if condition:
        values.append(warning)

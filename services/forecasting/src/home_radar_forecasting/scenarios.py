from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal

from home_radar_forecasting.config import ScenarioEngineConfig
from home_radar_forecasting.domain import (
    ConfidenceLevel,
    ScenarioName,
    ScenarioOutcome,
)

SCENARIOS: tuple[ScenarioName, ...] = ("bear", "base", "bull")


def generate_scenarios(
    fair_value_anchor: Decimal,
    structural_alpha: Decimal,
    confidence: ConfidenceLevel,
    config: ScenarioEngineConfig,
) -> tuple[ScenarioOutcome, ...]:
    if fair_value_anchor <= 0:
        raise ValueError("P4 Fair Value anchor must be positive")
    outcomes: list[ScenarioOutcome] = []
    for horizon, assumptions in sorted(config.market_cagr_ranges.items()):
        for scenario in SCENARIOS:
            market_low, market_high = assumptions[scenario]
            outcomes.append(
                _scenario_outcome(
                    fair_value_anchor,
                    structural_alpha,
                    confidence,
                    horizon,
                    scenario,
                    market_low,
                    market_high,
                    config,
                )
            )
    return tuple(outcomes)


def _scenario_outcome(
    anchor: Decimal,
    alpha: Decimal,
    confidence: ConfidenceLevel,
    horizon: int,
    scenario: ScenarioName,
    market_low: Decimal,
    market_high: Decimal,
    config: ScenarioEngineConfig,
) -> ScenarioOutcome:
    alpha_adjustment = _alpha_adjustment(alpha, scenario, config)
    widening = config.confidence_range_widening[confidence]
    cagr_low = max(Decimal("-0.95"), market_low + alpha_adjustment - widening)
    cagr_high = max(cagr_low, market_high + alpha_adjustment + widening)
    low_factor = (Decimal("1") + cagr_low) ** horizon
    high_factor = (Decimal("1") + cagr_high) ** horizon
    return ScenarioOutcome(
        horizon_years=horizon,
        scenario=scenario,
        price_range_low=_round_price(anchor * low_factor),
        price_range_high=_round_price(anchor * high_factor),
        nominal_return_low=_rounded(low_factor - Decimal("1")),
        nominal_return_high=_rounded(high_factor - Decimal("1")),
        cagr_low=_rounded(cagr_low),
        cagr_high=_rounded(cagr_high),
        probability=getattr(config.probabilities, scenario),
        confidence=confidence,
        market_beta_cagr_low=market_low,
        market_beta_cagr_high=market_high,
        structural_alpha_annual_adjustment=_rounded(alpha_adjustment),
    )


def _alpha_adjustment(
    alpha: Decimal, scenario: ScenarioName, config: ScenarioEngineConfig
) -> Decimal:
    normalized = max(Decimal("-1"), min(Decimal("1"), alpha / Decimal("100")))
    return (
        normalized
        * config.structural_alpha_maximum_annual_adjustment
        * config.scenario_alpha_sensitivity[scenario]
    )


def _round_price(value: Decimal) -> Decimal:
    return value.quantize(Decimal("1E4"), rounding=ROUND_HALF_UP)


def _rounded(value: Decimal) -> Decimal:
    return value.quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)

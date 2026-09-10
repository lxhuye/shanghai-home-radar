from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal

from home_radar_forecasting.config import WeightedScoreConfig
from home_radar_forecasting.domain import FactorEvidence, WeightedScoreResult


def calculate_future_score(
    factors: tuple[FactorEvidence, ...], config: WeightedScoreConfig
) -> WeightedScoreResult:
    by_name = {factor.factor: factor for factor in factors}
    available: dict[str, Decimal] = {}
    missing: list[str] = []
    for name in config.weights:
        evidence = by_name.get(name)
        if evidence is None or evidence.future_score is None:
            missing.append(name)
            continue
        available[name] = evidence.future_score
    available_weight = sum((config.weights[name] for name in available), Decimal())
    coverage = available_weight / Decimal("100")
    if coverage < config.minimum_coverage or available_weight == 0:
        return WeightedScoreResult(
            score=None,
            coverage=_rounded(coverage, Decimal("0.000001")),
            contributions={},
            missing_factors=tuple(missing),
        )
    contributions = {
        name: _rounded(score * config.weights[name] / available_weight)
        for name, score in available.items()
    }
    score = _rounded(sum(contributions.values(), Decimal()))
    return WeightedScoreResult(
        score=score,
        coverage=_rounded(coverage, Decimal("0.000001")),
        contributions=contributions,
        missing_factors=tuple(missing),
    )


def quality_value_quadrant(
    value_score: Decimal,
    future_score: Decimal | None,
    *,
    high_value_score: Decimal,
    high_future_score: Decimal,
) -> str | None:
    if future_score is None:
        return None
    high_value = value_score >= high_value_score
    high_future = future_score >= high_future_score
    if high_future and high_value:
        return "QUALITY_AT_DISCOUNT"
    if high_future:
        return "GOOD_BUT_EXPENSIVE"
    if high_value:
        return "VALUE_TRAP"
    return "PASS"


def _rounded(value: Decimal, quantum: Decimal = Decimal("0.01")) -> Decimal:
    return value.quantize(quantum, rounding=ROUND_HALF_UP)

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal


@dataclass(frozen=True)
class FutureBacktestCase:
    listing_id: uuid.UUID
    assessment_version: str
    forecast_at: datetime
    outcome_at: datetime
    horizon_years: int
    fair_value_anchor: Decimal
    observed_value: Decimal
    bear_probability: Decimal
    base_probability: Decimal
    bull_probability: Decimal
    forecast_low: Decimal
    forecast_high: Decimal
    predicted_rank: Decimal
    observed_rank: Decimal
    predicted_downside: bool
    observed_downside: bool
    predicted_outperformance: bool
    observed_outperformance: bool
    confidence: str
    data_mode: str
    verified_outcome: bool


@dataclass(frozen=True)
class FutureBacktestMetrics:
    sample_count: int
    scenario_coverage: Decimal
    downside_calibration_error: Decimal
    rank_correlation: Decimal | None
    relative_outperformance_accuracy: Decimal
    confidence_calibration: dict[str, Decimal]


def calculate_future_backtest_metrics(
    cases: list[FutureBacktestCase],
) -> FutureBacktestMetrics:
    if not cases:
        raise ValueError("future backtesting requires verified outcomes")
    if any(not case.verified_outcome or case.data_mode != "live" for case in cases):
        raise ValueError("synthetic, sample, or unverified outcomes cannot publish metrics")
    coverage = _mean(
        [
            Decimal(int(case.forecast_low <= case.observed_value <= case.forecast_high))
            for case in cases
        ]
    )
    downside_error = _mean(
        [Decimal(int(case.predicted_downside != case.observed_downside)) for case in cases]
    )
    outperformance = _mean(
        [
            Decimal(int(case.predicted_outperformance == case.observed_outperformance))
            for case in cases
        ]
    )
    return FutureBacktestMetrics(
        sample_count=len(cases),
        scenario_coverage=coverage,
        downside_calibration_error=downside_error,
        rank_correlation=_rank_concordance(cases),
        relative_outperformance_accuracy=outperformance,
        confidence_calibration=_confidence_calibration(cases),
    )


def _rank_concordance(cases: list[FutureBacktestCase]) -> Decimal | None:
    concordant = 0
    comparable = 0
    for index, left in enumerate(cases):
        for right in cases[index + 1 :]:
            predicted = left.predicted_rank - right.predicted_rank
            observed = left.observed_rank - right.observed_rank
            if predicted == 0 or observed == 0:
                continue
            comparable += 1
            concordant += int((predicted > 0) == (observed > 0))
    return Decimal(concordant) / Decimal(comparable) if comparable else None


def _confidence_calibration(cases: list[FutureBacktestCase]) -> dict[str, Decimal]:
    result: dict[str, Decimal] = {}
    for confidence in sorted({case.confidence for case in cases}):
        scoped = [case for case in cases if case.confidence == confidence]
        result[confidence] = _mean(
            [
                abs(case.observed_value - case.fair_value_anchor) / case.fair_value_anchor
                for case in scoped
            ]
        )
    return result


def _mean(values: list[Decimal]) -> Decimal:
    return sum(values, Decimal()) / Decimal(len(values))

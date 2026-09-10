from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal


@dataclass(frozen=True)
class VerifiedBacktestCase:
    listing_id: uuid.UUID
    valuation_version: str
    valuation_at: datetime
    predicted_fair_value: Decimal
    value_score: Decimal
    confidence_bucket: str
    verified_transaction_at: datetime
    verified_transaction_price: Decimal


@dataclass(frozen=True)
class BacktestMetrics:
    sample_count: int
    mean_absolute_error: Decimal
    mean_absolute_percentage_error: Decimal
    median_absolute_percentage_error: Decimal
    directional_ranking_quality: Decimal | None
    calibration_by_confidence: dict[str, Decimal]


def calculate_backtest_metrics(
    cases: list[VerifiedBacktestCase],
) -> BacktestMetrics:
    """Evaluate only verified outcomes; callers must never pass synthetic closes."""
    if not cases:
        raise ValueError("backtesting requires verified transaction outcomes")
    absolute_errors = [
        abs(case.predicted_fair_value - case.verified_transaction_price) for case in cases
    ]
    percentage_errors = [
        error / case.verified_transaction_price
        for error, case in zip(absolute_errors, cases, strict=True)
    ]
    calibration: dict[str, Decimal] = {}
    for bucket in sorted({case.confidence_bucket for case in cases}):
        values = [
            error
            for error, case in zip(percentage_errors, cases, strict=True)
            if case.confidence_bucket == bucket
        ]
        calibration[bucket] = sum(values, Decimal("0")) / Decimal(len(values))
    return BacktestMetrics(
        sample_count=len(cases),
        mean_absolute_error=sum(absolute_errors, Decimal("0")) / Decimal(len(cases)),
        mean_absolute_percentage_error=sum(percentage_errors, Decimal("0")) / Decimal(len(cases)),
        median_absolute_percentage_error=_median(percentage_errors),
        directional_ranking_quality=_ranking_quality(cases),
        calibration_by_confidence=calibration,
    )


def _ranking_quality(cases: list[VerifiedBacktestCase]) -> Decimal | None:
    concordant = 0
    comparable_pairs = 0
    for index, left in enumerate(cases):
        for right in cases[index + 1 :]:
            predicted = left.predicted_fair_value - right.predicted_fair_value
            actual = left.verified_transaction_price - right.verified_transaction_price
            if predicted == 0 or actual == 0:
                continue
            comparable_pairs += 1
            concordant += int((predicted > 0) == (actual > 0))
    if comparable_pairs == 0:
        return None
    return Decimal(concordant) / Decimal(comparable_pairs)


def _median(values: list[Decimal]) -> Decimal:
    ordered = sorted(values)
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[middle]
    return (ordered[middle - 1] + ordered[middle]) / Decimal("2")

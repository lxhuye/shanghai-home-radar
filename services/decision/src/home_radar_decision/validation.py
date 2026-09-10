from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal

from home_radar_decision.config import ValidationConfig
from home_radar_decision.domain import BlindEvaluationCase, BlindEvaluationMetrics


def calculate_blind_metrics(
    cases: list[BlindEvaluationCase], config: ValidationConfig
) -> BlindEvaluationMetrics:
    ranked = sorted(
        (case for case in cases if case.rank is not None),
        key=lambda case: case.rank or 0,
    )
    value_traps = [case for case in cases if case.human_label == "VALUE_TRAP"]
    views = [case for case in cases if case.workflow_state == "VIEW"]
    return BlindEvaluationMetrics(
        labeled_count=len(cases),
        precision_at_5=_strict_precision(ranked[:5], config),
        precision_at_10=_strict_precision(ranked[:10], config),
        value_trap_false_positive_rate=_rate(
            sum(
                case.eligibility_status == "ELIGIBLE" or case.workflow_state == "VIEW"
                for case in value_traps
            ),
            len(value_traps),
        ),
        view_acceptance_rate=_rate(
            sum(case.human_label == config.strict_positive_label for case in views),
            len(views),
        ),
        top_10_manual_acceptance_rate=_rate(
            sum(case.human_label in config.accepted_labels for case in ranked[:10]),
            len(ranked[:10]),
        ),
    )


def _strict_precision(cases: list[BlindEvaluationCase], config: ValidationConfig) -> Decimal | None:
    return _rate(
        sum(case.human_label == config.strict_positive_label for case in cases),
        len(cases),
    )


def _rate(numerator: int, denominator: int) -> Decimal | None:
    if denominator == 0:
        return None
    return (Decimal(numerator) / Decimal(denominator)).quantize(
        Decimal("0.0001"), rounding=ROUND_HALF_UP
    )

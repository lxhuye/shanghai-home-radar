from __future__ import annotations

from decimal import Decimal
from typing import Any

from home_radar_models.decision import DecisionAssessment
from home_radar_models.future import FutureAssessment
from home_radar_models.valuation import ValuationResult
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from home_radar_decision.domain import DecisionInputs


class DecisionInputsUnavailableError(LookupError):
    pass


def latest_decision_assessments(
    session: Session,
    *,
    data_mode: str,
    decision_model_version: str,
    configuration_version: str,
) -> list[DecisionAssessment]:
    latest = (
        select(
            DecisionAssessment.id.label("assessment_id"),
            func.row_number()
            .over(
                partition_by=DecisionAssessment.listing_id,
                order_by=(
                    DecisionAssessment.generated_at.desc(),
                    DecisionAssessment.id.desc(),
                ),
            )
            .label("revision"),
        )
        .where(
            DecisionAssessment.data_mode == data_mode,
            DecisionAssessment.decision_model_version == decision_model_version,
            DecisionAssessment.configuration_version == configuration_version,
        )
        .subquery()
    )
    return list(
        session.scalars(
            select(DecisionAssessment)
            .join(latest, latest.c.assessment_id == DecisionAssessment.id)
            .where(latest.c.revision == 1)
        )
    )


def load_decision_inputs(session: Session, future: FutureAssessment) -> DecisionInputs:
    valuation = session.scalar(
        select(ValuationResult).where(
            ValuationResult.listing_id == future.listing_id,
            ValuationResult.data_mode == future.data_mode,
            ValuationResult.valuation_version == future.valuation_version,
        )
    )
    if valuation is None:
        raise DecisionInputsUnavailableError(
            f"valuation {future.valuation_version} is unavailable for P5.5"
        )
    if valuation.baseline_version != future.baseline_version:
        raise DecisionInputsUnavailableError("P4 and P5 baseline version chains do not match")
    return DecisionInputs(
        listing_id=future.listing_id,
        data_mode=future.data_mode,
        current_ask=valuation.current_ask,
        fair_value=valuation.fair_value,
        fair_value_low=valuation.fair_value_low,
        fair_value_high=valuation.fair_value_high,
        ask_discount_to_fair_value=valuation.ask_discount_to_fair_value,
        value_score=valuation.value_score,
        liquidity_score=_liquidity_score(valuation.score_components),
        valuation_confidence=valuation.valuation_confidence,
        baseline_confidence=valuation.baseline_confidence,
        future_score=future.future_score,
        future_score_coverage=future.future_score_coverage,
        future_confidence=future.confidence,
        obsolescence_risk=future.obsolescence_risk,
        obsolescence_coverage=future.obsolescence_coverage,
        structural_alpha=future.structural_alpha,
        calibration_state=future.calibration_state,
        valuation_warnings=tuple(valuation.warnings),
        future_warnings=tuple(future.warnings),
        factor_breakdown=tuple(future.factor_breakdown),
        risk_breakdown=future.risk_breakdown,
        valuation_version=valuation.valuation_version,
        future_assessment_version=future.future_assessment_version,
        baseline_version=future.baseline_version,
        valuation_configuration_version=valuation.configuration_version,
        future_configuration_version=future.configuration_version,
        data_version=future.data_version,
        data_timestamp=future.data_timestamp,
    )


def _liquidity_score(score_components: dict[str, Any]) -> Decimal | None:
    liquidity = score_components.get("liquidity")
    if not isinstance(liquidity, dict):
        return None
    raw = liquidity.get("raw")
    if raw is None:
        return None
    try:
        value = Decimal(str(raw))
    except ArithmeticError as exc:
        raise DecisionInputsUnavailableError("P4 liquidity score is invalid") from exc
    if not Decimal("0") <= value <= Decimal("100"):
        raise DecisionInputsUnavailableError("P4 liquidity score is out of range")
    return value

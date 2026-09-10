from __future__ import annotations

from home_radar_decision.config import CONFIDENCE_LEVELS, DecisionConfig
from home_radar_decision.domain import (
    DecisionInputs,
    EligibilityStatus,
    OpportunityClassification,
    WorkflowState,
)


def confidence_gate_passed(inputs: DecisionInputs, config: DecisionConfig) -> bool:
    minimum = CONFIDENCE_LEVELS.index(config.eligibility.minimum_confidence)
    levels = (
        inputs.valuation_confidence,
        inputs.future_confidence,
        inputs.baseline_confidence,
    )
    return all(
        level in CONFIDENCE_LEVELS and CONFIDENCE_LEVELS.index(level) >= minimum for level in levels
    )


def hard_risks(inputs: DecisionInputs, config: DecisionConfig) -> tuple[str, ...]:
    risks = set(config.eligibility.hard_risk_warnings).intersection(
        inputs.valuation_warnings + inputs.future_warnings
    )
    if (
        inputs.obsolescence_risk is not None
        and inputs.obsolescence_risk >= config.eligibility.maximum_obsolescence_risk
    ):
        risks.add("HIGH_OBSOLESCENCE_RISK")
    return tuple(sorted(risks))


def eligibility_status(
    classification: OpportunityClassification,
    confidence_passed: bool,
    risks: tuple[str, ...],
) -> EligibilityStatus:
    if classification == "INSUFFICIENT_DATA" or not confidence_passed:
        return "INSUFFICIENT"
    if risks or classification in {"VALUE_TRAP", "LOW_QUALITY"}:
        return "HARD_RISK_FILTERED"
    return "ELIGIBLE"


def workflow_state(
    inputs: DecisionInputs,
    classification: OpportunityClassification,
    eligibility: EligibilityStatus,
    config: DecisionConfig,
) -> WorkflowState:
    if eligibility != "ELIGIBLE":
        return "PASS"
    if classification == "GOOD_BUT_EXPENSIVE":
        return "WATCH"
    future_score = inputs.future_score
    obsolescence = inputs.obsolescence_risk
    liquidity = inputs.liquidity_score
    if future_score is None or obsolescence is None or liquidity is None:
        return "WATCH"
    rules = config.workflow
    if (
        inputs.value_score >= rules.view_minimum_value_score
        and future_score >= rules.view_minimum_future_score
        and liquidity >= rules.view_minimum_liquidity_score
        and obsolescence <= rules.view_maximum_obsolescence_risk
    ):
        return "VIEW"
    if liquidity >= rules.contact_minimum_liquidity_score:
        return "CONTACT"
    return "WATCH"

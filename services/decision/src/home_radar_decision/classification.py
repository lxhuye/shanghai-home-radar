from __future__ import annotations

from home_radar_decision.config import ClassificationConfig
from home_radar_decision.domain import DecisionInputs, OpportunityClassification


def classify_opportunity(
    inputs: DecisionInputs, config: ClassificationConfig
) -> OpportunityClassification:
    if _insufficient(inputs, config):
        return "INSUFFICIENT_DATA"
    high_value = inputs.value_score >= config.high_value_score
    high_future = (
        inputs.future_score is not None and inputs.future_score >= config.high_future_score
    )
    if high_value and high_future:
        return "QUALITY_AT_DISCOUNT"
    if high_future:
        return "GOOD_BUT_EXPENSIVE"
    if high_value:
        return "VALUE_TRAP"
    return "LOW_QUALITY"


def _insufficient(inputs: DecisionInputs, config: ClassificationConfig) -> bool:
    return (
        inputs.future_score is None
        or inputs.obsolescence_risk is None
        or inputs.future_score_coverage < config.minimum_future_coverage
        or inputs.obsolescence_coverage < config.minimum_obsolescence_coverage
        or inputs.valuation_confidence == "insufficient"
        or inputs.future_confidence == "insufficient"
        or inputs.baseline_confidence == "insufficient"
    )

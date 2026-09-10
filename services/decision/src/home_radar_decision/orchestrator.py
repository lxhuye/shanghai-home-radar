from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from decimal import Decimal

from home_radar_decision.classification import classify_opportunity
from home_radar_decision.config import DecisionConfig
from home_radar_decision.domain import DecisionEvaluation, DecisionInputs
from home_radar_decision.why_ranked import build_reasons
from home_radar_decision.workflow import (
    confidence_gate_passed,
    eligibility_status,
    hard_risks,
    workflow_state,
)


class DecisionOrchestrator:
    def __init__(self, config: DecisionConfig) -> None:
        self.config = config

    def evaluate(self, inputs: DecisionInputs) -> DecisionEvaluation:
        _validate_inputs(inputs)
        classification = classify_opportunity(inputs, self.config.classification)
        confidence_passed = confidence_gate_passed(inputs, self.config)
        risks = hard_risks(inputs, self.config)
        eligibility = eligibility_status(classification, confidence_passed, risks)
        state = workflow_state(inputs, classification, eligibility, self.config)
        positive, negative = build_reasons(inputs, self.config)
        fingerprint = _fingerprint(inputs, self.config)
        return DecisionEvaluation(
            listing_id=inputs.listing_id,
            data_mode=inputs.data_mode,
            current_ask=inputs.current_ask,
            fair_value=inputs.fair_value,
            fair_value_low=inputs.fair_value_low,
            fair_value_high=inputs.fair_value_high,
            value_score=inputs.value_score,
            future_score=inputs.future_score,
            liquidity_score=inputs.liquidity_score,
            obsolescence_risk=inputs.obsolescence_risk,
            structural_alpha=inputs.structural_alpha,
            valuation_confidence=inputs.valuation_confidence,
            future_confidence=inputs.future_confidence,
            calibration_state=inputs.calibration_state,
            opportunity_classification=classification,
            workflow_state=state,
            eligibility_status=eligibility,
            confidence_gate_passed=confidence_passed,
            hard_risks=risks,
            positive_reasons=positive,
            negative_reasons=negative,
            warnings=tuple(dict.fromkeys(inputs.valuation_warnings + inputs.future_warnings)),
            ranking_dimensions=_ranking_dimensions(
                inputs, classification, confidence_passed, risks
            ),
            provenance={
                "engine": "deterministic_decision_orchestrator",
                "bottom_layer_metrics_recomputed": False,
                "opaque_total_score_created": False,
                "llm_used": False,
                "attack_enabled": False,
                "investment_recommendation_emitted": False,
            },
            valuation_version=inputs.valuation_version,
            future_assessment_version=inputs.future_assessment_version,
            baseline_version=inputs.baseline_version,
            decision_model_version=self.config.decision_model_version,
            configuration_version=self.config.configuration_version,
            data_version=inputs.data_version,
            data_timestamp=inputs.data_timestamp,
            input_fingerprint=fingerprint,
            decision_version=f"p55-{fingerprint[:32]}",
            generated_at=datetime.now(UTC),
        )


def _validate_inputs(inputs: DecisionInputs) -> None:
    if inputs.data_mode not in {"demo", "sample", "live"}:
        raise ValueError("data mode must be demo, sample, or live")
    if inputs.current_ask <= 0 or inputs.fair_value <= 0:
        raise ValueError("P4 price inputs must be positive")
    if not inputs.fair_value_low <= inputs.fair_value <= inputs.fair_value_high:
        raise ValueError("P4 fair-value range is invalid")
    if not Decimal("0") <= inputs.value_score <= Decimal("100"):
        raise ValueError("P4 Value Score must be between 0 and 100")


def _fingerprint(inputs: DecisionInputs, config: DecisionConfig) -> str:
    payload = {
        "listing_id": str(inputs.listing_id),
        "data_mode": inputs.data_mode,
        "baseline_version": inputs.baseline_version,
        "valuation_version": inputs.valuation_version,
        "future_assessment_version": inputs.future_assessment_version,
        "data_version": inputs.data_version,
        "decision_model_version": config.decision_model_version,
        "configuration_version": config.configuration_version,
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def _ranking_dimensions(
    inputs: DecisionInputs,
    classification: str,
    confidence_passed: bool,
    risks: tuple[str, ...],
) -> dict[str, str | int]:
    return {
        "eligible_universe": "included" if classification != "INSUFFICIENT_DATA" else "excluded",
        "confidence_gate": "passed" if confidence_passed else "failed",
        "hard_risk_filter": "passed" if not risks else "failed",
        "quadrant": classification,
        "liquidity": (
            str(inputs.liquidity_score) if inputs.liquidity_score is not None else "missing"
        ),
    }

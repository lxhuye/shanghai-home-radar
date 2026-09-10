from __future__ import annotations

import uuid
from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from home_radar_decision.config import DecisionConfig, load_decision_config
from home_radar_decision.domain import BlindEvaluationCase, DecisionInputs
from home_radar_decision.orchestrator import DecisionOrchestrator
from home_radar_decision.ranking import rank_eligible
from home_radar_decision.validation import calculate_blind_metrics

AS_OF = datetime(2026, 9, 2, tzinfo=UTC)


@pytest.mark.parametrize(
    ("value", "future", "expected"),
    [
        ("85", "84", "QUALITY_AT_DISCOUNT"),
        ("60", "84", "GOOD_BUT_EXPENSIVE"),
        ("85", "50", "VALUE_TRAP"),
        ("60", "50", "LOW_QUALITY"),
    ],
)
def test_four_quadrants_are_explicit(value: str, future: str, expected: str) -> None:
    result = _engine().evaluate(_inputs(value_score=Decimal(value), future_score=Decimal(future)))
    assert result.opportunity_classification == expected


def test_high_value_low_future_is_filtered_as_value_trap() -> None:
    result = _engine().evaluate(
        _inputs(
            value_score=Decimal("94"),
            future_score=Decimal("51"),
            liquidity_score=Decimal("58"),
            obsolescence_risk=Decimal("71"),
        )
    )
    assert result.opportunity_classification == "VALUE_TRAP"
    assert result.eligibility_status == "HARD_RISK_FILTERED"
    assert result.workflow_state == "PASS"
    assert "HIGH_OBSOLESCENCE_RISK" in result.hard_risks


def test_price_decline_only_changes_p4_value_dimension() -> None:
    base = _inputs(
        current_ask=Decimal("3000000"),
        value_score=Decimal("70"),
        future_score=Decimal("51"),
        obsolescence_risk=Decimal("71"),
    )
    paths = [
        (Decimal("3000000"), Decimal("70")),
        (Decimal("2800000"), Decimal("78")),
        (Decimal("2600000"), Decimal("86")),
        (Decimal("2400000"), Decimal("94")),
    ]
    results = [
        _engine().evaluate(
            replace(
                base,
                current_ask=ask,
                value_score=value,
                ask_discount_to_fair_value=(base.fair_value - ask) / base.fair_value,
                valuation_version=f"p4-{ask}",
            )
        )
        for ask, value in paths
    ]
    assert [item.value_score for item in results] == [item[1] for item in paths]
    assert {item.future_score for item in results} == {Decimal("51")}
    assert {item.obsolescence_risk for item in results} == {Decimal("71")}
    assert results[-1].opportunity_classification == "VALUE_TRAP"
    assert all(item.workflow_state == "PASS" for item in results)


def test_ranking_excludes_trap_and_prefers_quality_quadrant_before_liquidity() -> None:
    strong = _engine().evaluate(
        _inputs(
            listing_id=uuid.UUID(int=1),
            value_score=Decimal("88"),
            future_score=Decimal("86"),
            liquidity_score=Decimal("91"),
            obsolescence_risk=Decimal("22"),
        )
    )
    trap = _engine().evaluate(
        _inputs(
            listing_id=uuid.UUID(int=2),
            value_score=Decimal("94"),
            future_score=Decimal("51"),
            liquidity_score=Decimal("99"),
            obsolescence_risk=Decimal("71"),
        )
    )
    expensive = _engine().evaluate(
        _inputs(
            listing_id=uuid.UUID(int=3),
            value_score=Decimal("70"),
            future_score=Decimal("95"),
            liquidity_score=Decimal("100"),
            obsolescence_risk=Decimal("20"),
        )
    )
    ranked = rank_eligible([trap, expensive, strong], load_decision_config())
    assert [item.evaluation.listing_id for item in ranked] == [
        strong.listing_id,
        expensive.listing_id,
    ]
    assert ranked[0].rank == 1
    assert ranked[0].eligible_count == 2


def test_ranking_handles_daily_3000_listing_universe_deterministically() -> None:
    base = _engine().evaluate(_inputs())
    evaluations = [
        replace(
            base,
            listing_id=uuid.UUID(int=index + 1),
            liquidity_score=Decimal(50 + index % 50),
            value_score=Decimal(75 + index % 20),
        )
        for index in range(3000)
    ]
    first = rank_eligible(evaluations, load_decision_config())
    second = rank_eligible(list(reversed(evaluations)), load_decision_config())
    assert len(first) == 3000
    assert [item.evaluation.listing_id for item in first] == [
        item.evaluation.listing_id for item in second
    ]
    assert first[-1].rank == 3000


def test_view_is_workflow_only_and_attack_is_absent() -> None:
    result = _engine().evaluate(_inputs())
    assert result.workflow_state == "VIEW"
    assert result.provenance["investment_recommendation_emitted"] is False
    assert result.provenance["attack_enabled"] is False
    assert any(reason.code == "FAIR_VALUE_DISCOUNT" for reason in result.positive_reasons)
    assert any(reason.code == "UNCALIBRATED_MODEL" for reason in result.negative_reasons)


def test_insufficient_coverage_is_not_rankable() -> None:
    result = _engine().evaluate(_inputs(future_score_coverage=Decimal("0.4")))
    assert result.opportunity_classification == "INSUFFICIENT_DATA"
    assert result.eligibility_status == "INSUFFICIENT"
    assert result.workflow_state == "PASS"


def test_blind_evaluation_metrics_use_strict_and_accepted_labels() -> None:
    cases = [
        _case(1, "VIEW", "WORTH_VIEWING"),
        _case(2, "CONTACT", "WAIT"),
        _case(3, "VIEW", "VALUE_TRAP"),
        _case(None, "PASS", "VALUE_TRAP", eligibility="HARD_RISK_FILTERED"),
    ]
    metrics = calculate_blind_metrics(cases, load_decision_config().validation)
    assert metrics.labeled_count == 4
    assert metrics.precision_at_5 == Decimal("0.3333")
    assert metrics.precision_at_10 == Decimal("0.3333")
    assert metrics.value_trap_false_positive_rate == Decimal("0.5000")
    assert metrics.view_acceptance_rate == Decimal("0.5000")
    assert metrics.top_10_manual_acceptance_rate == Decimal("0.6667")


def test_configuration_rejects_attack_enablement() -> None:
    payload = load_decision_config().model_dump()
    payload["workflow"]["attack_enabled"] = True
    with pytest.raises(ValueError, match="ATTACK is disabled"):
        DecisionConfig.model_validate(payload)


def _engine() -> DecisionOrchestrator:
    return DecisionOrchestrator(load_decision_config())


def _inputs(**overrides: object) -> DecisionInputs:
    values: dict[str, object] = {
        "listing_id": uuid.uuid4(),
        "data_mode": "sample",
        "current_ask": Decimal("2980000"),
        "fair_value": Decimal("3180000"),
        "fair_value_low": Decimal("3050000"),
        "fair_value_high": Decimal("3290000"),
        "ask_discount_to_fair_value": Decimal("0.062893"),
        "value_score": Decimal("87"),
        "liquidity_score": Decimal("91"),
        "valuation_confidence": "high",
        "baseline_confidence": "high",
        "future_score": Decimal("84"),
        "future_score_coverage": Decimal("0.90"),
        "future_confidence": "medium",
        "obsolescence_risk": Decimal("26"),
        "obsolescence_coverage": Decimal("0.85"),
        "structural_alpha": Decimal("12"),
        "calibration_state": "uncalibrated",
        "valuation_warnings": (),
        "future_warnings": ("UNCALIBRATED_MODEL",),
        "factor_breakdown": (),
        "risk_breakdown": {},
        "valuation_version": "p4-v1",
        "future_assessment_version": "p5-v1",
        "baseline_version": "p3-v1",
        "valuation_configuration_version": "valuation-config-v1",
        "future_configuration_version": "future-config-v1",
        "data_version": "data-v1",
        "data_timestamp": AS_OF,
    }
    values.update(overrides)
    return DecisionInputs(**values)  # type: ignore[arg-type]


def _case(
    rank: int | None,
    workflow: str,
    label: str,
    *,
    eligibility: str = "ELIGIBLE",
) -> BlindEvaluationCase:
    return BlindEvaluationCase(
        listing_id=uuid.uuid4(),
        rank=rank,
        opportunity_classification="QUALITY_AT_DISCOUNT",
        workflow_state=workflow,  # type: ignore[arg-type]
        eligibility_status=eligibility,  # type: ignore[arg-type]
        human_label=label,  # type: ignore[arg-type]
    )

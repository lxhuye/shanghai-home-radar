from __future__ import annotations

from decimal import Decimal

from home_radar_decision.p56_config import ValidationGateConfig
from home_radar_decision.p56_metrics import calculate_real_world_metrics, gate_recommendation


def test_real_world_metrics_cover_primary_gate_and_value_traps() -> None:
    cases = [_case(rank) for rank in range(1, 13)]
    reviews = {
        case["listing_id"]: {
            "why_ranked_verdict": "CONTRADICT" if case["rank"] == 5 else "AGREE",
            "root_cause": "DECISION_RULE_ERROR" if case["rank"] == 5 else None,
        }
        for case in cases
    }

    metrics = calculate_real_world_metrics(cases, reviews)

    assert metrics["precision_at_5"] == Decimal("0.8000")
    assert metrics["precision_at_10"] == Decimal("0.7000")
    assert metrics["human_acceptance_at_10"] == Decimal("0.7000")
    assert metrics["value_traps_at_5"] == 1
    assert metrics["value_traps_at_10"] == 1
    assert metrics["value_trap_false_positive_rate"] == Decimal("1.0000")
    assert metrics["human_view_recall"] == Decimal("1.0000")
    assert metrics["regret_at_5"] == 1
    assert metrics["why_ranked_major_contradiction_rate"] == Decimal("0.0833")
    assert metrics["root_cause_distribution"] == {"DECISION_RULE_ERROR": 1}

    recommendation, checks = gate_recommendation(metrics, _gate())
    assert recommendation == "P6_READY"
    assert all(checks.values())


def test_gate_returns_ready_with_limitations_when_only_effectiveness_fails() -> None:
    metrics = calculate_real_world_metrics(
        [
            _case(rank)
            | {
                "human_class": "GOOD_BUT_EXPENSIVE",
                "human_would_visit": rank <= 7,
            }
            for rank in range(1, 11)
        ],
        {
            f"listing-{rank}": {
                "why_ranked_verdict": "AGREE",
                "root_cause": None,
            }
            for rank in range(1, 11)
        },
    )

    recommendation, checks = gate_recommendation(metrics, _gate())

    assert recommendation == "P6_READY_WITH_LIMITATIONS"
    assert checks["precision_at_5"] is False
    assert checks["regret_at_5"] is True


def test_major_contradiction_threshold_is_strict() -> None:
    metrics = {
        "precision_at_5": Decimal("0.8000"),
        "precision_at_10": Decimal("0.7000"),
        "human_acceptance_at_10": Decimal("0.7000"),
        "value_traps_at_10": 1,
        "human_view_recall": Decimal("0.8000"),
        "why_ranked_major_contradiction_rate": Decimal("0.1500"),
        "regret_at_5": 1,
    }

    recommendation, checks = gate_recommendation(metrics, _gate())

    assert checks["why_ranked_major_contradiction_rate"] is False
    assert recommendation == "P5_CALIBRATION_REQUIRED"


def _case(rank: int) -> dict[str, object]:
    human_class = (
        "QUALITY_AT_DISCOUNT"
        if rank in {1, 2, 3, 4, 6, 7, 8}
        else "VALUE_TRAP"
        if rank == 5
        else "LOW_QUALITY"
    )
    human_workflow = "VIEW" if human_class == "QUALITY_AT_DISCOUNT" else "PASS"
    return {
        "listing_id": f"listing-{rank}",
        "rank": rank,
        "human_class": human_class,
        "human_workflow": human_workflow,
        "human_would_visit": human_class == "QUALITY_AT_DISCOUNT",
        "model_class": "QUALITY_AT_DISCOUNT",
        "model_workflow": "VIEW" if human_class == "QUALITY_AT_DISCOUNT" else "PASS",
        "model_eligibility": "ELIGIBLE",
        "valuation_confidence": "high",
        "future_confidence": "high",
    }


def _gate() -> ValidationGateConfig:
    return ValidationGateConfig(
        precision_at_5=Decimal("0.80"),
        precision_at_10=Decimal("0.70"),
        human_acceptance_at_10=Decimal("0.70"),
        maximum_value_traps_at_10=1,
        human_view_recall=Decimal("0.80"),
        maximum_why_ranked_major_contradiction_rate=Decimal("0.15"),
        maximum_regret_at_5=1,
    )

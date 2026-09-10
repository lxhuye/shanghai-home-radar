from __future__ import annotations

from collections import Counter
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from home_radar_decision.p56_config import ValidationGateConfig

HUMAN_CLASSES = (
    "QUALITY_AT_DISCOUNT",
    "GOOD_BUT_EXPENSIVE",
    "VALUE_TRAP",
    "LOW_QUALITY",
    "INSUFFICIENT_INFORMATION",
)
MODEL_CLASSES = (
    "QUALITY_AT_DISCOUNT",
    "GOOD_BUT_EXPENSIVE",
    "VALUE_TRAP",
    "LOW_QUALITY",
    "INSUFFICIENT_DATA",
)


def calculate_real_world_metrics(
    cases: list[dict[str, Any]], reviews: dict[str, dict[str, Any]] | None = None
) -> dict[str, Any]:
    reviews = reviews or {}
    ranked = sorted(
        (case for case in cases if case.get("rank") is not None),
        key=lambda case: int(case["rank"]),
    )
    top5 = ranked[:5]
    top10 = ranked[:10]
    human_views = [case for case in cases if case["human_workflow"] == "VIEW"]
    human_traps = [case for case in cases if case["human_class"] == "VALUE_TRAP"]
    confusion = {human: {model: 0 for model in MODEL_CLASSES} for human in HUMAN_CLASSES}
    for case in cases:
        confusion[case["human_class"]][case["model_class"]] += 1

    errors = [case for case in cases if _is_error(case)]
    high_confidence = [case for case in cases if _confidence_band(case) == "high"]
    low_confidence = [case for case in cases if _confidence_band(case) == "low"]
    reviewed = [
        reviews[str(case["listing_id"])] for case in cases if str(case["listing_id"]) in reviews
    ]
    root_causes = Counter(review["root_cause"] for review in reviewed if review.get("root_cause"))

    return {
        "sample_size": len(cases),
        "ranked_count": len(ranked),
        "precision_at_5": _rate(_class_count(top5, "QUALITY_AT_DISCOUNT"), len(top5)),
        "precision_at_10": _rate(_class_count(top10, "QUALITY_AT_DISCOUNT"), len(top10)),
        "human_acceptance_at_5": _rate(_visit_count(top5), len(top5)),
        "human_acceptance_at_10": _rate(_visit_count(top10), len(top10)),
        "human_view_recall": _rate(
            sum(case["model_workflow"] == "VIEW" for case in human_views),
            len(human_views),
        ),
        "value_trap_false_positive_rate": _rate(
            sum(
                case["model_class"] != "VALUE_TRAP"
                and (case["model_eligibility"] == "ELIGIBLE" or case["model_workflow"] == "VIEW")
                for case in human_traps
            ),
            len(human_traps),
        ),
        "value_traps_at_5": _class_count(top5, "VALUE_TRAP"),
        "value_traps_at_10": _class_count(top10, "VALUE_TRAP"),
        "value_trap_rate_at_5": _rate(_class_count(top5, "VALUE_TRAP"), len(top5)),
        "value_trap_rate_at_10": _rate(_class_count(top10, "VALUE_TRAP"), len(top10)),
        "workflow_accuracy": _rate(
            sum(case["human_workflow"] == case["model_workflow"] for case in cases),
            len(cases),
        ),
        "opportunity_confusion_matrix": confusion,
        "why_ranked_agreement_rate": _rate(
            sum(review["why_ranked_verdict"] == "AGREE" for review in reviewed),
            len(reviewed),
        ),
        "why_ranked_major_contradiction_rate": _rate(
            sum(review["why_ranked_verdict"] == "CONTRADICT" for review in reviewed),
            len(reviewed),
        ),
        "reviewed_count": len(reviewed),
        "error_count": len(errors),
        "high_confidence_error_rate": _rate(
            sum(_is_error(case) for case in high_confidence), len(high_confidence)
        ),
        "low_confidence_error_rate": _rate(
            sum(_is_error(case) for case in low_confidence), len(low_confidence)
        ),
        "regret_at_5": sum(not case["human_would_visit"] for case in top5),
        "root_cause_distribution": dict(sorted(root_causes.items())),
    }


def gate_recommendation(
    metrics: dict[str, Any], config: ValidationGateConfig
) -> tuple[str, dict[str, bool]]:
    checks = {
        "precision_at_5": _gte(metrics["precision_at_5"], config.precision_at_5),
        "precision_at_10": _gte(metrics["precision_at_10"], config.precision_at_10),
        "human_acceptance_at_10": _gte(
            metrics["human_acceptance_at_10"], config.human_acceptance_at_10
        ),
        "value_traps_at_10": metrics["value_traps_at_10"] <= config.maximum_value_traps_at_10,
        "human_view_recall": _gte(metrics["human_view_recall"], config.human_view_recall),
        "why_ranked_major_contradiction_rate": _lte(
            metrics["why_ranked_major_contradiction_rate"],
            config.maximum_why_ranked_major_contradiction_rate,
        ),
        "regret_at_5": metrics["regret_at_5"] <= config.maximum_regret_at_5,
    }
    if all(checks.values()):
        return "P6_READY", checks
    safety = (
        checks["value_traps_at_10"]
        and checks["why_ranked_major_contradiction_rate"]
        and checks["regret_at_5"]
    )
    if safety:
        return "P6_READY_WITH_LIMITATIONS", checks
    return "P5_CALIBRATION_REQUIRED", checks


def _class_count(cases: list[dict[str, Any]], human_class: str) -> int:
    return sum(case["human_class"] == human_class for case in cases)


def _visit_count(cases: list[dict[str, Any]]) -> int:
    return sum(bool(case["human_would_visit"]) for case in cases)


def _is_error(case: dict[str, Any]) -> bool:
    human_class = str(case["human_class"]).replace("INSUFFICIENT_INFORMATION", "INSUFFICIENT_DATA")
    return human_class != str(case["model_class"]) or str(case["human_workflow"]) != str(
        case["model_workflow"]
    )


def _confidence_band(case: dict[str, Any]) -> str:
    levels = {
        str(case.get("valuation_confidence", "low")).lower(),
        str(case.get("future_confidence", "low")).lower(),
    }
    if levels == {"high"}:
        return "high"
    if levels & {"low", "insufficient"}:
        return "low"
    return "medium"


def _rate(numerator: int, denominator: int) -> Decimal | None:
    if denominator == 0:
        return None
    return (Decimal(numerator) / Decimal(denominator)).quantize(
        Decimal("0.0001"), rounding=ROUND_HALF_UP
    )


def _gte(actual: Decimal | None, expected: Decimal) -> bool:
    return actual is not None and actual >= expected


def _lte(actual: Decimal | None, expected: Decimal) -> bool:
    return actual is not None and actual < expected

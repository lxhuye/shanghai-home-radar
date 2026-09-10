from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from home_radar_models.enums import BaselineConfidence

from home_radar_valuation.config import ValuationConfig
from home_radar_valuation.domain import TargetProperty, floor_category
from home_radar_valuation.scoring import decision_for_score


@dataclass(frozen=True)
class WarningResult:
    warnings: tuple[str, ...]
    capped_score: Decimal
    decision: str


def apply_warning_rules(
    target: TargetProperty,
    discount: Decimal,
    liquidity_score: Decimal | None,
    comparable_count: int,
    baseline_confidence: str,
    valuation_confidence: str,
    transaction_support: str,
    score: Decimal,
    config: ValuationConfig,
) -> WarningResult:
    rules = config.warnings
    warnings: set[str] = set()
    low_liquidity = liquidity_score is None or liquidity_score < rules.low_liquidity_score
    insufficient_sample = comparable_count < rules.insufficient_comparable_count
    extreme_floor = (
        floor_category(target.floor, target.total_floors) == "top" and target.elevator is False
    )
    building_type = (target.building_type or "").lower()
    abnormal_product = any(
        token.lower() in building_type for token in rules.abnormal_property_types
    )
    low_baseline = baseline_confidence in {
        BaselineConfidence.LOW.value,
        BaselineConfidence.INSUFFICIENT.value,
    }

    if low_liquidity:
        warnings.add("PRODUCT_LIQUIDITY_RISK")
    if insufficient_sample:
        warnings.add("LOW_SAMPLE_CONFIDENCE")
    if extreme_floor:
        warnings.add("EXTREME_FLOOR_RISK")
    if transaction_support == "none" or low_baseline:
        warnings.add("SOURCE_DATA_LIMITATION")
    if discount >= rules.large_discount_rate and (
        low_liquidity or insufficient_sample or extreme_floor or abnormal_product or low_baseline
    ):
        warnings.add("VALUE_TRAP_RISK")

    capped = score
    if "VALUE_TRAP_RISK" in warnings:
        capped = min(capped, rules.trap_score_cap)
    if valuation_confidence == BaselineConfidence.INSUFFICIENT.value:
        capped = min(capped, rules.insufficient_confidence_score_cap)
    return WarningResult(
        warnings=tuple(sorted(warnings)),
        capped_score=capped,
        decision=decision_for_score(capped, config.value_score),
    )

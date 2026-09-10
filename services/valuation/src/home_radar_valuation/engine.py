from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime
from decimal import ROUND_HALF_UP, Decimal
from typing import Any
from zoneinfo import ZoneInfo

from home_radar_models.enums import ValuationBasis

from home_radar_valuation.adjustments import (
    adjust_comparable,
    target_missing_attributes,
)
from home_radar_valuation.aggregation import aggregate_fair_value
from home_radar_valuation.comparables import (
    effective_comparable_count,
    select_comparables,
)
from home_radar_valuation.confidence import calculate_confidence
from home_radar_valuation.config import ValuationConfig
from home_radar_valuation.domain import AdjustedComparable, BaselineEvidence
from home_radar_valuation.repository import EvaluationInputs
from home_radar_valuation.scoring import calculate_value_score
from home_radar_valuation.warnings import apply_warning_rules

ZERO = Decimal("0")


@dataclass(frozen=True)
class ValuationEvaluation:
    listing_id: str | None
    data_mode: str
    data_notice: str
    recommendation_status: str
    fair_value: Decimal
    fair_value_low: Decimal
    fair_value_high: Decimal
    valuation_basis: str
    valuation_confidence: str
    valuation_confidence_score: Decimal
    transaction_support: str
    current_ask: Decimal
    ask_discount_to_fair_value: Decimal
    estimated_executable_price: Decimal | None
    executable_discount_to_fair_value: Decimal | None
    value_score: Decimal
    decision: str
    comparable_count: int
    effective_comparable_count: Decimal
    baseline_level_used: str
    baseline_confidence: str
    baseline_version: str
    fallback_reason: str | None
    adjustments: list[dict[str, Any]]
    comparables: list[dict[str, Any]]
    warnings: list[str]
    score_components: dict[str, Any]
    price_history: dict[str, Any]
    provenance: dict[str, Any]
    valuation_model_version: str
    scoring_model_version: str
    configuration_version: str
    valuation_version: str
    input_fingerprint: str
    generated_at: datetime
    baseline_generated_at: datetime | None


class DeterministicValuationEngine:
    def __init__(self, config: ValuationConfig) -> None:
        self.config = config

    def evaluate(
        self,
        inputs: EvaluationInputs,
        as_of: datetime,
        *,
        input_fingerprint: str | None = None,
    ) -> ValuationEvaluation:
        if as_of.tzinfo is None:
            raise ValueError("as_of must be timezone-aware")
        target = inputs.target
        selection = select_comparables(target, inputs.candidates, as_of, self.config.comparables)
        adjusted = tuple(
            adjust_comparable(target, comparable, self.config.adjustments)
            for comparable in selection.comparables
        )
        missing = target_missing_attributes(target)
        aggregation = aggregate_fair_value(
            target,
            adjusted,
            inputs.valuation_baseline,
            self.config.comparables,
            self.config.adjustments,
            len(missing),
        )
        retained = aggregation.retained_comparables
        confidence = calculate_confidence(
            selection,
            retained,
            inputs.valuation_baseline,
            aggregation.dispersion_rate,
            len(missing),
            self.config.confidence,
            self.config.comparables,
        )
        liquidity = (
            inputs.listing_baseline.liquidity_score if inputs.listing_baseline is not None else None
        )
        median_year = _median_year(retained)
        score = calculate_value_score(
            target,
            aggregation.fair_value,
            liquidity,
            inputs.price_history,
            median_year,
            self.config.value_score,
        )
        discount = _ratio(aggregation.fair_value - target.current_ask, aggregation.fair_value)
        baseline_confidence = (
            inputs.valuation_baseline.confidence
            if inputs.valuation_baseline is not None
            else "insufficient"
        )
        warnings = apply_warning_rules(
            target,
            discount,
            liquidity,
            len(retained),
            baseline_confidence,
            confidence.level,
            confidence.transaction_support,
            score.score,
            self.config,
        )
        executable = target.documented_executable_price
        executable_discount = (
            _ratio(aggregation.fair_value - executable, aggregation.fair_value)
            if executable is not None
            else None
        )
        fingerprint = input_fingerprint or build_input_fingerprint(inputs, self.config, as_of)
        valuation_version = f"p4-{fingerprint[:32]}"
        baseline_version = _baseline_version(inputs)
        return ValuationEvaluation(
            listing_id=str(target.listing_id) if target.listing_id is not None else None,
            data_mode=target.data_mode,
            data_notice=_data_notice(target.data_mode),
            recommendation_status=("live_analysis" if target.data_mode == "live" else "demo_only"),
            fair_value=aggregation.fair_value,
            fair_value_low=aggregation.fair_value_low,
            fair_value_high=aggregation.fair_value_high,
            valuation_basis=_valuation_basis(retained, inputs),
            valuation_confidence=confidence.level,
            valuation_confidence_score=confidence.score,
            transaction_support=confidence.transaction_support,
            current_ask=target.current_ask,
            ask_discount_to_fair_value=discount,
            estimated_executable_price=executable,
            executable_discount_to_fair_value=executable_discount,
            value_score=warnings.capped_score,
            decision=warnings.decision,
            comparable_count=len(retained),
            effective_comparable_count=_retained_effective_count(retained),
            baseline_level_used=(
                inputs.valuation_baseline.level_used
                if inputs.valuation_baseline is not None
                else "none"
            ),
            baseline_confidence=baseline_confidence,
            baseline_version=baseline_version,
            fallback_reason=_fallback_reason(selection.fallback_reason, inputs),
            adjustments=_adjustment_explanation(retained, aggregation.fair_value),
            comparables=_comparable_explanation(retained),
            warnings=list(warnings.warnings),
            score_components={
                **_json_safe(score.components),
                "uncapped_score": str(score.score),
                "liquidity_baseline": str(liquidity) if liquidity is not None else None,
            },
            price_history=_json_safe(asdict(inputs.price_history)),
            provenance={
                "engine": "deterministic_rule_based",
                "llm_used_for_pricing": False,
                "asking_price_used_in_fair_value": False,
                "seller_signal_used_in_fair_value": False,
                "baseline_level_effects": [
                    "district",
                    "submarket",
                    "community",
                    "area_bucket",
                    "layout",
                ],
                "unit_residual_adjustments": [
                    "floor_elevator_interaction",
                    "orientation",
                    "relative_building_age",
                    "intra_community_metro",
                    "layout_quality",
                    "road_noise",
                ],
                "missing_target_attributes": list(missing),
                "comparable_window_days": selection.window_days,
                "comparable_candidates_considered": len(inputs.candidates),
                "outlier_comparables_removed": len(adjusted) - len(retained),
                "baseline_versions": list(
                    inputs.valuation_baseline.baseline_versions
                    if inputs.valuation_baseline is not None
                    else ()
                ),
                "baseline_fallback_path": list(
                    inputs.valuation_baseline.fallback_path
                    if inputs.valuation_baseline is not None
                    else ()
                ),
                "confidence_components": _json_safe(confidence.components),
                "interval_rate": str(aggregation.interval_rate),
                "dispersion_rate": str(aggregation.dispersion_rate),
                "executable_price_evidence": target.executable_price_evidence,
                "investment_recommendation": False,
            },
            valuation_model_version=self.config.valuation_model_version,
            scoring_model_version=self.config.scoring_model_version,
            configuration_version=self.config.configuration_version,
            valuation_version=valuation_version,
            input_fingerprint=fingerprint,
            generated_at=datetime.now(UTC),
            baseline_generated_at=(
                inputs.valuation_baseline.generated_at
                if inputs.valuation_baseline is not None
                else None
            ),
        )


def build_input_fingerprint(
    inputs: EvaluationInputs, config: ValuationConfig, as_of: datetime
) -> str:
    target = asdict(inputs.target)
    target["listing_id"] = str(inputs.target.listing_id) if inputs.target.listing_id else None
    payload = {
        "target": _json_safe(target),
        "latest_observation_id": (
            str(inputs.latest_observation_id) if inputs.latest_observation_id else None
        ),
        "candidates": [
            {
                "id": str(item.observation_id),
                "observed_at": item.observed_at.isoformat(),
                "price": str(item.total_price),
                "unit_price": str(item.unit_price),
                "source_confidence": str(item.source_confidence),
                "metadata": _json_safe(item.metadata),
            }
            for item in sorted(inputs.candidates, key=lambda item: str(item.observation_id))
        ],
        "valuation_baseline": _baseline_payload(inputs.valuation_baseline),
        "listing_baseline": _baseline_payload(inputs.listing_baseline),
        "price_history": _json_safe(asdict(inputs.price_history)),
        "versions": {
            "valuation": config.valuation_model_version,
            "scoring": config.scoring_model_version,
            "configuration": config.configuration_version,
        },
        "evaluation_date_shanghai": (
            as_of.astimezone(ZoneInfo("Asia/Shanghai")).date().isoformat()
        ),
    }
    encoded = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


def _valuation_basis(comparables: tuple[AdjustedComparable, ...], inputs: EvaluationInputs) -> str:
    evidence = {item.selected.record.observation_type for item in comparables}
    if inputs.valuation_baseline is not None:
        evidence.add(inputs.valuation_baseline.observation_type)
    has_transaction = "transaction" in evidence
    has_listing = "listing" in evidence
    if has_transaction and has_listing:
        return ValuationBasis.MIXED_SOURCE.value
    if has_transaction:
        return ValuationBasis.TRANSACTION_SUPPORTED.value
    return ValuationBasis.LISTING_DERIVED.value


def _adjustment_explanation(
    comparables: tuple[AdjustedComparable, ...], fair_value: Decimal
) -> list[dict[str, Any]]:
    totals: dict[str, Decimal] = {}
    reasons: dict[str, set[str]] = {}
    evidence: dict[str, set[str]] = {}
    for comparable in comparables:
        for adjustment in comparable.adjustments:
            totals[adjustment.factor] = totals.get(adjustment.factor, ZERO) + (
                comparable.selected.weight * adjustment.rate
            )
            reasons.setdefault(adjustment.factor, set()).add(adjustment.reason)
            evidence.setdefault(adjustment.factor, set()).add(adjustment.evidence)
    return [
        {
            "factor": factor,
            "rate": str(rate.quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP)),
            "amount": str((fair_value * rate).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)),
            "reason": "; ".join(sorted(reasons[factor])),
            "evidence": "; ".join(sorted(evidence[factor])),
        }
        for factor, rate in sorted(totals.items())
    ]


def _comparable_explanation(
    comparables: tuple[AdjustedComparable, ...],
) -> list[dict[str, Any]]:
    ordered = sorted(
        comparables,
        key=lambda item: (-item.selected.weight, str(item.selected.record.observation_id)),
    )
    return [
        {
            "observation_id": str(item.selected.record.observation_id),
            "listing_id": (
                str(item.selected.record.listing_id)
                if item.selected.record.listing_id is not None
                else None
            ),
            "observation_type": item.selected.record.observation_type,
            "source": item.selected.record.source,
            "observed_at": item.selected.record.observed_at.isoformat(),
            "tier": item.selected.tier,
            "similarity_score": str(item.selected.similarity_score),
            "weight": str(item.selected.weight),
            "reason": list(item.selected.reasons),
            "area_sqm": str(item.selected.record.area_sqm),
            "raw_total_price": str(item.selected.record.total_price),
            "raw_unit_price": str(item.selected.record.unit_price),
            "adjusted_unit_price": str(item.adjusted_unit_price),
            "adjusted_value": str(item.adjusted_value),
            "total_adjustment_rate": str(item.total_adjustment_rate),
            "adjustments": [
                {
                    "factor": adjustment.factor,
                    "rate": str(adjustment.rate),
                    "amount": str(adjustment.amount),
                    "reason": adjustment.reason,
                    "evidence": adjustment.evidence,
                }
                for adjustment in item.adjustments
            ],
        }
        for item in ordered
    ]


def _retained_effective_count(
    comparables: tuple[AdjustedComparable, ...],
) -> Decimal:
    total = sum((item.selected.weight for item in comparables), ZERO)
    if total == 0:
        return ZERO
    normalized = (item.selected.weight / total for item in comparables)
    return effective_comparable_count(normalized)


def _median_year(comparables: tuple[AdjustedComparable, ...]) -> int | None:
    years = sorted(
        item.selected.record.year_built
        for item in comparables
        if item.selected.record.year_built is not None
    )
    if not years:
        return None
    return years[len(years) // 2]


def _fallback_reason(selection_reason: str | None, inputs: EvaluationInputs) -> str | None:
    values = [selection_reason]
    if inputs.valuation_baseline is not None:
        values.append(inputs.valuation_baseline.fallback_reason)
    filtered = [value for value in values if value]
    return "; ".join(filtered) if filtered else None


def _baseline_version(inputs: EvaluationInputs) -> str:
    versions: set[str] = set()
    for baseline in (inputs.valuation_baseline, inputs.listing_baseline):
        if baseline is not None:
            versions.update(baseline.baseline_versions)
    if not versions:
        return "none"
    joined = "|".join(sorted(versions))
    return f"p3set-{hashlib.sha256(joined.encode()).hexdigest()[:40]}"


def _baseline_payload(value: BaselineEvidence | None) -> Any:
    if value is None:
        return None
    return _json_safe(asdict(value))


def _json_safe(value: Any) -> Any:
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    return value


def _ratio(numerator: Decimal, denominator: Decimal) -> Decimal:
    return (numerator / denominator).quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP)


def _data_notice(data_mode: str) -> str:
    if data_mode == "live":
        return "LIVE authorized evidence; this is analysis, not an investment recommendation."
    if data_mode == "demo":
        return "DEMO synthetic evidence; never treat this output as a live recommendation."
    return "SAMPLE evidence; this output is DEMO-only and not a live recommendation."

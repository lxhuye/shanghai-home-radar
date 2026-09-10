from __future__ import annotations

from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal

from home_radar_forecasting.accessibility import (
    employment_accessibility,
    planning_realization_factor,
    transport_accessibility,
)
from home_radar_forecasting.config import FutureConfig
from home_radar_forecasting.domain import (
    ALL_FUTURE_FACTORS,
    FactorEvidence,
    FutureInputs,
    FutureProjectInput,
)


def build_future_factors(
    inputs: FutureInputs, as_of: datetime, config: FutureConfig
) -> tuple[FactorEvidence, ...]:
    factors = {factor.factor: factor for factor in inputs.factors}
    property_ = inputs.property
    if inputs.employment_centers:
        factors["employment_accessibility"] = employment_accessibility(
            property_.longitude,
            property_.latitude,
            inputs.employment_centers,
            config.employment_accessibility,
            property_.data_mode,
        )
    if any(project.project_type == "transport" for project in inputs.projects):
        factors["transport_accessibility"] = transport_accessibility(
            property_.longitude,
            property_.latitude,
            inputs.projects,
            config.transport_accessibility,
            config.planning_realization_weights,
            property_.data_mode,
        )
    if inputs.projects:
        factors["planning_realization"] = planning_realization_factor(
            inputs.projects,
            config.planning_realization_weights,
            property_.data_mode,
        )
    _set_if_missing(factors, "urban_renewal", _renewal_factor(inputs.projects, inputs))
    _set_if_missing(factors, "building_aging", _aging_factor(inputs, as_of, config))
    _set_if_missing(factors, "product_obsolescence", _product_factor(inputs))
    return tuple(
        factors.get(name) or _missing_factor(name, property_.data_mode)
        for name in ALL_FUTURE_FACTORS
    )


def _renewal_factor(
    projects: tuple[FutureProjectInput, ...], inputs: FutureInputs
) -> FactorEvidence | None:
    renewal = [project for project in projects if project.project_type == "urban_renewal"]
    if not renewal:
        return None
    weights = [
        project.probability if project.probability is not None else Decimal("0")
        for project in renewal
    ]
    score = _rounded(
        sum(
            (
                probability * project.confidence
                for probability, project in zip(weights, renewal, strict=True)
            ),
            Decimal(),
        )
        / Decimal(len(renewal))
        * Decimal("100")
    )
    return FactorEvidence(
        factor="urban_renewal",
        current_score=Decimal("0"),
        future_score=score,
        confidence=_average([project.confidence for project in renewal]),
        source="future_project_model",
        source_timestamp=max(project.source_date for project in renewal),
        data_mode=inputs.property.data_mode,
        explanation="Community-level renewal evidence weighted by explicit probability.",
        metadata={"project_count": len(renewal)},
    )


def _aging_factor(
    inputs: FutureInputs, as_of: datetime, config: FutureConfig
) -> FactorEvidence | None:
    year = inputs.property.year_built
    if year is None:
        return None
    current_age = max(0, as_of.year - year)
    future_age = current_age + 5
    grace = config.obsolescence_risk.building_age_grace_years
    rate = config.obsolescence_risk.building_age_risk_per_year
    current = min(Decimal("100"), Decimal(max(0, current_age - grace)) * rate)
    future = min(Decimal("100"), Decimal(max(0, future_age - grace)) * rate)
    return FactorEvidence(
        factor="building_aging",
        current_score=_rounded(current),
        future_score=_rounded(future),
        confidence=Decimal("0.9"),
        source="canonical_property",
        source_timestamp=as_of,
        data_mode=inputs.property.data_mode,
        explanation="Building-aging risk projected mechanically to the five-year horizon.",
        metadata={"year_built": year, "future_age": future_age, "higher_is_worse": True},
    )


def _product_factor(inputs: FutureInputs) -> FactorEvidence | None:
    property_ = inputs.property
    evidence: list[Decimal] = []
    if property_.elevator is not None:
        evidence.append(Decimal("15") if property_.elevator else Decimal("82"))
    evidence.extend(
        value
        for value in (
            _quality_risk(property_.parking_quality),
            _quality_risk(property_.property_management_quality),
            _quality_risk(property_.maintenance_quality),
            _quality_risk(property_.layout_mainstreamness),
        )
        if value is not None
    )
    if not evidence:
        return None
    current = _average(evidence)
    future = min(Decimal("100"), current + Decimal("5"))
    return FactorEvidence(
        factor="product_obsolescence",
        current_score=_rounded(current),
        future_score=_rounded(future),
        confidence=min(Decimal("1"), Decimal(len(evidence)) / Decimal("5")),
        source="canonical_property",
        source_timestamp=inputs.data_timestamp,
        data_mode=property_.data_mode,
        explanation="Observed product attributes form a separate replacement-risk factor.",
        metadata={"observed_attribute_count": len(evidence), "higher_is_worse": True},
    )


def _quality_risk(value: str | None) -> Decimal | None:
    if value is None:
        return None
    return {
        "excellent": Decimal("10"),
        "good": Decimal("25"),
        "average": Decimal("50"),
        "poor": Decimal("82"),
        "very_poor": Decimal("95"),
    }.get(value.strip().lower())


def _set_if_missing(
    factors: dict[str, FactorEvidence], name: str, factor: FactorEvidence | None
) -> None:
    if name not in factors and factor is not None:
        factors[name] = factor


def _missing_factor(name: str, data_mode: str) -> FactorEvidence:
    return FactorEvidence(
        factor=name,
        current_score=None,
        future_score=None,
        confidence=Decimal("0"),
        source="insufficient",
        source_timestamp=None,
        data_mode=data_mode,
        explanation=f"No source-independent evidence is available for {name}.",
    )


def _average(values: list[Decimal]) -> Decimal:
    return sum(values, Decimal()) / Decimal(len(values))


def _rounded(value: Decimal) -> Decimal:
    return value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

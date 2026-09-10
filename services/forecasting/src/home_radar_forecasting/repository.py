from __future__ import annotations

import hashlib
import json
import uuid
from datetime import UTC, datetime, time
from decimal import Decimal
from typing import Any

from home_radar_models.enums import DataMode, MarketObservationType
from home_radar_models.future import (
    EmploymentCenter,
    FutureFactorObservation,
    FutureProject,
)
from home_radar_models.listing import Listing
from home_radar_models.market import MarketBaseline, MarketObservation
from home_radar_models.valuation import ValuationResult
from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session

from home_radar_forecasting.domain import (
    CalibrationEvidence,
    EmploymentCenterInput,
    FactorEvidence,
    FutureInputs,
    FutureProjectInput,
    FutureProperty,
)


class FutureInputsUnavailableError(LookupError):
    pass


def load_future_inputs(
    session: Session,
    listing_id: uuid.UUID,
    data_mode: DataMode,
    as_of: datetime,
    *,
    valuation: ValuationResult | None = None,
) -> FutureInputs:
    valuation = valuation or _latest_valuation(session, listing_id, data_mode, as_of)
    observation, listing = _latest_listing(session, listing_id, data_mode, as_of)
    factor_rows = _factor_rows(session, listing, data_mode, as_of)
    factor_evidence = _best_factor_evidence(factor_rows, listing, data_mode)
    listing_baseline = _best_baseline(
        session, listing, data_mode, as_of, MarketObservationType.LISTING
    )
    rental_baseline = _best_baseline(
        session, listing, data_mode, as_of, MarketObservationType.RENTAL
    )
    factor_evidence = _add_p3_factors(
        factor_evidence, listing_baseline, rental_baseline, data_mode, as_of
    )
    centers = _employment_centers(session, data_mode, as_of)
    projects = _future_projects(session, listing, data_mode, as_of)
    timestamps = _timestamps(
        observation,
        factor_rows,
        centers,
        projects,
        listing_baseline,
        rental_baseline,
    )
    return FutureInputs(
        property=_future_property(valuation, observation, listing, data_mode),
        factors=tuple(factor_evidence.values()),
        employment_centers=centers,
        projects=projects,
        hierarchical_components=_hierarchical_components(factor_rows),
        calibration=CalibrationEvidence(),
        data_version=_data_version(
            valuation, factor_rows, centers, projects, listing_baseline, rental_baseline
        ),
        data_timestamp=max(timestamps) if timestamps else None,
    )


def _latest_valuation(
    session: Session, listing_id: uuid.UUID, data_mode: DataMode, as_of: datetime
) -> ValuationResult:
    statement = select(ValuationResult).where(
        ValuationResult.listing_id == listing_id,
        ValuationResult.data_mode == data_mode.value,
    )
    if not session.info.get("validation_allow_generated_after_cutoff"):
        statement = statement.where(ValuationResult.calculated_at <= as_of)
    valuation = session.scalar(statement.order_by(ValuationResult.calculated_at.desc()).limit(1))
    if valuation is None:
        raise FutureInputsUnavailableError(
            f"listing {listing_id} has no P4 valuation in {data_mode.value} mode"
        )
    return valuation


def _latest_listing(
    session: Session, listing_id: uuid.UUID, data_mode: DataMode, as_of: datetime
) -> tuple[MarketObservation, Listing]:
    row = session.execute(
        select(MarketObservation, Listing)
        .join(Listing, Listing.id == MarketObservation.listing_id)
        .where(
            MarketObservation.listing_id == listing_id,
            MarketObservation.data_mode == data_mode.value,
            MarketObservation.observation_type == MarketObservationType.LISTING.value,
            MarketObservation.observed_at <= as_of,
        )
        .order_by(MarketObservation.observed_at.desc())
        .limit(1)
    ).one_or_none()
    if row is None:
        raise FutureInputsUnavailableError(
            f"listing {listing_id} is unavailable in {data_mode.value} mode"
        )
    return row[0], row[1]


def _factor_rows(
    session: Session, listing: Listing, data_mode: DataMode, as_of: datetime
) -> list[FutureFactorObservation]:
    scope = or_(
        FutureFactorObservation.listing_id == listing.id,
        and_(
            FutureFactorObservation.scope_type == "community",
            FutureFactorObservation.district == listing.district,
            FutureFactorObservation.submarket == listing.submarket,
            FutureFactorObservation.community == listing.community,
        ),
        and_(
            FutureFactorObservation.scope_type == "submarket",
            FutureFactorObservation.district == listing.district,
            FutureFactorObservation.submarket == listing.submarket,
        ),
        and_(
            FutureFactorObservation.scope_type == "district",
            FutureFactorObservation.district == listing.district,
        ),
        FutureFactorObservation.scope_type == "shanghai",
    )
    return list(
        session.scalars(
            select(FutureFactorObservation).where(
                FutureFactorObservation.data_mode == data_mode.value,
                FutureFactorObservation.observed_at <= as_of,
                FutureFactorObservation.effective_from <= as_of,
                or_(
                    FutureFactorObservation.effective_to.is_(None),
                    FutureFactorObservation.effective_to > as_of,
                ),
                scope,
            )
        )
    )


def _best_factor_evidence(
    rows: list[FutureFactorObservation], listing: Listing, data_mode: DataMode
) -> dict[str, FactorEvidence]:
    grouped: dict[str, list[FutureFactorObservation]] = {}
    for row in rows:
        grouped.setdefault(row.factor, []).append(row)
    return {
        name: _factor_from_row(max(values, key=lambda row: _factor_rank(row, listing)))
        for name, values in grouped.items()
        if not name.endswith("_alpha")
    }


def _factor_rank(row: FutureFactorObservation, listing: Listing) -> tuple[int, datetime, Decimal]:
    specificity = {
        "shanghai": 0,
        "district": 1,
        "submarket": 2,
        "community": 3,
        "listing": 4,
    }[row.scope_type]
    if row.listing_id == listing.id:
        specificity = 5
    return specificity, row.observed_at, row.confidence


def _factor_from_row(row: FutureFactorObservation) -> FactorEvidence:
    return FactorEvidence(
        factor=row.factor,
        current_score=row.current_score,
        future_score=row.future_score,
        confidence=row.confidence,
        source=row.source,
        source_timestamp=row.source_timestamp,
        data_mode=row.data_mode,
        explanation=row.explanation,
        metadata={
            **row.observation_metadata,
            "observation_id": str(row.id),
            "scope_type": row.scope_type,
        },
    )


def _employment_centers(
    session: Session, data_mode: DataMode, as_of: datetime
) -> tuple[EmploymentCenterInput, ...]:
    rows = session.execute(
        select(
            EmploymentCenter,
            func.ST_X(EmploymentCenter.coordinates),
            func.ST_Y(EmploymentCenter.coordinates),
        ).where(
            EmploymentCenter.data_mode == data_mode.value,
            EmploymentCenter.effective_from <= as_of,
            or_(EmploymentCenter.effective_to.is_(None), EmploymentCenter.effective_to > as_of),
            EmploymentCenter.source_timestamp <= as_of,
        )
    ).all()
    return tuple(
        EmploymentCenterInput(
            name=center.name,
            category=center.category,
            longitude=Decimal(str(longitude)),
            latitude=Decimal(str(latitude)),
            current_employment_weight=center.current_employment_weight,
            future_employment_weight=center.future_employment_weight,
            source=center.source,
            source_timestamp=center.source_timestamp,
            confidence=center.confidence,
        )
        for center, longitude, latitude in rows
    )


def _future_projects(
    session: Session, listing: Listing, data_mode: DataMode, as_of: datetime
) -> tuple[FutureProjectInput, ...]:
    scope = or_(
        FutureProject.district.is_(None),
        and_(FutureProject.district == listing.district, FutureProject.submarket.is_(None)),
        and_(
            FutureProject.district == listing.district,
            FutureProject.submarket == listing.submarket,
            FutureProject.community.is_(None),
        ),
        and_(
            FutureProject.district == listing.district,
            FutureProject.submarket == listing.submarket,
            FutureProject.community == listing.community,
        ),
    )
    rows = session.execute(
        select(
            FutureProject,
            func.ST_X(FutureProject.coordinates),
            func.ST_Y(FutureProject.coordinates),
        ).where(
            FutureProject.data_mode == data_mode.value,
            FutureProject.effective_from <= as_of,
            or_(FutureProject.effective_to.is_(None), FutureProject.effective_to > as_of),
            FutureProject.source_date <= as_of,
            scope,
        )
    ).all()
    return tuple(
        _project_input(project, longitude, latitude) for project, longitude, latitude in rows
    )


def _project_input(
    project: FutureProject, longitude: float | None, latitude: float | None
) -> FutureProjectInput:
    completion = (
        datetime.combine(project.expected_completion, time(), UTC)
        if project.expected_completion is not None
        else None
    )
    return FutureProjectInput(
        name=project.name,
        project_type=project.project_type,
        status=project.status,
        longitude=Decimal(str(longitude)) if longitude is not None else None,
        latitude=Decimal(str(latitude)) if latitude is not None else None,
        expected_completion=completion,
        source=project.source,
        source_date=project.source_date,
        confidence=project.confidence,
        probability=project.probability,
    )


def _best_baseline(
    session: Session,
    listing: Listing,
    data_mode: DataMode,
    as_of: datetime,
    observation_type: MarketObservationType,
) -> MarketBaseline | None:
    statement = select(MarketBaseline).where(
        MarketBaseline.data_mode == data_mode.value,
        MarketBaseline.observation_type == observation_type.value,
        MarketBaseline.input_cutoff_at <= as_of,
        or_(
            MarketBaseline.district.is_(None),
            MarketBaseline.district == listing.district,
        ),
        or_(
            MarketBaseline.submarket.is_(None),
            MarketBaseline.submarket == listing.submarket,
        ),
        or_(
            MarketBaseline.community.is_(None),
            MarketBaseline.community == listing.community,
        ),
    )
    validation_run_id = session.info.get("validation_baseline_run_id")
    if validation_run_id is not None:
        statement = statement.where(MarketBaseline.materialization_run_id == validation_run_id)
    rows = list(session.scalars(statement))
    if not rows:
        return None
    return max(
        rows,
        key=lambda row: (
            _baseline_rank(row),
            -abs(row.window_days - 90),
            row.generated_at,
        ),
    )


def _baseline_rank(row: MarketBaseline) -> int:
    return {"shanghai": 0, "district": 1, "submarket": 2, "community": 3}.get(row.level, 0)


def _add_p3_factors(
    factors: dict[str, FactorEvidence],
    listing: MarketBaseline | None,
    rental: MarketBaseline | None,
    data_mode: DataMode,
    as_of: datetime,
) -> dict[str, FactorEvidence]:
    if "buyer_pool_depth" not in factors and listing is not None:
        factors["buyer_pool_depth"] = FactorEvidence(
            factor="buyer_pool_depth",
            current_score=listing.liquidity_score,
            future_score=None,
            confidence=listing.liquidity_confidence or Decimal("0"),
            source="p3_listing_baseline",
            source_timestamp=listing.generated_at,
            data_mode=data_mode.value,
            explanation="P3 supplies current liquidity; long-term buyer-pool evidence is missing.",
            metadata={"baseline_version": listing.baseline_version},
        )
    if "rental_demand" not in factors:
        factors["rental_demand"] = _rental_factor(rental, data_mode, as_of)
    return factors


def _rental_factor(
    baseline: MarketBaseline | None, data_mode: DataMode, as_of: datetime
) -> FactorEvidence:
    if baseline is None or baseline.rent_per_sqm is None:
        return FactorEvidence(
            factor="rental_demand",
            current_score=None,
            future_score=None,
            confidence=Decimal("0"),
            source="insufficient",
            source_timestamp=None,
            data_mode=data_mode.value,
            explanation="P3 has no real rental baseline; rental yield is not manufactured.",
        )
    score = baseline.liquidity_score
    return FactorEvidence(
        factor="rental_demand",
        current_score=score,
        future_score=score,
        confidence=baseline.confidence_score,
        source="p3_rental_baseline",
        source_timestamp=baseline.generated_at,
        data_mode=data_mode.value,
        explanation="P3 rental baseline carried forward without an unsupported growth claim.",
        metadata={
            "baseline_version": baseline.baseline_version,
            "rent_per_sqm": str(baseline.rent_per_sqm),
            "as_of": as_of.isoformat(),
        },
    )


def _future_property(
    valuation: ValuationResult,
    observation: MarketObservation,
    listing: Listing,
    data_mode: DataMode,
) -> FutureProperty:
    metadata = observation.observation_metadata
    return FutureProperty(
        listing_id=listing.id,
        district=listing.district,
        submarket=listing.submarket,
        community=listing.community,
        area_sqm=observation.area_sqm or listing.area_sqm,
        fair_value=valuation.fair_value,
        value_score=valuation.value_score,
        valuation_version=valuation.valuation_version,
        baseline_version=valuation.baseline_version,
        baseline_confidence=valuation.baseline_confidence,
        valuation_confidence=valuation.valuation_confidence,
        transaction_support=valuation.transaction_support,
        data_mode=data_mode.value,
        longitude=observation.longitude or listing.longitude,
        latitude=observation.latitude or listing.latitude,
        year_built=_int_value(metadata, "year_built", listing.year_built),
        elevator=_bool_value(metadata, "elevator", listing.elevator),
        building_type=_str_value(metadata, "building_type", listing.building_type),
        bedrooms=observation.bedrooms or listing.bedrooms,
        layout=observation.layout,
        parking_quality=_str_value(metadata, "parking_quality", None),
        property_management_quality=_str_value(metadata, "property_management_quality", None),
        maintenance_quality=_str_value(metadata, "maintenance_quality", None),
        layout_mainstreamness=_str_value(metadata, "layout_mainstreamness", None),
    )


def _hierarchical_components(
    rows: list[FutureFactorObservation],
) -> dict[str, Decimal | None]:
    names = {"district_alpha", "submarket_alpha", "community_alpha", "property_alpha"}
    return {
        row.factor: row.future_score
        for row in rows
        if row.factor in names and row.future_score is not None
    }


def _timestamps(
    observation: MarketObservation,
    rows: list[FutureFactorObservation],
    centers: tuple[EmploymentCenterInput, ...],
    projects: tuple[FutureProjectInput, ...],
    listing_baseline: MarketBaseline | None,
    rental_baseline: MarketBaseline | None,
) -> list[datetime]:
    values = [observation.observed_at]
    values.extend(row.source_timestamp for row in rows)
    values.extend(center.source_timestamp for center in centers)
    values.extend(project.source_date for project in projects)
    values.extend(
        baseline.input_cutoff_at
        for baseline in (listing_baseline, rental_baseline)
        if baseline is not None
    )
    return values


def _data_version(
    valuation: ValuationResult,
    rows: list[FutureFactorObservation],
    centers: tuple[EmploymentCenterInput, ...],
    projects: tuple[FutureProjectInput, ...],
    listing_baseline: MarketBaseline | None,
    rental_baseline: MarketBaseline | None,
) -> str:
    payload = {
        "valuation": valuation.valuation_version,
        "factors": sorted(str(row.id) for row in rows),
        "centers": sorted((center.name, center.source_timestamp.isoformat()) for center in centers),
        "projects": sorted((project.name, project.source_date.isoformat()) for project in projects),
        "baselines": [
            baseline.baseline_version
            for baseline in (listing_baseline, rental_baseline)
            if baseline is not None
        ],
    }
    digest = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
    return f"p5-data-{digest[:32]}"


def _str_value(metadata: dict[str, Any], key: str, fallback: str | None) -> str | None:
    value = metadata.get(key, fallback)
    return str(value) if value is not None else None


def _int_value(metadata: dict[str, Any], key: str, fallback: int | None) -> int | None:
    value = metadata.get(key, fallback)
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return fallback


def _bool_value(metadata: dict[str, Any], key: str, fallback: bool | None) -> bool | None:
    value = metadata.get(key, fallback)
    if isinstance(value, bool):
        return value
    if isinstance(value, str) and value.lower() in {"true", "false"}:
        return value.lower() == "true"
    return fallback

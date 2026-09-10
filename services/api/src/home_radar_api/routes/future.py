from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from home_radar_forecasting.cache import evaluate_cached_future
from home_radar_forecasting.config import load_future_config
from home_radar_forecasting.repository import FutureInputsUnavailableError
from home_radar_market.config import load_market_config
from home_radar_models.enums import DataMode
from home_radar_models.future import FutureAssessment
from home_radar_shared.config import get_settings
from home_radar_valuation.aggregation import InsufficientValuationEvidenceError
from home_radar_valuation.config import load_valuation_config
from home_radar_valuation.repository import ListingNotAvailableForModeError
from sqlalchemy.orm import Session

from home_radar_api.dependencies import get_db
from home_radar_api.future_schemas import (
    FutureFactorPage,
    FutureRead,
    FutureRiskRead,
    FutureScenarioPage,
)

router = APIRouter(prefix="/api/v1/future", tags=["future"])


@router.get("/listings/{listing_id}", response_model=FutureRead)
def get_listing_future(
    listing_id: uuid.UUID,
    db: Annotated[Session, Depends(get_db)],
    as_of: Annotated[datetime | None, Query()] = None,
) -> FutureRead:
    result, cache_hit = _cached_result(db, listing_id, as_of)
    return FutureRead.model_validate(_read_payload(result, cache_hit))


@router.get("/listings/{listing_id}/factors", response_model=FutureFactorPage)
def get_listing_future_factors(
    listing_id: uuid.UUID,
    db: Annotated[Session, Depends(get_db)],
    as_of: Annotated[datetime | None, Query()] = None,
) -> FutureFactorPage:
    result, _ = _cached_result(db, listing_id, as_of)
    return FutureFactorPage.model_validate(
        {
            "listing_id": listing_id,
            "future_assessment_version": result.future_assessment_version,
            "data_mode": result.data_mode,
            "factors": result.factor_breakdown,
            "confidence": result.confidence,
            "calibration_state": result.calibration_state,
        }
    )


@router.get("/listings/{listing_id}/scenarios", response_model=FutureScenarioPage)
def get_listing_future_scenarios(
    listing_id: uuid.UUID,
    db: Annotated[Session, Depends(get_db)],
    as_of: Annotated[datetime | None, Query()] = None,
) -> FutureScenarioPage:
    result, _ = _cached_result(db, listing_id, as_of)
    return FutureScenarioPage.model_validate(
        {
            "listing_id": listing_id,
            "future_assessment_version": result.future_assessment_version,
            "data_mode": result.data_mode,
            "fair_value_anchor": result.fair_value_anchor,
            "structural_alpha": result.structural_alpha,
            "scenarios": result.scenarios,
            "confidence": result.confidence,
            "calibration_state": result.calibration_state,
        }
    )


@router.get("/listings/{listing_id}/risks", response_model=FutureRiskRead)
def get_listing_future_risks(
    listing_id: uuid.UUID,
    db: Annotated[Session, Depends(get_db)],
    as_of: Annotated[datetime | None, Query()] = None,
) -> FutureRiskRead:
    result, _ = _cached_result(db, listing_id, as_of)
    return FutureRiskRead.model_validate(
        {
            "listing_id": listing_id,
            "future_assessment_version": result.future_assessment_version,
            "data_mode": result.data_mode,
            "obsolescence_risk": result.obsolescence_risk,
            "obsolescence_coverage": result.obsolescence_coverage,
            "risk_breakdown": result.risk_breakdown,
            "warnings": result.warnings,
            "confidence": result.confidence,
            "calibration_state": result.calibration_state,
        }
    )


def _cached_result(
    db: Session, listing_id: uuid.UUID, as_of: datetime | None
) -> tuple[FutureAssessment, bool]:
    if as_of is not None and as_of.tzinfo is None:
        raise HTTPException(status_code=422, detail="as_of must be timezone-aware")
    settings = get_settings()
    try:
        result = evaluate_cached_future(
            db,
            listing_id,
            _mode(),
            as_of or datetime.now(UTC),
            load_market_config(settings.market_baseline_config_path),
            load_valuation_config(settings.valuation_config_path),
            load_future_config(settings.forecasting_config_path),
        )
        db.commit()
        return result
    except (ListingNotAvailableForModeError, FutureInputsUnavailableError) as exc:
        db.rollback()
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except InsufficientValuationEvidenceError as exc:
        db.rollback()
        raise HTTPException(status_code=422, detail=str(exc)) from exc


def _read_payload(result: FutureAssessment, cache_hit: bool) -> dict[str, object]:
    return {
        "listing_id": result.listing_id,
        "data_mode": result.data_mode,
        "data_notice": _data_notice(result.data_mode),
        "recommendation_status": "research_uncalibrated"
        if result.calibration_state != "calibrated"
        else "calibrated_relative_outlook",
        "cache_hit": cache_hit,
        "relative_outlook": result.relative_outlook,
        "investment_conclusion": None,
        "future_score": result.future_score,
        "future_score_coverage": result.future_score_coverage,
        "obsolescence_risk": result.obsolescence_risk,
        "obsolescence_coverage": result.obsolescence_coverage,
        "structural_alpha": result.structural_alpha,
        "quality_value_quadrant": result.quality_value_quadrant,
        "fair_value_anchor": result.fair_value_anchor,
        "value_score": result.value_score,
        "scenarios": result.scenarios,
        "factors": result.factor_breakdown,
        "confidence": result.confidence,
        "confidence_score": result.confidence_score,
        "calibration_state": result.calibration_state,
        "warnings": result.warnings,
        "missing_inputs": result.missing_inputs,
        "risk_breakdown": result.risk_breakdown,
        "structural_components": result.structural_components,
        "provenance": result.provenance,
        "future_model_version": result.future_model_version,
        "scenario_model_version": result.scenario_model_version,
        "valuation_version": result.valuation_version,
        "baseline_version": result.baseline_version,
        "configuration_version": result.configuration_version,
        "data_version": result.data_version,
        "data_timestamp": result.data_timestamp,
        "input_fingerprint": result.input_fingerprint,
        "future_assessment_version": result.future_assessment_version,
        "generated_at": result.generated_at,
    }


def _mode() -> DataMode:
    try:
        return DataMode(get_settings().market_data_mode)
    except ValueError as exc:
        raise RuntimeError("SHR_MARKET_DATA_MODE must be demo, sample, or live") from exc


def _data_notice(data_mode: str) -> str:
    if data_mode == "live":
        return "LIVE evidence; output remains a relative outlook, not investment advice."
    if data_mode == "demo":
        return "DEMO synthetic evidence; never present it as a live Shanghai forecast."
    return "SAMPLE evidence; this research output is not a live Shanghai forecast."

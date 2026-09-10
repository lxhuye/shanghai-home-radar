from __future__ import annotations

import uuid
from dataclasses import asdict
from datetime import UTC, datetime
from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from home_radar_market.config import load_market_config
from home_radar_models.enums import DataMode
from home_radar_models.valuation import ValuationResult
from home_radar_shared.config import get_settings
from home_radar_valuation.aggregation import InsufficientValuationEvidenceError
from home_radar_valuation.cache import evaluate_cached_listing
from home_radar_valuation.config import load_valuation_config
from home_radar_valuation.domain import PriceHistory, TargetProperty
from home_radar_valuation.engine import DeterministicValuationEngine
from home_radar_valuation.repository import (
    ListingNotAvailableForModeError,
    load_market_inputs,
)
from sqlalchemy.orm import Session

from home_radar_api.dependencies import get_db
from home_radar_api.schemas import (
    ValuationComparablesRead,
    ValuationEvaluateRequest,
    ValuationExplanationRead,
    ValuationRead,
)

router = APIRouter(prefix="/api/v1/valuation", tags=["valuation"])


@router.get("/listings/{listing_id}", response_model=ValuationRead)
def get_listing_valuation(
    listing_id: uuid.UUID,
    db: Annotated[Session, Depends(get_db)],
    as_of: Annotated[datetime | None, Query()] = None,
) -> ValuationRead:
    result, cache_hit = _cached_result(db, listing_id, as_of)
    return _read_from_model(result, cache_hit)


@router.post("/evaluate", response_model=ValuationRead)
def evaluate_structured_property(
    request: ValuationEvaluateRequest,
    db: Annotated[Session, Depends(get_db)],
) -> ValuationRead:
    settings = get_settings()
    mode = _mode()
    as_of = request.as_of or datetime.now(UTC)
    if as_of.tzinfo is None:
        raise HTTPException(status_code=422, detail="as_of must be timezone-aware")
    target = TargetProperty(
        listing_id=None,
        **request.target.model_dump(),
        data_mode=mode.value,
    )
    history = _structured_history(request, target)
    market_config = load_market_config(settings.market_baseline_config_path)
    valuation_config = load_valuation_config(settings.valuation_config_path)
    inputs = load_market_inputs(db, target, history, as_of, market_config, valuation_config)
    try:
        evaluation = DeterministicValuationEngine(valuation_config).evaluate(inputs, as_of)
    except InsufficientValuationEvidenceError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return ValuationRead.model_validate({**asdict(evaluation), "cache_hit": False})


@router.get("/listings/{listing_id}/comparables", response_model=ValuationComparablesRead)
def get_listing_comparables(
    listing_id: uuid.UUID,
    db: Annotated[Session, Depends(get_db)],
    as_of: Annotated[datetime | None, Query()] = None,
) -> ValuationComparablesRead:
    result, _ = _cached_result(db, listing_id, as_of)
    return ValuationComparablesRead.model_validate(
        {
            "listing_id": listing_id,
            "valuation_version": result.valuation_version,
            "comparable_count": result.comparable_count,
            "comparables": result.comparables,
        }
    )


@router.get("/listings/{listing_id}/explanation", response_model=ValuationExplanationRead)
def get_listing_explanation(
    listing_id: uuid.UUID,
    db: Annotated[Session, Depends(get_db)],
    as_of: Annotated[datetime | None, Query()] = None,
) -> ValuationExplanationRead:
    result, _ = _cached_result(db, listing_id, as_of)
    return ValuationExplanationRead.model_validate(
        {
            "listing_id": listing_id,
            "valuation_version": result.valuation_version,
            "fair_value": result.fair_value,
            "fair_value_low": result.fair_value_low,
            "fair_value_high": result.fair_value_high,
            "baseline_level_used": result.baseline_level_used,
            "baseline_confidence": result.baseline_confidence,
            "fallback_reason": result.fallback_reason,
            "adjustments": result.adjustments,
            "warnings": result.warnings,
            "provenance": result.provenance,
        }
    )


def _cached_result(
    db: Session, listing_id: uuid.UUID, as_of: datetime | None = None
) -> tuple[ValuationResult, bool]:
    settings = get_settings()
    if as_of is not None and as_of.tzinfo is None:
        raise HTTPException(status_code=422, detail="as_of must be timezone-aware")
    try:
        result, cache_hit = evaluate_cached_listing(
            db,
            listing_id,
            _mode(),
            as_of or datetime.now(UTC),
            load_market_config(settings.market_baseline_config_path),
            load_valuation_config(settings.valuation_config_path),
        )
        db.commit()
        return result, cache_hit
    except ListingNotAvailableForModeError as exc:
        db.rollback()
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except InsufficientValuationEvidenceError as exc:
        db.rollback()
        raise HTTPException(status_code=422, detail=str(exc)) from exc


def _structured_history(request: ValuationEvaluateRequest, target: TargetProperty) -> PriceHistory:
    value = request.price_history
    if value is None:
        return PriceHistory(
            original_ask=target.current_ask,
            current_ask=target.current_ask,
            absolute_reduction=Decimal("0"),
            percentage_reduction=Decimal("0"),
            price_cut_count=0,
            days_since_last_cut=None,
            days_on_market=0,
            relisting_flag=False,
        )
    reduction = max(Decimal("0"), value.original_ask - target.current_ask)
    return PriceHistory(
        original_ask=value.original_ask,
        current_ask=target.current_ask,
        absolute_reduction=reduction,
        percentage_reduction=reduction / value.original_ask,
        price_cut_count=value.price_cut_count,
        days_since_last_cut=value.days_since_last_cut,
        days_on_market=value.days_on_market,
        relisting_flag=value.relisting_flag,
    )


def _read_from_model(result: ValuationResult, cache_hit: bool) -> ValuationRead:
    return ValuationRead.model_validate(
        {
            "listing_id": result.listing_id,
            "data_mode": result.data_mode,
            "data_notice": _data_notice(result.data_mode),
            "recommendation_status": (
                "live_analysis" if result.data_mode == "live" else "demo_only"
            ),
            "cache_hit": cache_hit,
            "fair_value": result.fair_value,
            "fair_value_low": result.fair_value_low,
            "fair_value_high": result.fair_value_high,
            "valuation_basis": result.valuation_basis,
            "valuation_confidence": result.valuation_confidence,
            "valuation_confidence_score": result.valuation_confidence_score,
            "transaction_support": result.transaction_support,
            "current_ask": result.current_ask,
            "ask_discount_to_fair_value": result.ask_discount_to_fair_value,
            "estimated_executable_price": result.estimated_executable_price,
            "executable_discount_to_fair_value": (result.executable_discount_to_fair_value),
            "value_score": result.value_score,
            "decision": result.decision,
            "comparable_count": result.comparable_count,
            "effective_comparable_count": result.effective_comparable_count,
            "baseline_level_used": result.baseline_level_used,
            "baseline_confidence": result.baseline_confidence,
            "baseline_version": result.baseline_version,
            "fallback_reason": result.fallback_reason,
            "adjustments": result.adjustments,
            "comparables": result.comparables,
            "warnings": result.warnings,
            "score_components": result.score_components,
            "price_history": result.price_history,
            "provenance": result.provenance,
            "valuation_model_version": result.valuation_model_version,
            "scoring_model_version": result.scoring_model_version,
            "configuration_version": result.configuration_version,
            "valuation_version": result.valuation_version,
            "input_fingerprint": result.input_fingerprint,
            "generated_at": result.calculated_at,
            "baseline_generated_at": result.baseline_generated_at,
        }
    )


def _mode() -> DataMode:
    try:
        return DataMode(get_settings().market_data_mode)
    except ValueError as exc:
        raise RuntimeError("SHR_MARKET_DATA_MODE must be demo, sample, or live") from exc


def _data_notice(data_mode: str) -> str:
    if data_mode == "live":
        return "LIVE authorized evidence; this is analysis, not an investment recommendation."
    if data_mode == "demo":
        return "DEMO synthetic evidence; never treat this output as a live recommendation."
    return "SAMPLE evidence; this output is DEMO-only and not a live recommendation."

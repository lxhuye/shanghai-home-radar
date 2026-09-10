from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query
from home_radar_decision.blind_validation import (
    BlindValidationError,
    create_blind_batch,
    label_blind_item,
    reveal_blind_batch,
    validation_items,
)
from home_radar_decision.cache import evaluate_cached_decision
from home_radar_decision.config import DecisionConfig, load_decision_config
from home_radar_decision.p56_config import load_real_world_validation_config
from home_radar_decision.ranking import decision_sort_key
from home_radar_decision.real_world_validation import (
    RealWorldValidationError,
    append_label_amendment,
    create_validation_run,
    finalize_validation_run,
    freeze_human_labels,
    generate_model_results,
    label_validation_item,
    record_review,
    reveal_validation_run,
    validation_run_items,
)
from home_radar_decision.repository import (
    DecisionInputsUnavailableError,
    latest_decision_assessments,
)
from home_radar_forecasting.config import load_future_config
from home_radar_forecasting.repository import FutureInputsUnavailableError
from home_radar_market.config import load_market_config
from home_radar_models.decision import (
    DecisionAssessment,
    DecisionValidationBatch,
    DecisionValidationReview,
)
from home_radar_models.enums import DataMode, ListingStatus
from home_radar_models.listing import Listing
from home_radar_shared.config import get_settings
from home_radar_valuation.aggregation import InsufficientValuationEvidenceError
from home_radar_valuation.config import load_valuation_config
from home_radar_valuation.repository import ListingNotAvailableForModeError
from sqlalchemy import select
from sqlalchemy.orm import Session

from home_radar_api.decision_schemas import (
    DecisionRead,
    OpportunityPage,
    RealWorldAmendmentRequest,
    RealWorldLabelsRequest,
    RealWorldReviewsRequest,
    RealWorldValidationCreate,
    RealWorldValidationRunRead,
    ValidationBatchCreate,
    ValidationBatchRead,
    ValidationLabelsRequest,
    WhyRankedRead,
)
from home_radar_api.dependencies import get_db, require_api_key

router = APIRouter(prefix="/api/v1/decisions", tags=["decisions"])


@router.get("/listings/{listing_id}", response_model=DecisionRead)
def get_listing_decision(
    listing_id: uuid.UUID,
    db: Annotated[Session, Depends(get_db)],
    as_of: Annotated[datetime | None, Query()] = None,
) -> DecisionRead:
    result, cache_hit = _cached_result(db, listing_id, as_of)
    rank_by_id, eligible_count, _ = _rank_context(db, _decision_config())
    return DecisionRead.model_validate(
        _read_payload(result, cache_hit, rank_by_id.get(result.listing_id), eligible_count)
    )


@router.get("/opportunities", response_model=OpportunityPage)
def get_opportunities(
    db: Annotated[Session, Depends(get_db)],
    limit: Annotated[int, Query(ge=1, le=100)] = 10,
) -> OpportunityPage:
    config = _decision_config()
    rank_by_id, eligible_count, latest = _rank_context(db, config)
    ranked = sorted(
        (item for item in latest if item.listing_id in rank_by_id),
        key=lambda item: rank_by_id[item.listing_id],
    )[:limit]
    return OpportunityPage.model_validate(
        {
            "data_mode": _mode().value,
            "evaluated_count": len(latest),
            "eligible_count": eligible_count,
            "items": [
                _read_payload(item, True, rank_by_id[item.listing_id], eligible_count)
                for item in ranked
            ],
        }
    )


@router.get("/listings/{listing_id}/why-ranked", response_model=WhyRankedRead)
def get_why_ranked(
    listing_id: uuid.UUID,
    db: Annotated[Session, Depends(get_db)],
    as_of: Annotated[datetime | None, Query()] = None,
) -> WhyRankedRead:
    result, _ = _cached_result(db, listing_id, as_of)
    rank_by_id, eligible_count, _ = _rank_context(db, _decision_config())
    rank = rank_by_id.get(result.listing_id)
    return WhyRankedRead.model_validate(
        {
            "listing_id": result.listing_id,
            "decision_version": result.decision_version,
            "rank": rank,
            "eligible_count": eligible_count,
            "summary": (
                f"Rank #{rank} / {eligible_count} eligible listings"
                if rank is not None
                else f"Not ranked: {result.eligibility_status}"
            ),
            "eligibility_status": result.eligibility_status,
            "opportunity_classification": result.opportunity_classification,
            "ranking_dimensions": result.ranking_dimensions,
            "positive_reasons": result.positive_reasons,
            "negative_reasons": result.negative_reasons,
            "hard_risks": result.hard_risks,
        }
    )


@router.post(
    "/validation/batches",
    response_model=ValidationBatchRead,
    dependencies=[Depends(require_api_key)],
)
def create_validation_batch(
    request: ValidationBatchCreate,
    db: Annotated[Session, Depends(get_db)],
) -> ValidationBatchRead:
    settings = get_settings()
    as_of = request.as_of or datetime.now(UTC)
    try:
        batch = create_blind_batch(
            db,
            name=request.name,
            listing_ids=request.listing_ids,
            data_mode=_mode(),
            as_of=as_of,
            market_config=load_market_config(settings.market_baseline_config_path),
            valuation_config=load_valuation_config(settings.valuation_config_path),
            future_config=load_future_config(settings.forecasting_config_path),
            decision_config=_decision_config(),
        )
        db.commit()
        return _validation_payload(db, batch)
    except _INPUT_ERRORS as exc:
        db.rollback()
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get(
    "/validation/batches/{batch_id}",
    response_model=ValidationBatchRead,
    dependencies=[Depends(require_api_key)],
)
def get_validation_batch(
    batch_id: uuid.UUID,
    db: Annotated[Session, Depends(get_db)],
) -> ValidationBatchRead:
    try:
        batch, _ = validation_items(db, batch_id)
        return _validation_payload(db, batch)
    except BlindValidationError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post(
    "/validation/batches/{batch_id}/labels",
    response_model=ValidationBatchRead,
    dependencies=[Depends(require_api_key)],
)
def label_validation_batch(
    batch_id: uuid.UUID,
    request: ValidationLabelsRequest,
    db: Annotated[Session, Depends(get_db)],
) -> ValidationBatchRead:
    try:
        for label in request.labels:
            label_blind_item(
                db,
                batch_id,
                label.listing_id,
                label.human_label,
                label.notes,
            )
        db.commit()
        batch, _ = validation_items(db, batch_id)
        return _validation_payload(db, batch)
    except BlindValidationError as exc:
        db.rollback()
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post(
    "/validation/batches/{batch_id}/reveal",
    response_model=ValidationBatchRead,
    dependencies=[Depends(require_api_key)],
)
def reveal_validation_batch(
    batch_id: uuid.UUID,
    db: Annotated[Session, Depends(get_db)],
) -> ValidationBatchRead:
    try:
        batch = reveal_blind_batch(db, batch_id, _decision_config())
        db.commit()
        return _validation_payload(db, batch)
    except BlindValidationError as exc:
        db.rollback()
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post(
    "/validation/runs",
    response_model=RealWorldValidationRunRead,
    dependencies=[Depends(require_api_key)],
)
def create_real_world_validation_run(
    request: RealWorldValidationCreate,
    db: Annotated[Session, Depends(get_db)],
) -> RealWorldValidationRunRead:
    settings = get_settings()
    market = load_market_config(settings.market_baseline_config_path)
    valuation = load_valuation_config(settings.valuation_config_path)
    future = load_future_config(settings.forecasting_config_path)
    decision = _decision_config()
    validation = load_real_world_validation_config(settings.validation_config_path)
    config_paths = tuple(sorted(settings.decision_config_path.parent.glob("*.yaml")))
    try:
        batch = create_validation_run(
            db,
            name=request.name,
            entries=[entry.model_dump(mode="json") for entry in request.entries],
            data_mode=_mode(),
            as_of=request.as_of or datetime.now(UTC),
            source_commit=settings.source_commit,
            source_tree_hash=settings.source_tree_hash,
            config_paths=config_paths,
            market_config=market,
            valuation_config=valuation,
            future_config=future,
            decision_config=decision,
            validation_config=validation,
        )
        db.commit()
        return _real_world_payload(db, batch)
    except (*_INPUT_ERRORS, RealWorldValidationError) as exc:
        db.rollback()
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get(
    "/validation/runs/{batch_id}",
    response_model=RealWorldValidationRunRead,
    dependencies=[Depends(require_api_key)],
)
def get_real_world_validation_run(
    batch_id: uuid.UUID,
    db: Annotated[Session, Depends(get_db)],
) -> RealWorldValidationRunRead:
    try:
        batch, _ = validation_run_items(db, batch_id)
        return _real_world_payload(db, batch)
    except RealWorldValidationError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post(
    "/validation/runs/{batch_id}/labels",
    response_model=RealWorldValidationRunRead,
    dependencies=[Depends(require_api_key)],
)
def label_real_world_validation_run(
    batch_id: uuid.UUID,
    request: RealWorldLabelsRequest,
    db: Annotated[Session, Depends(get_db)],
) -> RealWorldValidationRunRead:
    try:
        for label in request.labels:
            label_validation_item(
                db,
                batch_id,
                listing_id=label.listing_id,
                opportunity_classification=label.opportunity_classification,
                workflow_recommendation=label.workflow_recommendation,
                confidence=label.confidence,
                positive_reasons=label.positive_reasons,
                risks=label.risks,
                would_visit=label.would_physically_visit,
                notes=label.notes,
            )
        db.commit()
        batch, _ = validation_run_items(db, batch_id)
        return _real_world_payload(db, batch)
    except RealWorldValidationError as exc:
        db.rollback()
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post(
    "/validation/runs/{batch_id}/freeze-labels",
    response_model=RealWorldValidationRunRead,
    dependencies=[Depends(require_api_key)],
)
def freeze_real_world_validation_labels(
    batch_id: uuid.UUID,
    db: Annotated[Session, Depends(get_db)],
) -> RealWorldValidationRunRead:
    return _p56_transition(db, batch_id, lambda: freeze_human_labels(db, batch_id))


@router.post(
    "/validation/runs/{batch_id}/generate",
    response_model=RealWorldValidationRunRead,
    dependencies=[Depends(require_api_key)],
)
def generate_real_world_validation_results(
    batch_id: uuid.UUID,
    db: Annotated[Session, Depends(get_db)],
) -> RealWorldValidationRunRead:
    settings = get_settings()
    return _p56_transition(
        db,
        batch_id,
        lambda: generate_model_results(
            db,
            batch_id,
            market_config=load_market_config(settings.market_baseline_config_path),
            valuation_config=load_valuation_config(settings.valuation_config_path),
            future_config=load_future_config(settings.forecasting_config_path),
            decision_config=_decision_config(),
            source_commit=settings.source_commit,
            source_tree_hash=settings.source_tree_hash,
            config_paths=tuple(sorted(settings.decision_config_path.parent.glob("*.yaml"))),
        ),
    )


@router.post(
    "/validation/runs/{batch_id}/reveal",
    response_model=RealWorldValidationRunRead,
    dependencies=[Depends(require_api_key)],
)
def reveal_real_world_validation_results(
    batch_id: uuid.UUID,
    db: Annotated[Session, Depends(get_db)],
) -> RealWorldValidationRunRead:
    return _p56_transition(db, batch_id, lambda: reveal_validation_run(db, batch_id))


@router.post(
    "/validation/runs/{batch_id}/reviews",
    response_model=RealWorldValidationRunRead,
    dependencies=[Depends(require_api_key)],
)
def review_real_world_validation_run(
    batch_id: uuid.UUID,
    request: RealWorldReviewsRequest,
    db: Annotated[Session, Depends(get_db)],
) -> RealWorldValidationRunRead:
    try:
        for review in request.reviews:
            record_review(
                db,
                batch_id,
                listing_id=review.listing_id,
                why_ranked_verdict=review.why_ranked_verdict,
                root_cause=review.root_cause,
                notes=review.notes,
            )
        db.commit()
        batch, _ = validation_run_items(db, batch_id)
        return _real_world_payload(db, batch)
    except RealWorldValidationError as exc:
        db.rollback()
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post(
    "/validation/runs/{batch_id}/amendments",
    response_model=RealWorldValidationRunRead,
    dependencies=[Depends(require_api_key)],
)
def amend_real_world_validation_label(
    batch_id: uuid.UUID,
    request: RealWorldAmendmentRequest,
    db: Annotated[Session, Depends(get_db)],
) -> RealWorldValidationRunRead:
    try:
        append_label_amendment(
            db,
            batch_id,
            listing_id=request.listing_id,
            proposed_label=request.proposed_label,
            reason=request.reason,
        )
        db.commit()
        batch, _ = validation_run_items(db, batch_id)
        return _real_world_payload(db, batch)
    except RealWorldValidationError as exc:
        db.rollback()
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post(
    "/validation/runs/{batch_id}/finalize",
    response_model=RealWorldValidationRunRead,
    dependencies=[Depends(require_api_key)],
)
def finalize_real_world_validation(
    batch_id: uuid.UUID,
    db: Annotated[Session, Depends(get_db)],
) -> RealWorldValidationRunRead:
    settings = get_settings()
    return _p56_transition(
        db,
        batch_id,
        lambda: finalize_validation_run(
            db,
            batch_id,
            validation_config=load_real_world_validation_config(settings.validation_config_path),
            report_directory=settings.validation_report_directory,
        ),
    )


_INPUT_ERRORS = (
    BlindValidationError,
    DecisionInputsUnavailableError,
    FutureInputsUnavailableError,
    ListingNotAvailableForModeError,
    InsufficientValuationEvidenceError,
)


def _cached_result(
    db: Session, listing_id: uuid.UUID, as_of: datetime | None
) -> tuple[DecisionAssessment, bool]:
    if as_of is not None and as_of.tzinfo is None:
        raise HTTPException(status_code=422, detail="as_of must be timezone-aware")
    settings = get_settings()
    try:
        result = evaluate_cached_decision(
            db,
            listing_id,
            _mode(),
            as_of or datetime.now(UTC),
            load_market_config(settings.market_baseline_config_path),
            load_valuation_config(settings.valuation_config_path),
            load_future_config(settings.forecasting_config_path),
            _decision_config(),
        )
        db.commit()
        return result
    except (DecisionInputsUnavailableError, FutureInputsUnavailableError) as exc:
        db.rollback()
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ListingNotAvailableForModeError as exc:
        db.rollback()
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except InsufficientValuationEvidenceError as exc:
        db.rollback()
        raise HTTPException(status_code=422, detail=str(exc)) from exc


def _rank_context(
    db: Session, config: DecisionConfig
) -> tuple[dict[uuid.UUID, int], int, list[DecisionAssessment]]:
    latest = latest_decision_assessments(
        db,
        data_mode=_mode().value,
        decision_model_version=config.decision_model_version,
        configuration_version=config.configuration_version,
    )
    listing_ids = [item.listing_id for item in latest]
    active_ids = (
        set(
            db.scalars(
                select(Listing.id).where(
                    Listing.id.in_(listing_ids),
                    Listing.status == ListingStatus.ACTIVE.value,
                )
            )
        )
        if listing_ids
        else set()
    )
    latest = [item for item in latest if item.listing_id in active_ids]
    eligible = [item for item in latest if item.eligibility_status == "ELIGIBLE"]
    ordered = sorted(eligible, key=lambda item: decision_sort_key(item, config))
    return (
        {item.listing_id: rank for rank, item in enumerate(ordered, start=1)},
        len(ordered),
        latest,
    )


def _read_payload(
    result: DecisionAssessment,
    cache_hit: bool,
    rank: int | None,
    eligible_count: int,
) -> dict[str, Any]:
    return {
        column.name: getattr(result, column.name)
        for column in DecisionAssessment.__table__.columns
        if column.name not in {"id", "created_at"}
    } | {
        "data_notice": _data_notice(result.data_mode),
        "recommendation_status": "research_decision_workflow",
        "investment_conclusion": None,
        "cache_hit": cache_hit,
        "rank": rank,
        "eligible_count": eligible_count,
    }


def _validation_payload(db: Session, batch: DecisionValidationBatch) -> ValidationBatchRead:
    batch, items = validation_items(db, batch.id)
    revealed = batch.status == "revealed"
    return ValidationBatchRead.model_validate(
        {
            "id": batch.id,
            "name": batch.name,
            "data_mode": batch.data_mode,
            "status": batch.status,
            "target_sample_size": batch.target_sample_size,
            "actual_sample_size": len(items),
            "labeled_count": sum(item.human_label is not None for item in items),
            "input_cutoff_at": batch.input_cutoff_at,
            "decision_model_version": batch.decision_model_version,
            "configuration_version": batch.configuration_version,
            "metrics": batch.metrics if revealed else None,
            "items": [
                {
                    "listing_id": item.listing_id,
                    "listing": item.listing_snapshot,
                    "human_label": item.human_label,
                    "notes": item.notes,
                    "labeled_at": item.labeled_at,
                    "frozen_rank": item.frozen_rank if revealed else None,
                    "model_result": item.model_snapshot if revealed else None,
                }
                for item in items
            ],
        }
    )


def _p56_transition(db: Session, batch_id: uuid.UUID, operation: Any) -> RealWorldValidationRunRead:
    try:
        batch = operation()
        db.commit()
        return _real_world_payload(db, batch)
    except (*_INPUT_ERRORS, RealWorldValidationError) as exc:
        db.rollback()
        raise HTTPException(status_code=422, detail=str(exc)) from exc


def _real_world_payload(db: Session, batch: DecisionValidationBatch) -> RealWorldValidationRunRead:
    batch, items = validation_run_items(db, batch.id)
    visible = batch.status in {"revealed", "finalized"}
    item_ids = [item.id for item in items]
    reviewed_count = 0
    if item_ids:
        reviewed_count = len(
            list(
                db.scalars(
                    select(DecisionValidationReview).where(
                        DecisionValidationReview.item_id.in_(item_ids)
                    )
                )
            )
        )
    return RealWorldValidationRunRead.model_validate(
        {
            "id": batch.id,
            "validation_run_id": batch.validation_run_id,
            "name": batch.name,
            "protocol_version": batch.protocol_version,
            "data_mode": batch.data_mode,
            "status": batch.status,
            "target_sample_size": batch.target_sample_size,
            "actual_sample_size": len(items),
            "labeled_count": sum(_p56_label_payload(item) is not None for item in items),
            "reviewed_count": reviewed_count,
            "input_cutoff_at": batch.input_cutoff_at,
            "freeze_manifest": batch.freeze_manifest,
            "metrics": batch.metrics if visible else None,
            "final_recommendation": batch.final_recommendation,
            "labels_frozen_at": batch.labels_frozen_at,
            "model_generated_at": batch.model_generated_at if visible else None,
            "revealed_at": batch.revealed_at,
            "finalized_at": batch.finalized_at,
            "items": [
                {
                    "listing_id": item.listing_id,
                    "geography_bucket": item.geography_bucket,
                    "archetypes": item.archetypes,
                    "data_provenance": item.data_provenance,
                    "listing": item.listing_snapshot,
                    "human_label": _p56_label_payload(item),
                    "labeled_at": item.labeled_at,
                    "frozen_rank": item.frozen_rank if visible else None,
                    "model_result": item.model_snapshot if visible else None,
                }
                for item in items
            ],
        }
    )


def _p56_label_payload(item: Any) -> dict[str, Any] | None:
    if item.human_opportunity_classification is None:
        return None
    return {
        "opportunity_classification": item.human_opportunity_classification,
        "workflow_recommendation": item.human_workflow_recommendation,
        "confidence": item.human_confidence,
        "positive_reasons": item.human_positive_reasons,
        "risks": item.human_risks,
        "would_physically_visit": item.human_would_visit,
        "notes": item.notes,
    }


def _mode() -> DataMode:
    try:
        return DataMode(get_settings().market_data_mode)
    except ValueError as exc:
        raise RuntimeError("SHR_MARKET_DATA_MODE must be demo, sample, or live") from exc


def _decision_config() -> DecisionConfig:
    return load_decision_config(get_settings().decision_config_path)


def _data_notice(data_mode: str) -> str:
    if data_mode == "live":
        return "LIVE authorized evidence; VIEW is a workflow state, not investment advice."
    if data_mode == "demo":
        return "DEMO synthetic evidence; never present this as a live decision."
    return "SAMPLE evidence; this decision workflow remains research-only."

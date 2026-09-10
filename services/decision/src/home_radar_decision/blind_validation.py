from __future__ import annotations

import uuid
from dataclasses import asdict
from datetime import UTC, datetime
from typing import Any, cast

from home_radar_forecasting.config import FutureConfig
from home_radar_market.config import MarketBaselineConfig
from home_radar_models.decision import (
    DecisionAssessment,
    DecisionValidationBatch,
    DecisionValidationItem,
)
from home_radar_models.enums import DataMode
from home_radar_models.listing import Listing
from home_radar_valuation.config import ValuationConfig
from sqlalchemy import select
from sqlalchemy.orm import Session

from home_radar_decision.cache import evaluate_cached_decision
from home_radar_decision.config import DecisionConfig
from home_radar_decision.domain import BlindEvaluationCase, HumanLabel
from home_radar_decision.ranking import decision_sort_key
from home_radar_decision.validation import calculate_blind_metrics


class BlindValidationError(ValueError):
    pass


def create_blind_batch(
    session: Session,
    *,
    name: str,
    listing_ids: list[uuid.UUID],
    data_mode: DataMode,
    as_of: datetime,
    market_config: MarketBaselineConfig,
    valuation_config: ValuationConfig,
    future_config: FutureConfig,
    decision_config: DecisionConfig,
) -> DecisionValidationBatch:
    if as_of.tzinfo is None:
        raise BlindValidationError("as_of must be timezone-aware")
    if not listing_ids or len(listing_ids) != len(set(listing_ids)):
        raise BlindValidationError("listing_ids must be non-empty and unique")
    assessments = [
        evaluate_cached_decision(
            session,
            listing_id,
            data_mode,
            as_of,
            market_config,
            valuation_config,
            future_config,
            decision_config,
        )[0]
        for listing_id in listing_ids
    ]
    rank_by_listing = _rank_by_listing(assessments, decision_config)
    listings = _load_listings(session, listing_ids)
    batch = DecisionValidationBatch(
        name=name,
        data_mode=data_mode.value,
        status="blind_labeling",
        target_sample_size=decision_config.validation.target_sample_size,
        input_cutoff_at=as_of,
        decision_model_version=decision_config.decision_model_version,
        configuration_version=decision_config.configuration_version,
    )
    session.add(batch)
    session.flush()
    for assessment in assessments:
        listing = listings[assessment.listing_id]
        session.add(
            DecisionValidationItem(
                batch_id=batch.id,
                listing_id=listing.id,
                decision_assessment_id=assessment.id,
                frozen_rank=rank_by_listing.get(listing.id),
                listing_snapshot=_listing_snapshot(listing),
                model_snapshot=_model_snapshot(assessment),
            )
        )
    session.flush()
    return batch


def label_blind_item(
    session: Session,
    batch_id: uuid.UUID,
    listing_id: uuid.UUID,
    human_label: HumanLabel,
    notes: str | None = None,
) -> DecisionValidationItem:
    batch = session.get(DecisionValidationBatch, batch_id)
    if batch is None:
        raise BlindValidationError("validation batch not found")
    if batch.status != "blind_labeling":
        raise BlindValidationError("revealed validation batches are immutable")
    item = session.scalar(
        select(DecisionValidationItem).where(
            DecisionValidationItem.batch_id == batch_id,
            DecisionValidationItem.listing_id == listing_id,
        )
    )
    if item is None:
        raise BlindValidationError("listing is not part of this validation batch")
    item.human_label = human_label
    item.notes = notes
    item.labeled_at = datetime.now(UTC)
    session.flush()
    return item


def reveal_blind_batch(
    session: Session,
    batch_id: uuid.UUID,
    decision_config: DecisionConfig,
) -> DecisionValidationBatch:
    batch = session.get(DecisionValidationBatch, batch_id)
    if batch is None:
        raise BlindValidationError("validation batch not found")
    if batch.status == "revealed":
        return batch
    items = list(
        session.scalars(
            select(DecisionValidationItem).where(DecisionValidationItem.batch_id == batch_id)
        )
    )
    if not items or any(item.human_label is None for item in items):
        raise BlindValidationError("all listings must be labeled before reveal")
    metrics = calculate_blind_metrics(
        [_evaluation_case(item) for item in items], decision_config.validation
    )
    batch.metrics = _json_safe(asdict(metrics))
    batch.status = "revealed"
    batch.revealed_at = datetime.now(UTC)
    session.flush()
    return batch


def validation_items(
    session: Session, batch_id: uuid.UUID
) -> tuple[DecisionValidationBatch, list[DecisionValidationItem]]:
    batch = session.get(DecisionValidationBatch, batch_id)
    if batch is None:
        raise BlindValidationError("validation batch not found")
    items = list(
        session.scalars(
            select(DecisionValidationItem)
            .where(DecisionValidationItem.batch_id == batch_id)
            .order_by(DecisionValidationItem.created_at, DecisionValidationItem.id)
        )
    )
    return batch, items


def _rank_by_listing(
    assessments: list[DecisionAssessment], config: DecisionConfig
) -> dict[uuid.UUID, int]:
    eligible = [item for item in assessments if item.eligibility_status == "ELIGIBLE"]
    ordered = sorted(eligible, key=lambda item: decision_sort_key(item, config))
    return {item.listing_id: rank for rank, item in enumerate(ordered, start=1)}


def _load_listings(session: Session, listing_ids: list[uuid.UUID]) -> dict[uuid.UUID, Listing]:
    rows = list(session.scalars(select(Listing).where(Listing.id.in_(listing_ids))))
    by_id = {row.id: row for row in rows}
    missing = set(listing_ids) - set(by_id)
    if missing:
        raise BlindValidationError(f"listings not found: {sorted(map(str, missing))}")
    return by_id


def _listing_snapshot(listing: Listing) -> dict[str, Any]:
    return {
        "listing_id": str(listing.id),
        "source_url": listing.source_url,
        "district": listing.district,
        "submarket": listing.submarket,
        "community": listing.community,
        "current_ask": str(listing.total_price),
        "area_sqm": str(listing.area_sqm),
        "bedrooms": listing.bedrooms,
        "floor": listing.floor,
        "total_floors": listing.total_floors,
        "year_built": listing.year_built,
        "elevator": listing.elevator,
        "building_type": listing.building_type,
    }


def _model_snapshot(assessment: DecisionAssessment) -> dict[str, Any]:
    return {
        "decision_version": assessment.decision_version,
        "opportunity_classification": assessment.opportunity_classification,
        "workflow_state": assessment.workflow_state,
        "eligibility_status": assessment.eligibility_status,
        "value_score": str(assessment.value_score),
        "future_score": _optional_str(assessment.future_score),
        "liquidity_score": _optional_str(assessment.liquidity_score),
        "obsolescence_risk": _optional_str(assessment.obsolescence_risk),
        "positive_reasons": assessment.positive_reasons,
        "negative_reasons": assessment.negative_reasons,
    }


def _evaluation_case(item: DecisionValidationItem) -> BlindEvaluationCase:
    model = item.model_snapshot
    label = item.human_label
    if label is None:
        raise BlindValidationError("validation item is not labeled")
    return BlindEvaluationCase(
        listing_id=item.listing_id,
        rank=item.frozen_rank,
        opportunity_classification=model["opportunity_classification"],
        workflow_state=model["workflow_state"],
        eligibility_status=model["eligibility_status"],
        human_label=cast(HumanLabel, label),
    )


def _optional_str(value: object | None) -> str | None:
    return str(value) if value is not None else None


def _json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    if hasattr(value, "as_tuple"):
        return str(value)
    return value

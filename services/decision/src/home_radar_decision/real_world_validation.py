from __future__ import annotations

import hashlib
import json
import uuid
from collections import Counter
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

from home_radar_forecasting.config import FutureConfig
from home_radar_market.config import MarketBaselineConfig
from home_radar_market.materializer import materialize_baselines
from home_radar_market.windows import SHANGHAI
from home_radar_models.collection import CrawlRun
from home_radar_models.community import Community
from home_radar_models.decision import (
    DecisionAssessment,
    DecisionValidationBatch,
    DecisionValidationItem,
    DecisionValidationLabelAmendment,
    DecisionValidationReview,
)
from home_radar_models.enums import DataMode
from home_radar_models.future import EmploymentCenter, FutureFactorObservation, FutureProject
from home_radar_models.listing import Listing, ListingEvent, ListingSnapshot
from home_radar_models.market import BaselineMaterializationRun, MarketBaseline, MarketObservation
from home_radar_valuation.config import ValuationConfig
from sqlalchemy import delete, select, text
from sqlalchemy.orm import Session

from home_radar_decision.blind_validation import _listing_snapshot, _model_snapshot
from home_radar_decision.cache import evaluate_cached_decision
from home_radar_decision.config import DecisionConfig
from home_radar_decision.p56_config import RealWorldValidationConfig
from home_radar_decision.p56_metrics import calculate_real_world_metrics, gate_recommendation
from home_radar_decision.ranking import decision_sort_key

HUMAN_CLASSES = {
    "QUALITY_AT_DISCOUNT",
    "GOOD_BUT_EXPENSIVE",
    "VALUE_TRAP",
    "LOW_QUALITY",
    "INSUFFICIENT_INFORMATION",
}
WORKFLOWS = {"PASS", "WATCH", "CONTACT", "VIEW"}
CONFIDENCES = {"LOW", "MEDIUM", "HIGH"}
VERDICTS = {"AGREE", "PARTIAL", "CONTRADICT"}
ROOT_CAUSES = {
    "DATA_GAP",
    "BAD_COMPARABLE_SELECTION",
    "VALUATION_ERROR",
    "LIQUIDITY_ERROR",
    "FUTURE_FACTOR_ERROR",
    "OBSOLESCENCE_ERROR",
    "DECISION_RULE_ERROR",
    "HUMAN_DISAGREEMENT",
    "UNKNOWN",
}


class RealWorldValidationError(ValueError):
    pass


def create_validation_run(
    session: Session,
    *,
    name: str,
    entries: list[dict[str, Any]],
    data_mode: DataMode,
    as_of: datetime,
    source_commit: str,
    source_tree_hash: str,
    config_paths: tuple[Path, ...],
    market_config: MarketBaselineConfig,
    valuation_config: ValuationConfig,
    future_config: FutureConfig,
    decision_config: DecisionConfig,
    validation_config: RealWorldValidationConfig,
    validation_isolation: dict[str, Any] | None = None,
) -> DecisionValidationBatch:
    if as_of.tzinfo is None:
        raise RealWorldValidationError("as_of must be timezone-aware")
    if data_mode == DataMode.DEMO:
        raise RealWorldValidationError("P5.6 refuses DEMO data")
    if source_commit == "unknown" or source_tree_hash == "unknown":
        raise RealWorldValidationError("source commit and source tree hash must be frozen")
    _validate_entries(entries, validation_config)
    listing_ids = [uuid.UUID(str(entry["listing_id"])) for entry in entries]
    if len(listing_ids) != len(set(listing_ids)):
        raise RealWorldValidationError("listing_ids must be unique")
    listings = _load_listings(session, listing_ids)
    _validate_listings(listings, validation_config)

    now = datetime.now(UTC)
    run_id = f"p56-{now.strftime('%Y%m%dT%H%M%SZ')}-{uuid.uuid4().hex[:12]}"
    snapshots: list[dict[str, Any]] = []
    for entry in entries:
        listing = listings[uuid.UUID(str(entry["listing_id"]))]
        snapshots.append(
            {
                "listing": _validation_listing_snapshot(entry, listing),
                "geography_bucket": entry["geography_bucket"],
                "archetypes": sorted(set(entry["archetypes"])),
                "data_provenance": entry["data_provenance"],
            }
        )
    snapshots.sort(key=lambda value: value["listing"]["listing_id"])
    dataset_fingerprint = _fingerprint(snapshots)
    manifest = {
        "validation_run_id": run_id,
        "protocol_version": validation_config.protocol_version,
        "frozen_at": now.isoformat(),
        "git_commit": source_commit,
        "source_tree_hash": source_tree_hash,
        "p3_baseline_model_version": market_config.calculation_version,
        "p3_configuration_version": market_config.configuration_version,
        "p4_valuation_model_version": valuation_config.valuation_model_version,
        "p4_configuration_version": valuation_config.configuration_version,
        "p5_future_model_version": future_config.future_model_version,
        "p5_configuration_version": future_config.configuration_version,
        "p55_decision_model_version": decision_config.decision_model_version,
        "p55_configuration_version": decision_config.configuration_version,
        "p56_configuration_version": validation_config.configuration_version,
        "yaml_sha256": _config_hashes(config_paths),
        "db_schema_revision": _db_revision(session),
        "data_mode": data_mode.value,
        "dataset_fingerprint": dataset_fingerprint,
        "model_input_fingerprints": _validation_input_fingerprints(
            session,
            data_mode=data_mode,
            as_of=as_of,
            target_listing_ids=set(listing_ids),
            validation_isolation=validation_isolation,
        ),
    }
    if validation_isolation is not None:
        manifest["validation_isolation"] = _json_safe(validation_isolation)
    batch = DecisionValidationBatch(
        name=name,
        data_mode=data_mode.value,
        status="blind_labeling",
        target_sample_size=validation_config.dataset.target_sample_size,
        input_cutoff_at=as_of,
        decision_model_version=decision_config.decision_model_version,
        configuration_version=decision_config.configuration_version,
        protocol_version=validation_config.protocol_version,
        validation_run_id=run_id,
        freeze_manifest=manifest,
        dataset_fingerprint=dataset_fingerprint,
    )
    session.add(batch)
    session.flush()
    by_id = {str(entry["listing_id"]): entry for entry in entries}
    for listing_id in listing_ids:
        entry = by_id[str(listing_id)]
        session.add(
            DecisionValidationItem(
                batch_id=batch.id,
                listing_id=listing_id,
                decision_assessment_id=None,
                listing_snapshot=_validation_listing_snapshot(entry, listings[listing_id]),
                model_snapshot={},
                geography_bucket=entry["geography_bucket"],
                archetypes=sorted(set(entry["archetypes"])),
                data_provenance=entry["data_provenance"],
            )
        )
    session.flush()
    return batch


def _validation_listing_snapshot(entry: dict[str, Any], listing: Listing) -> dict[str, Any]:
    supplied = entry.get("blind_listing_snapshot")
    if supplied is None:
        return _listing_snapshot(listing)
    if not isinstance(supplied, dict):
        raise RealWorldValidationError("blind listing snapshot must be an object")
    forbidden = {
        "fair_value",
        "value_score",
        "future_score",
        "obsolescence_risk",
        "structural_alpha",
        "opportunity_classification",
        "workflow_state",
        "rank",
        "why_ranked",
    }
    leaked = sorted(forbidden & supplied.keys())
    if leaked:
        raise RealWorldValidationError(f"blind listing snapshot leaks model fields: {leaked}")
    return {"listing_id": str(listing.id), **_json_safe(supplied)}


def label_validation_item(
    session: Session,
    batch_id: uuid.UUID,
    *,
    listing_id: uuid.UUID,
    opportunity_classification: str,
    workflow_recommendation: str,
    confidence: str,
    positive_reasons: list[str],
    risks: list[str],
    would_visit: bool,
    notes: str | None,
) -> DecisionValidationItem:
    batch = _p56_batch(session, batch_id)
    if batch.status != "blind_labeling":
        raise RealWorldValidationError("human labels are immutable after label freeze")
    if opportunity_classification not in HUMAN_CLASSES:
        raise RealWorldValidationError("invalid human opportunity classification")
    if workflow_recommendation not in WORKFLOWS or confidence not in CONFIDENCES:
        raise RealWorldValidationError("invalid human workflow or confidence")
    if not 1 <= len(positive_reasons) <= 3 or not 1 <= len(risks) <= 3:
        raise RealWorldValidationError("provide 1 to 3 positive reasons and 1 to 3 risks")
    item = _item(session, batch_id, listing_id)
    item.human_opportunity_classification = opportunity_classification
    item.human_workflow_recommendation = workflow_recommendation
    item.human_confidence = confidence
    item.human_positive_reasons = positive_reasons
    item.human_risks = risks
    item.human_would_visit = would_visit
    item.notes = notes
    item.labeled_at = datetime.now(UTC)
    session.flush()
    return item


def freeze_human_labels(session: Session, batch_id: uuid.UUID) -> DecisionValidationBatch:
    batch = _p56_batch(session, batch_id)
    if batch.status == "labels_frozen":
        return batch
    if batch.status != "blind_labeling":
        raise RealWorldValidationError("labels can only be frozen after blind labeling")
    items = _items(session, batch_id)
    if not items or any(not _fully_labeled(item) for item in items):
        raise RealWorldValidationError("all listings require complete human labels before freeze")
    frozen = [_human_snapshot(item) for item in items]
    batch.human_labels_fingerprint = _fingerprint(frozen)
    batch.freeze_manifest = dict(batch.freeze_manifest) | {
        "human_labels_fingerprint": batch.human_labels_fingerprint
    }
    batch.labels_frozen_at = datetime.now(UTC)
    batch.status = "labels_frozen"
    session.flush()
    return batch


def generate_model_results(
    session: Session,
    batch_id: uuid.UUID,
    *,
    market_config: MarketBaselineConfig,
    valuation_config: ValuationConfig,
    future_config: FutureConfig,
    decision_config: DecisionConfig,
    source_commit: str,
    source_tree_hash: str,
    config_paths: tuple[Path, ...],
) -> DecisionValidationBatch:
    batch = _p56_batch(session, batch_id)
    if batch.status == "model_generated":
        return batch
    if batch.status != "labels_frozen":
        raise RealWorldValidationError("freeze human labels before model generation")
    items = _items(session, batch_id)
    _assert_frozen_runtime(
        session,
        batch,
        items,
        market_config,
        valuation_config,
        future_config,
        decision_config,
        source_commit,
        source_tree_hash,
        config_paths,
    )
    _assert_dataset_and_labels(batch, items)
    isolation = batch.freeze_manifest.get("validation_isolation")
    assessments = (
        _isolated_model_assessments(
            session,
            batch,
            items,
            isolation,
            market_config,
            valuation_config,
            future_config,
            decision_config,
        )
        if isinstance(isolation, dict)
        else [
            evaluate_cached_decision(
                session,
                item.listing_id,
                DataMode(batch.data_mode),
                batch.input_cutoff_at,
                market_config,
                valuation_config,
                future_config,
                decision_config,
            )[0]
            for item in items
        ]
    )
    rank_by_listing = _rank_by_listing(assessments, decision_config)
    assessment_by_listing = {assessment.listing_id: assessment for assessment in assessments}
    for item in items:
        assessment = assessment_by_listing[item.listing_id]
        item.decision_assessment_id = assessment.id
        item.frozen_rank = rank_by_listing.get(item.listing_id)
        item.model_snapshot = _model_snapshot(assessment) | {
            "current_ask": str(assessment.current_ask),
            "fair_value": str(assessment.fair_value),
            "fair_value_low": str(assessment.fair_value_low),
            "fair_value_high": str(assessment.fair_value_high),
            "structural_alpha": str(assessment.structural_alpha),
            "valuation_confidence": assessment.valuation_confidence,
            "future_confidence": assessment.future_confidence,
            "warnings": assessment.warnings,
            "hard_risks": assessment.hard_risks,
            "ranking_dimensions": assessment.ranking_dimensions,
        }
    # Keep the database-level stage guard deterministic: persist model rows while
    # the batch is still labels_frozen, then atomically advance the batch state.
    session.flush(items)
    model_snapshot = [
        {"listing_id": str(item.listing_id), "rank": item.frozen_rank, "model": item.model_snapshot}
        for item in items
    ]
    batch.model_outputs_fingerprint = _fingerprint(model_snapshot)
    batch.freeze_manifest = dict(batch.freeze_manifest) | {
        "model_outputs_fingerprint": batch.model_outputs_fingerprint
    }
    batch.model_generated_at = datetime.now(UTC)
    batch.status = "model_generated"
    session.flush()
    return batch


def _isolated_model_assessments(
    session: Session,
    batch: DecisionValidationBatch,
    items: list[DecisionValidationItem],
    isolation: dict[str, Any],
    market_config: MarketBaselineConfig,
    valuation_config: ValuationConfig,
    future_config: FutureConfig,
    decision_config: DecisionConfig,
) -> list[DecisionAssessment]:
    context_ids = {uuid.UUID(str(value)) for value in isolation.get("context_listing_ids", [])}
    target_ids = {item.listing_id for item in items}
    if not context_ids or context_ids & target_ids:
        raise RealWorldValidationError("validation context must be non-empty and target-free")
    exclusions = {
        str(target): {uuid.UUID(str(value)) for value in values}
        for target, values in isolation.get("target_near_duplicate_exclusions", {}).items()
    }
    if any(not values <= context_ids for values in exclusions.values()):
        raise RealWorldValidationError("near-duplicate exclusions must belong to CONTEXT")

    baseline = materialize_baselines(
        session,
        as_of_date=(batch.input_cutoff_at.astimezone(SHANGHAI).date() + timedelta(days=1)),
        data_mode=DataMode(batch.data_mode),
        config=market_config,
        input_cutoff_at=batch.input_cutoff_at,
        observation_listing_ids=context_ids,
        commit=False,
    )
    session.info["validation_baseline_run_id"] = baseline.run_id
    session.info["validation_context_listing_ids"] = context_ids
    session.info["validation_near_duplicate_exclusions"] = exclusions
    session.info["validation_allow_generated_after_cutoff"] = True
    try:
        assessments = [
            evaluate_cached_decision(
                session,
                item.listing_id,
                DataMode(batch.data_mode),
                batch.input_cutoff_at,
                market_config,
                valuation_config,
                future_config,
                decision_config,
            )[0]
            for item in items
        ]
        session.flush()
    finally:
        for key in (
            "validation_allow_generated_after_cutoff",
            "validation_near_duplicate_exclusions",
            "validation_context_listing_ids",
            "validation_baseline_run_id",
        ):
            session.info.pop(key, None)
        session.execute(
            delete(MarketBaseline).where(MarketBaseline.materialization_run_id == baseline.run_id)
        )
        session.execute(
            delete(BaselineMaterializationRun).where(
                BaselineMaterializationRun.id == baseline.run_id
            )
        )
    return assessments


def reveal_validation_run(session: Session, batch_id: uuid.UUID) -> DecisionValidationBatch:
    batch = _p56_batch(session, batch_id)
    if batch.status in {"revealed", "finalized"}:
        return batch
    if batch.status != "model_generated":
        raise RealWorldValidationError("generate frozen model results before reveal")
    items = _items(session, batch_id)
    _assert_fingerprints(batch, items)
    batch.metrics = _json_safe(calculate_real_world_metrics([_metric_case(item) for item in items]))
    batch.revealed_at = datetime.now(UTC)
    batch.status = "revealed"
    session.flush()
    return batch


def record_review(
    session: Session,
    batch_id: uuid.UUID,
    *,
    listing_id: uuid.UUID,
    why_ranked_verdict: str,
    root_cause: str | None,
    notes: str | None,
) -> DecisionValidationReview:
    batch = _p56_batch(session, batch_id)
    if batch.status not in {"revealed", "finalized"}:
        raise RealWorldValidationError("post-freeze review requires revealed results")
    if why_ranked_verdict not in VERDICTS or (root_cause and root_cause not in ROOT_CAUSES):
        raise RealWorldValidationError("invalid review verdict or root cause")
    item = _item(session, batch_id, listing_id)
    if _is_error(item) and root_cause is None:
        raise RealWorldValidationError("a root cause is required for every disagreement")
    existing = session.scalar(
        select(DecisionValidationReview).where(DecisionValidationReview.item_id == item.id)
    )
    if existing is not None:
        raise RealWorldValidationError(
            "reviews are append-only and this listing is already reviewed"
        )
    review = DecisionValidationReview(
        item_id=item.id,
        why_ranked_verdict=why_ranked_verdict,
        root_cause=root_cause,
        notes=notes,
    )
    session.add(review)
    session.flush()
    return review


def append_label_amendment(
    session: Session,
    batch_id: uuid.UUID,
    *,
    listing_id: uuid.UUID,
    proposed_label: dict[str, Any],
    reason: str,
) -> DecisionValidationLabelAmendment:
    batch = _p56_batch(session, batch_id)
    if batch.status not in {"revealed", "finalized"}:
        raise RealWorldValidationError("label corrections are only logged after reveal")
    item = _item(session, batch_id, listing_id)
    amendment = DecisionValidationLabelAmendment(
        item_id=item.id, proposed_label=proposed_label, reason=reason
    )
    session.add(amendment)
    session.flush()
    return amendment


def finalize_validation_run(
    session: Session,
    batch_id: uuid.UUID,
    *,
    validation_config: RealWorldValidationConfig,
    report_directory: Path,
) -> DecisionValidationBatch:
    batch = _p56_batch(session, batch_id)
    if batch.status == "finalized":
        return batch
    if batch.status != "revealed":
        raise RealWorldValidationError("reveal the run before finalization")
    items = _items(session, batch_id)
    _assert_fingerprints(batch, items)
    reviews = _reviews_by_listing(session, items)
    if len(reviews) != len(items):
        raise RealWorldValidationError("every listing requires a post-reveal Why Ranked review")
    for item in items:
        review = reviews[str(item.listing_id)]
        if _is_error(item) and not review["root_cause"]:
            raise RealWorldValidationError("every disagreement requires a root cause")
    metrics = calculate_real_world_metrics([_metric_case(item) for item in items], reviews)
    recommendation, checks = gate_recommendation(metrics, validation_config.gate)
    metrics["gate_checks"] = checks
    batch.metrics = _json_safe(metrics)
    batch.final_recommendation = recommendation
    batch.finalized_at = datetime.now(UTC)
    batch.status = "finalized"
    _write_reports(report_directory, batch, items, reviews)
    session.flush()
    return batch


def validation_run_items(
    session: Session, batch_id: uuid.UUID
) -> tuple[DecisionValidationBatch, list[DecisionValidationItem]]:
    batch = _p56_batch(session, batch_id)
    return batch, _items(session, batch_id)


def _validate_entries(entries: list[dict[str, Any]], config: RealWorldValidationConfig) -> None:
    dataset = config.dataset
    if not dataset.minimum_sample_size <= len(entries) <= dataset.maximum_sample_size:
        raise RealWorldValidationError(
            f"P5.6 requires {dataset.minimum_sample_size}-{dataset.maximum_sample_size} listings"
        )
    geographies = {entry.get("geography_bucket") for entry in entries}
    missing = set(dataset.required_geography_buckets) - geographies
    if missing:
        raise RealWorldValidationError(f"missing geography buckets: {sorted(missing)}")
    archetypes = [archetype for entry in entries for archetype in entry.get("archetypes", [])]
    if len(set(archetypes)) < dataset.minimum_distinct_archetypes:
        raise RealWorldValidationError("validation dataset lacks required archetype diversity")
    counts = Counter(archetypes)
    if counts and max(counts.values()) / len(entries) > float(
        dataset.maximum_single_archetype_share
    ):
        raise RealWorldValidationError("one archetype dominates the validation dataset")
    for entry in entries:
        provenance = entry.get("data_provenance", {})
        if provenance.get("kind") not in {"REAL_AUTHORIZED", "PROPERLY_ANONYMIZED"}:
            raise RealWorldValidationError(
                "every listing requires authorized or anonymized provenance"
            )
        if not provenance.get("reference"):
            raise RealWorldValidationError("every listing provenance requires a reference")


def _validate_listings(
    listings: dict[uuid.UUID, Listing], config: RealWorldValidationConfig
) -> None:
    for listing in listings.values():
        if (
            not config.dataset.minimum_price_rmb
            <= listing.total_price
            <= config.dataset.maximum_price_rmb
        ):
            raise RealWorldValidationError(f"listing {listing.id} is outside the P5.6 price range")


def _load_listings(session: Session, listing_ids: list[uuid.UUID]) -> dict[uuid.UUID, Listing]:
    rows = list(session.scalars(select(Listing).where(Listing.id.in_(listing_ids))))
    result = {row.id: row for row in rows}
    missing = set(listing_ids) - set(result)
    if missing:
        raise RealWorldValidationError(f"listings not found: {sorted(map(str, missing))}")
    return result


def _validation_input_fingerprints(
    session: Session,
    *,
    data_mode: DataMode,
    as_of: datetime,
    target_listing_ids: set[uuid.UUID],
    validation_isolation: dict[str, Any] | None,
) -> dict[str, dict[str, Any]]:
    """Fingerprint every persisted row the isolated P3/P4/P5 run can consume."""
    context_listing_ids: set[uuid.UUID] = set()
    if validation_isolation is not None:
        try:
            context_listing_ids = {
                uuid.UUID(str(value))
                for value in validation_isolation.get("context_listing_ids", [])
            }
        except (TypeError, ValueError, AttributeError) as exc:
            raise RealWorldValidationError(
                "validation context contains an invalid listing id"
            ) from exc
        if not context_listing_ids or context_listing_ids & target_listing_ids:
            raise RealWorldValidationError("validation context must be non-empty and target-free")

    all_listing_ids = target_listing_ids | context_listing_ids
    listings = list(
        session.scalars(select(Listing).where(Listing.id.in_(all_listing_ids)).order_by(Listing.id))
    )
    if {row.id for row in listings} != all_listing_ids:
        raise RealWorldValidationError("validation TARGET or CONTEXT listing is unavailable")

    observations = list(
        session.scalars(
            select(MarketObservation)
            .where(
                MarketObservation.data_mode == data_mode.value,
                MarketObservation.listing_id.in_(all_listing_ids),
                MarketObservation.observed_at <= as_of,
            )
            .order_by(MarketObservation.id)
        )
    )
    snapshots = list(
        session.scalars(
            select(ListingSnapshot)
            .join(CrawlRun, CrawlRun.id == ListingSnapshot.crawl_run_id)
            .where(
                CrawlRun.data_mode == data_mode.value,
                ListingSnapshot.listing_id.in_(all_listing_ids),
                ListingSnapshot.snapshot_at <= as_of,
            )
            .order_by(ListingSnapshot.id)
        )
    )
    events = list(
        session.scalars(
            select(ListingEvent)
            .join(CrawlRun, CrawlRun.id == ListingEvent.crawl_run_id)
            .where(
                CrawlRun.data_mode == data_mode.value,
                ListingEvent.listing_id.in_(all_listing_ids),
                ListingEvent.occurred_at <= as_of,
            )
            .order_by(ListingEvent.id)
        )
    )
    crawl_run_ids = {
        value
        for value in [
            *(row.crawl_run_id for row in observations),
            *(row.crawl_run_id for row in snapshots),
            *(row.crawl_run_id for row in events),
        ]
        if value is not None
    }
    crawl_runs = (
        list(
            session.scalars(
                select(CrawlRun).where(CrawlRun.id.in_(crawl_run_ids)).order_by(CrawlRun.id)
            )
        )
        if crawl_run_ids
        else []
    )

    target_listings = [row for row in listings if row.id in target_listing_ids]
    community_keys = {(row.district, row.submarket, row.community) for row in target_listings}
    communities = [
        row
        for row in session.scalars(select(Community).order_by(Community.id))
        if (row.district, row.submarket, row.community) in community_keys
    ]

    employment_centers = list(
        session.scalars(
            select(EmploymentCenter)
            .where(
                EmploymentCenter.data_mode == data_mode.value,
                EmploymentCenter.effective_from <= as_of,
                EmploymentCenter.source_timestamp <= as_of,
                (EmploymentCenter.effective_to.is_(None) | (EmploymentCenter.effective_to > as_of)),
            )
            .order_by(EmploymentCenter.id)
        )
    )
    projects = [
        row
        for row in session.scalars(
            select(FutureProject)
            .where(
                FutureProject.data_mode == data_mode.value,
                FutureProject.effective_from <= as_of,
                FutureProject.source_date <= as_of,
                (FutureProject.effective_to.is_(None) | (FutureProject.effective_to > as_of)),
            )
            .order_by(FutureProject.id)
        )
        if any(_future_project_applies(row, listing) for listing in listings)
    ]
    factor_observations = [
        row
        for row in session.scalars(
            select(FutureFactorObservation)
            .where(
                FutureFactorObservation.data_mode == data_mode.value,
                FutureFactorObservation.observed_at <= as_of,
                FutureFactorObservation.effective_from <= as_of,
                (
                    FutureFactorObservation.effective_to.is_(None)
                    | (FutureFactorObservation.effective_to > as_of)
                ),
            )
            .order_by(FutureFactorObservation.id)
        )
        if any(_future_factor_applies(row, listing) for listing in listings)
    ]

    sections: dict[str, list[Any]] = {
        "listing": listings,
        "community": communities,
        "market_observation": observations,
        "listing_snapshot": snapshots,
        "listing_event": events,
        "crawl_run": crawl_runs,
        "employment_center": employment_centers,
        "future_project": projects,
        "future_factor_observation": factor_observations,
    }
    return {
        name: {
            "row_count": len(rows),
            "sha256": _fingerprint([_database_row_snapshot(row) for row in rows]),
        }
        for name, rows in sections.items()
    }


def _future_project_applies(project: FutureProject, listing: Listing) -> bool:
    if project.district is None:
        return True
    if project.district != listing.district:
        return False
    if project.submarket is None:
        return True
    if project.submarket != listing.submarket:
        return False
    return project.community is None or project.community == listing.community


def _future_factor_applies(observation: FutureFactorObservation, listing: Listing) -> bool:
    if observation.listing_id == listing.id:
        return True
    if observation.scope_type == "shanghai":
        return True
    if observation.district != listing.district:
        return False
    if observation.scope_type == "district":
        return True
    if observation.submarket != listing.submarket:
        return False
    if observation.scope_type == "submarket":
        return True
    return observation.scope_type == "community" and observation.community == listing.community


def _database_row_snapshot(row: Any) -> dict[str, Any]:
    return {column.key: _json_safe(getattr(row, column.key)) for column in row.__table__.columns}


def _p56_batch(session: Session, batch_id: uuid.UUID) -> DecisionValidationBatch:
    batch = session.get(DecisionValidationBatch, batch_id)
    if batch is None or batch.protocol_version != "p5.6":
        raise RealWorldValidationError("P5.6 validation run not found")
    return batch


def _item(session: Session, batch_id: uuid.UUID, listing_id: uuid.UUID) -> DecisionValidationItem:
    result = session.scalar(
        select(DecisionValidationItem).where(
            DecisionValidationItem.batch_id == batch_id,
            DecisionValidationItem.listing_id == listing_id,
        )
    )
    if result is None:
        raise RealWorldValidationError("listing is not part of this validation run")
    return result


def _items(session: Session, batch_id: uuid.UUID) -> list[DecisionValidationItem]:
    return list(
        session.scalars(
            select(DecisionValidationItem)
            .where(DecisionValidationItem.batch_id == batch_id)
            .order_by(DecisionValidationItem.created_at, DecisionValidationItem.id)
        )
    )


def _rank_by_listing(
    assessments: list[DecisionAssessment], config: DecisionConfig
) -> dict[uuid.UUID, int]:
    eligible = [
        assessment for assessment in assessments if assessment.eligibility_status == "ELIGIBLE"
    ]
    ordered = sorted(eligible, key=lambda assessment: decision_sort_key(assessment, config))
    return {assessment.listing_id: rank for rank, assessment in enumerate(ordered, 1)}


def _fully_labeled(item: DecisionValidationItem) -> bool:
    return (
        all(
            value is not None
            for value in (
                item.human_opportunity_classification,
                item.human_workflow_recommendation,
                item.human_confidence,
                item.human_would_visit,
            )
        )
        and 1 <= len(item.human_positive_reasons) <= 3
        and 1 <= len(item.human_risks) <= 3
    )


def _human_snapshot(item: DecisionValidationItem) -> dict[str, Any]:
    return {
        "listing_id": str(item.listing_id),
        "opportunity_classification": item.human_opportunity_classification,
        "workflow_recommendation": item.human_workflow_recommendation,
        "confidence": item.human_confidence,
        "positive_reasons": item.human_positive_reasons,
        "risks": item.human_risks,
        "would_visit": item.human_would_visit,
        "notes": item.notes,
    }


def _metric_case(item: DecisionValidationItem) -> dict[str, Any]:
    model = item.model_snapshot
    return {
        "listing_id": str(item.listing_id),
        "rank": item.frozen_rank,
        "human_class": item.human_opportunity_classification,
        "human_workflow": item.human_workflow_recommendation,
        "human_would_visit": item.human_would_visit,
        "model_class": model["opportunity_classification"],
        "model_workflow": model["workflow_state"],
        "model_eligibility": model["eligibility_status"],
        "valuation_confidence": model.get("valuation_confidence"),
        "future_confidence": model.get("future_confidence"),
    }


def _is_error(item: DecisionValidationItem) -> bool:
    case = _metric_case(item)
    human_class = str(case["human_class"]).replace("INSUFFICIENT_INFORMATION", "INSUFFICIENT_DATA")
    return human_class != str(case["model_class"]) or str(case["human_workflow"]) != str(
        case["model_workflow"]
    )


def _reviews_by_listing(
    session: Session, items: list[DecisionValidationItem]
) -> dict[str, dict[str, Any]]:
    item_by_id = {item.id: item for item in items}
    rows = list(
        session.scalars(
            select(DecisionValidationReview).where(DecisionValidationReview.item_id.in_(item_by_id))
        )
    )
    return {
        str(item_by_id[review.item_id].listing_id): {
            "why_ranked_verdict": review.why_ranked_verdict,
            "root_cause": review.root_cause,
            "notes": review.notes,
        }
        for review in rows
    }


def _assert_frozen_runtime(
    session: Session,
    batch: DecisionValidationBatch,
    items: list[DecisionValidationItem],
    market: MarketBaselineConfig,
    valuation: ValuationConfig,
    future: FutureConfig,
    decision: DecisionConfig,
    source_commit: str,
    source_tree_hash: str,
    config_paths: tuple[Path, ...],
) -> None:
    manifest = batch.freeze_manifest
    current = {
        "p3_baseline_model_version": market.calculation_version,
        "p3_configuration_version": market.configuration_version,
        "p4_valuation_model_version": valuation.valuation_model_version,
        "p4_configuration_version": valuation.configuration_version,
        "p5_future_model_version": future.future_model_version,
        "p5_configuration_version": future.configuration_version,
        "p55_decision_model_version": decision.decision_model_version,
        "p55_configuration_version": decision.configuration_version,
        "git_commit": source_commit,
        "source_tree_hash": source_tree_hash,
        "yaml_sha256": _config_hashes(config_paths),
        "model_input_fingerprints": _validation_input_fingerprints(
            session,
            data_mode=DataMode(batch.data_mode),
            as_of=batch.input_cutoff_at,
            target_listing_ids={item.listing_id for item in items},
            validation_isolation=(
                batch.freeze_manifest.get("validation_isolation")
                if isinstance(batch.freeze_manifest.get("validation_isolation"), dict)
                else None
            ),
        ),
    }
    changed = [key for key, value in current.items() if manifest.get(key) != value]
    if changed:
        raise RealWorldValidationError(f"frozen runtime changed: {changed}")


def _assert_fingerprints(
    batch: DecisionValidationBatch, items: list[DecisionValidationItem]
) -> None:
    _assert_dataset_and_labels(batch, items)
    models = _fingerprint(
        [
            {
                "listing_id": str(item.listing_id),
                "rank": item.frozen_rank,
                "model": item.model_snapshot,
            }
            for item in items
        ]
    )
    if models != batch.model_outputs_fingerprint:
        raise RealWorldValidationError("frozen model evidence failed integrity check")


def _assert_dataset_and_labels(
    batch: DecisionValidationBatch, items: list[DecisionValidationItem]
) -> None:
    dataset_rows: list[dict[str, Any]] = [
        {
            "listing": item.listing_snapshot,
            "geography_bucket": item.geography_bucket,
            "archetypes": item.archetypes,
            "data_provenance": item.data_provenance,
        }
        for item in items
    ]
    dataset_rows.sort(key=lambda value: value["listing"]["listing_id"])
    dataset = _fingerprint(dataset_rows)
    human = _fingerprint([_human_snapshot(item) for item in items])
    if dataset != batch.dataset_fingerprint or human != batch.human_labels_fingerprint:
        raise RealWorldValidationError("frozen dataset or human labels failed integrity check")


def _config_hashes(paths: tuple[Path, ...]) -> dict[str, str]:
    result: dict[str, str] = {}
    for path in sorted(paths, key=lambda value: str(value)):
        result[str(path)] = hashlib.sha256(path.read_bytes()).hexdigest()
    return result


def _db_revision(session: Session) -> str:
    try:
        value = session.scalar(text("SELECT version_num FROM alembic_version"))
    except Exception as exc:
        raise RealWorldValidationError("cannot freeze database schema revision") from exc
    if not value:
        raise RealWorldValidationError("database schema revision is unavailable")
    return str(value)


def _fingerprint(value: Any) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(payload.encode()).hexdigest()


def _json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_safe(item) for item in value]
    if isinstance(value, Decimal):
        return str(value)
    return value


def _write_reports(
    directory: Path,
    batch: DecisionValidationBatch,
    items: list[DecisionValidationItem],
    reviews: dict[str, dict[str, Any]],
) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    ranked = sorted(items, key=lambda item: item.frozen_rank or 10**9)
    geography = Counter(item.geography_bucket for item in items)
    archetypes = Counter(archetype for item in items for archetype in item.archetypes)
    metrics = batch.metrics
    main = [
        "# P5.6 Real-World Validation Report",
        "",
        f"Run ID: `{batch.validation_run_id}`",
        f"Data mode: `{batch.data_mode}`",
        f"Sample size: {len(items)}",
        f"Final recommendation: `{batch.final_recommendation}`",
        "",
        "## Dataset composition",
        "",
        f"Geography: `{json.dumps(dict(sorted(geography.items())), ensure_ascii=False)}`",
        f"Archetypes: `{json.dumps(dict(sorted(archetypes.items())), ensure_ascii=False)}`",
        "",
        "## Frozen system",
        "",
        "```json",
        json.dumps(batch.freeze_manifest, ensure_ascii=False, indent=2, default=str),
        "```",
        "",
        "## KPIs and gate",
        "",
        "```json",
        json.dumps(metrics, ensure_ascii=False, indent=2, default=str),
        "```",
        "",
        "## Frozen ranking",
        "",
        "| Rank | Listing | Model class | Human class | Model workflow | Human workflow | Visit |",
        "|---:|---|---|---|---|---|---|",
    ]
    for item in ranked:
        model = item.model_snapshot
        main.append(
            f"| {item.frozen_rank or '-'} | {item.listing_id} | "
            f"{model['opportunity_classification']} | {item.human_opportunity_classification} | "
            f"{model['workflow_state']} | {item.human_workflow_recommendation} | "
            f"{item.human_would_visit} |"
        )
    main.extend(
        [
            "",
            "## Limitations",
            "",
            "This is a frozen 50-listing decision-quality test. "
            "It does not estimate long-run market returns.",
            "",
            "## Engineering recommendation",
            "",
            str(batch.final_recommendation),
        ]
    )
    (directory / "P56_REAL_WORLD_VALIDATION_REPORT.md").write_text(
        "\n".join(main) + "\n", encoding="utf-8"
    )

    errors = [item for item in ranked if _is_error(item)]
    error_lines = [
        "# P5.6 Error Review",
        "",
        f"Run ID: `{batch.validation_run_id}`",
        "",
    ]
    errors.sort(
        key=lambda item: (
            item.human_opportunity_classification != "VALUE_TRAP",
            item.frozen_rank or 10**9,
        )
    )
    for item in errors:
        model = item.model_snapshot
        review = reviews[str(item.listing_id)]
        error_lines.extend(
            [
                f"## {item.listing_id}",
                "",
                f"- Rank: {item.frozen_rank or 'unranked'}",
                f"- Property: {json.dumps(item.listing_snapshot, ensure_ascii=False, default=str)}",
                f"- Model: {model['opportunity_classification']} / {model['workflow_state']}",
                "- Human: "
                f"{item.human_opportunity_classification} / "
                f"{item.human_workflow_recommendation}",
                "- Scores: "
                f"value={model.get('value_score')}, "
                f"future={model.get('future_score')}, "
                f"liquidity={model.get('liquidity_score')}, "
                f"obsolescence={model.get('obsolescence_risk')}",
                "- Confidence: "
                f"valuation={model.get('valuation_confidence')}, "
                f"future={model.get('future_confidence')}",
                f"- Reasons: {json.dumps(model.get('positive_reasons', []), ensure_ascii=False)}",
                f"- Warnings: {json.dumps(model.get('warnings', []), ensure_ascii=False)}",
                f"- Root cause: {review['root_cause']}",
                f"- Review: {review.get('notes') or ''}",
                "",
            ]
        )
    filename = f"P56_ERROR_REVIEW_{batch.validation_run_id}.md"
    (directory / filename).write_text("\n".join(error_lines) + "\n", encoding="utf-8")

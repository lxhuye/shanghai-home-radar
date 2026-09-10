from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from time import monotonic
from typing import Any

from home_radar_models.enums import DataMode
from home_radar_models.market import (
    BaselineMaterializationRun,
    MarketBaseline,
    MarketObservation,
)
from sqlalchemy import delete, select, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from home_radar_market.computation import SliceComputation, compute_slice
from home_radar_market.config import MarketBaselineConfig
from home_radar_market.domain import (
    BaselineSliceKey,
    ObservationRecord,
    latest_per_entity,
    slice_keys,
)
from home_radar_market.observation_service import import_listing_observations
from home_radar_market.windows import closed_day_window


@dataclass(frozen=True)
class MaterializationResult:
    run_id: uuid.UUID
    input_signature: str
    imported_observations: int
    baseline_count: int
    idempotent_replay: bool


class MaterializationInProgressError(RuntimeError):
    pass


def materialize_baselines(
    session: Session,
    *,
    as_of_date: date,
    data_mode: DataMode,
    config: MarketBaselineConfig,
    input_cutoff_at: datetime | None = None,
    observation_listing_ids: set[uuid.UUID] | None = None,
    commit: bool = True,
) -> MaterializationResult:
    lock_key = materialization_lock_key(as_of_date, data_mode)
    with _materialization_lock(session, lock_key):
        return _materialize_baselines_unlocked(
            session,
            as_of_date=as_of_date,
            data_mode=data_mode,
            config=config,
            input_cutoff_at=input_cutoff_at,
            observation_listing_ids=observation_listing_ids,
            commit=commit,
        )


def _materialize_baselines_unlocked(
    session: Session,
    *,
    as_of_date: date,
    data_mode: DataMode,
    config: MarketBaselineConfig,
    input_cutoff_at: datetime | None,
    observation_listing_ids: set[uuid.UUID] | None,
    commit: bool,
) -> MaterializationResult:
    if input_cutoff_at is not None and input_cutoff_at.tzinfo is None:
        raise ValueError("input_cutoff_at must be timezone-aware")
    imported = import_listing_observations(session, data_mode, config)
    _persist(session, commit)
    input_cutoff = input_cutoff_at or datetime.now(UTC)

    largest_window = closed_day_window(as_of_date, max(config.windows_days))
    statement = select(MarketObservation).where(
        MarketObservation.data_mode == data_mode.value,
        MarketObservation.observed_at < largest_window.end_at,
    )
    if observation_listing_ids is not None:
        statement = statement.where(
            MarketObservation.listing_id.in_(observation_listing_ids),
            MarketObservation.observed_at <= input_cutoff,
        )
    else:
        statement = statement.where(MarketObservation.created_at <= input_cutoff)
    models = session.scalars(statement).all()
    records = [ObservationRecord.from_model(model) for model in models]
    input_signature = _input_signature(records)
    existing = session.scalar(
        select(BaselineMaterializationRun).where(
            BaselineMaterializationRun.as_of_date == as_of_date,
            BaselineMaterializationRun.data_mode == data_mode.value,
            BaselineMaterializationRun.calculation_version == config.calculation_version,
            BaselineMaterializationRun.configuration_version == config.configuration_version,
            BaselineMaterializationRun.input_signature == input_signature,
        )
    )
    if existing is not None and existing.status == "succeeded":
        return MaterializationResult(
            existing.id, input_signature, imported, existing.baseline_count, True
        )

    started_at = datetime.now(UTC)
    started_clock = monotonic()
    if existing is None:
        run = BaselineMaterializationRun(
            as_of_date=as_of_date,
            data_mode=data_mode.value,
            calculation_version=config.calculation_version,
            configuration_version=config.configuration_version,
            input_signature=input_signature,
            input_cutoff_at=input_cutoff,
            status="running",
            started_at=started_at,
        )
        session.add(run)
        _persist(session, commit)
    else:
        run = existing
        session.execute(
            delete(MarketBaseline).where(MarketBaseline.materialization_run_id == run.id)
        )
        run.status = "running"
        run.started_at = started_at
        run.finished_at = None
        run.error_message = None
        run.input_cutoff_at = input_cutoff
        _persist(session, commit)

    try:
        baselines = _build_baselines(
            run=run,
            records=records,
            as_of_date=as_of_date,
            data_mode=data_mode,
            input_cutoff=input_cutoff,
            config=config,
            generated_at=started_at,
        )
        session.add_all(baselines)
        run.observation_count = len(records)
        run.baseline_count = len(baselines)
        run.status = "succeeded"
        run.finished_at = datetime.now(UTC)
        run.duration_ms = int((monotonic() - started_clock) * 1000)
        _persist(session, commit)
    except Exception as exc:
        if not commit:
            raise
        session.rollback()
        failed_run = session.get(BaselineMaterializationRun, run.id)
        assert failed_run is not None
        failed_run.status = "failed"
        failed_run.finished_at = datetime.now(UTC)
        failed_run.duration_ms = int((monotonic() - started_clock) * 1000)
        failed_run.error_message = f"{exc.__class__.__name__}: {exc}"[:4000]
        session.commit()
        raise
    return MaterializationResult(run.id, input_signature, imported, len(baselines), False)


def _persist(session: Session, commit: bool) -> None:
    if commit:
        session.commit()
    else:
        session.flush()


def materialization_lock_key(as_of_date: date, data_mode: DataMode) -> int:
    digest = hashlib.blake2b(
        f"market-baseline:{data_mode.value}:{as_of_date.isoformat()}".encode(), digest_size=8
    ).digest()
    return int.from_bytes(digest, byteorder="big", signed=True)


@contextmanager
def _materialization_lock(session: Session, lock_key: int) -> Iterator[None]:
    bind = session.get_bind()
    if bind.dialect.name != "postgresql":
        yield
        return
    engine = bind if isinstance(bind, Engine) else bind.engine
    with engine.connect() as lock_connection:
        acquired = lock_connection.scalar(
            text("SELECT pg_try_advisory_lock(:lock_key)"), {"lock_key": lock_key}
        )
        if not acquired:
            session.rollback()
            raise MaterializationInProgressError("materialization already owns this date and mode")
        try:
            yield
        finally:
            lock_connection.execute(
                text("SELECT pg_advisory_unlock(:lock_key)"), {"lock_key": lock_key}
            )


def _build_baselines(
    *,
    run: BaselineMaterializationRun,
    records: list[ObservationRecord],
    as_of_date: date,
    data_mode: DataMode,
    input_cutoff: datetime,
    config: MarketBaselineConfig,
    generated_at: datetime,
) -> list[MarketBaseline]:
    baselines: list[MarketBaseline] = []
    for window_days in config.windows_days:
        window = closed_day_window(as_of_date, window_days)
        window_records = [
            record for record in records if window.start_at <= record.observed_at < window.end_at
        ]
        deduplicated = latest_per_entity(window_records)
        groups: dict[BaselineSliceKey, list[ObservationRecord]] = {}
        for record in deduplicated:
            for key in slice_keys(record, window_days):
                groups.setdefault(key, []).append(record)
        for key, group in groups.items():
            calculation = compute_slice(group, records, key, window.start_at, window.end_at, config)
            baselines.append(
                _baseline_model(
                    run=run,
                    key=key,
                    calculation=calculation,
                    as_of_date=as_of_date,
                    data_mode=data_mode,
                    input_cutoff=input_cutoff,
                    config=config,
                    generated_at=generated_at,
                    window_start=window.start_at,
                    window_end=window.end_at,
                )
            )
    return baselines


def _baseline_model(
    *,
    run: BaselineMaterializationRun,
    key: BaselineSliceKey,
    calculation: SliceComputation,
    as_of_date: date,
    data_mode: DataMode,
    input_cutoff: datetime,
    config: MarketBaselineConfig,
    generated_at: datetime,
    window_start: datetime,
    window_end: datetime,
) -> MarketBaseline:
    listing = calculation.listing_metrics
    identity = {
        "as_of_date": as_of_date.isoformat(),
        "data_mode": data_mode.value,
        "input_signature": run.input_signature,
        "calculation_version": config.calculation_version,
        "configuration_version": config.configuration_version,
        "slice": key.__dict__,
    }
    baseline_version = hashlib.sha256(
        json.dumps(identity, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()
    liquidity_components = {
        name: {
            "raw_value": str(component.raw_value),
            "normalized_score": str(component.normalized_score),
            "weight": str(component.weight),
            "semantic_note": component.semantic_note,
        }
        for name, component in calculation.liquidity.components.items()
    }
    return MarketBaseline(
        materialization_run_id=run.id,
        as_of_date=as_of_date,
        generated_at=generated_at,
        data_mode=data_mode.value,
        observation_type=key.observation_type,
        window_days=key.window_days,
        level=key.level,
        district=key.district,
        submarket=key.submarket,
        community=key.community,
        area_bucket=key.area_bucket,
        layout=key.layout,
        observation_count=calculation.observation_count,
        outlier_count=calculation.outlier_count,
        effective_price_sample_count=calculation.effective_price_sample_count,
        effective_unit_price_sample_count=calculation.effective_unit_price_sample_count,
        price_p10=calculation.price.p10,
        price_p25=calculation.price.p25,
        price_p50=calculation.price.p50,
        price_p75=calculation.price.p75,
        price_p90=calculation.price.p90,
        unit_price_p10=calculation.unit_price.p10,
        unit_price_p25=calculation.unit_price.p25,
        unit_price_p50=calculation.unit_price.p50,
        unit_price_p75=calculation.unit_price.p75,
        unit_price_p90=calculation.unit_price.p90,
        active_inventory=listing.active_inventory if listing else 0,
        new_listings=listing.new_listings if listing else 0,
        price_cut_count=listing.price_cut_count if listing else 0,
        price_cut_ratio=listing.price_cut_ratio if listing else None,
        median_initial_ask=listing.median_initial_ask if listing else None,
        median_current_ask=listing.median_current_ask if listing else None,
        median_price_cut_pct=listing.median_price_cut_pct if listing else None,
        median_days_on_market=listing.median_days_on_market if listing else None,
        relisting_rate=listing.relisting_rate if listing else None,
        missing_candidate_count=listing.missing_candidate_count if listing else 0,
        inactive_count=listing.inactive_count if listing else 0,
        ask_price_change_30d=listing.ask_price_change_30d if listing else None,
        ask_price_change_90d=listing.ask_price_change_90d if listing else None,
        inventory_change_30d=listing.inventory_change_30d if listing else None,
        inventory_change_90d=listing.inventory_change_90d if listing else None,
        median_monthly_rent=calculation.median_monthly_rent,
        rent_per_sqm=calculation.rent_per_sqm,
        liquidity_score=calculation.liquidity.score,
        liquidity_confidence=calculation.liquidity.confidence,
        liquidity_components=liquidity_components,
        confidence_level=calculation.confidence.level.value,
        confidence_score=calculation.confidence.score,
        confidence_components={
            "values": {
                name: str(value) for name, value in calculation.confidence.components.items()
            },
            "caps": list(calculation.confidence.caps),
            "weights": {name: str(value) for name, value in config.confidence.weights.items()},
        },
        source_types=[key.observation_type],
        sources=list(calculation.sources),
        observation_start=calculation.observation_start,
        observation_end=calculation.observation_end,
        window_start_at=window_start,
        window_end_at=window_end,
        input_cutoff_at=input_cutoff,
        calculation_version=config.calculation_version,
        configuration_version=config.configuration_version,
        baseline_version=baseline_version,
        provenance={
            **calculation.provenance,
            "input_signature": run.input_signature,
            "materialization_run_id": str(run.id),
            "window_bounds": "[start_at,end_at)",
            "asking_price_not_transaction": key.observation_type == "listing",
        },
    )


def _input_signature(records: list[ObservationRecord]) -> str:
    payload: list[dict[str, Any]] = []
    for record in sorted(records, key=lambda item: str(item.id)):
        payload.append(
            {
                "id": str(record.id),
                "type": record.observation_type,
                "source": record.source,
                "source_record_id": record.source_record_id,
                "district": record.district,
                "submarket": record.submarket,
                "community": record.community,
                "area_bucket": record.area_bucket,
                "layout": record.layout,
                "observed_at": record.observed_at.isoformat(),
                "created_at": record.created_at.isoformat(),
                "total_price": _decimal(record.total_price),
                "unit_price": _decimal(record.unit_price),
                "monthly_rent": _decimal(record.monthly_rent),
                "rent_per_sqm": _decimal(record.rent_per_sqm),
                "index_value": _decimal(record.index_value),
                "coverage_complete": record.coverage_complete,
                "listing_status": record.metadata.get("listing_status"),
                "event_types": record.metadata.get("event_types"),
            }
        )
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _decimal(value: Decimal | None) -> str | None:
    return str(value) if value is not None else None

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any

from home_radar_market.config import MarketBaselineConfig
from home_radar_market.resolver import BaselineCandidate, resolve_hierarchy
from home_radar_models.collection import CrawlRun
from home_radar_models.community import Community
from home_radar_models.enums import (
    BaselineConfidence,
    BaselineLevel,
    CrawlRunStatus,
    DataMode,
    ListingEventType,
    MarketObservationType,
)
from home_radar_models.listing import Listing, ListingEvent, ListingSnapshot
from home_radar_models.market import (
    BaselineMaterializationRun,
    MarketBaseline,
    MarketObservation,
)
from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session

from home_radar_valuation.config import ValuationConfig
from home_radar_valuation.domain import (
    BaselineEvidence,
    ComparableRecord,
    PriceHistory,
    TargetProperty,
)
from home_radar_valuation.history import build_price_history


class ListingNotAvailableForModeError(LookupError):
    pass


@dataclass(frozen=True)
class EvaluationInputs:
    target: TargetProperty
    candidates: list[ComparableRecord]
    valuation_baseline: BaselineEvidence | None
    listing_baseline: BaselineEvidence | None
    price_history: PriceHistory
    latest_observation_id: uuid.UUID | None


def load_listing_inputs(
    session: Session,
    listing_id: uuid.UUID,
    data_mode: DataMode,
    as_of: datetime,
    market_config: MarketBaselineConfig,
    valuation_config: ValuationConfig,
) -> EvaluationInputs:
    row = session.execute(
        select(MarketObservation, Listing)
        .join(Listing, Listing.id == MarketObservation.listing_id)
        .where(
            MarketObservation.listing_id == listing_id,
            MarketObservation.data_mode == data_mode.value,
            MarketObservation.observation_type == MarketObservationType.LISTING.value,
            MarketObservation.observed_at <= as_of,
        )
        .order_by(MarketObservation.observed_at.desc(), MarketObservation.created_at.desc())
        .limit(1)
    ).one_or_none()
    if row is None:
        raise ListingNotAvailableForModeError(
            f"listing {listing_id} is not available in {data_mode.value} mode"
        )
    observation, listing = row
    community = session.scalar(
        select(Community).where(
            Community.district == observation.district,
            Community.submarket == observation.submarket,
            Community.community == observation.community,
        )
    )
    target = _target_from_models(observation, listing, community, data_mode)
    history = _load_price_history(session, listing, data_mode, as_of, target.current_ask)
    return load_market_inputs(
        session,
        target,
        history,
        as_of,
        market_config,
        valuation_config,
        latest_observation_id=observation.id,
    )


def load_market_inputs(
    session: Session,
    target: TargetProperty,
    price_history: PriceHistory,
    as_of: datetime,
    market_config: MarketBaselineConfig,
    valuation_config: ValuationConfig,
    *,
    latest_observation_id: uuid.UUID | None = None,
) -> EvaluationInputs:
    data_mode = DataMode(target.data_mode)
    candidates = _load_comparable_records(
        session,
        target,
        data_mode,
        as_of,
        max(valuation_config.comparables.windows_days),
    )
    run = _latest_baseline_run(session, data_mode, as_of)
    listing_baseline = _resolve_best_baseline(
        session,
        run,
        target,
        MarketObservationType.LISTING,
        market_config,
        valuation_config,
    )
    transaction_baseline = _resolve_best_baseline(
        session,
        run,
        target,
        MarketObservationType.TRANSACTION,
        market_config,
        valuation_config,
    )
    valuation_baseline = _choose_valuation_baseline(
        listing_baseline, transaction_baseline, valuation_config
    )
    return EvaluationInputs(
        target=target,
        candidates=candidates,
        valuation_baseline=valuation_baseline,
        listing_baseline=listing_baseline,
        price_history=price_history,
        latest_observation_id=latest_observation_id,
    )


def _load_comparable_records(
    session: Session,
    target: TargetProperty,
    data_mode: DataMode,
    as_of: datetime,
    maximum_days: int,
) -> list[ComparableRecord]:
    statement = (
        select(MarketObservation, Listing)
        .outerjoin(Listing, Listing.id == MarketObservation.listing_id)
        .where(
            MarketObservation.data_mode == data_mode.value,
            MarketObservation.observation_type.in_(
                [
                    MarketObservationType.LISTING.value,
                    MarketObservationType.TRANSACTION.value,
                ]
            ),
            MarketObservation.observed_at <= as_of,
            MarketObservation.observed_at >= as_of - timedelta(days=maximum_days),
            MarketObservation.area_sqm.is_not(None),
            MarketObservation.total_price.is_not(None),
            MarketObservation.unit_price.is_not(None),
            or_(
                MarketObservation.community == target.community,
                MarketObservation.submarket == target.submarket,
            ),
        )
        .order_by(MarketObservation.observed_at.desc())
    )
    context_listing_ids = session.info.get("validation_context_listing_ids")
    if context_listing_ids is not None:
        statement = statement.where(MarketObservation.listing_id.in_(context_listing_ids))
        exclusions = session.info.get("validation_near_duplicate_exclusions", {}).get(
            str(target.listing_id), set()
        )
        if exclusions:
            statement = statement.where(MarketObservation.listing_id.not_in(exclusions))
    rows = session.execute(statement).all()
    return [
        _comparable_from_models(observation, listing)
        for observation, listing in rows
        if observation.area_sqm is not None
        and observation.total_price is not None
        and observation.unit_price is not None
    ]


def _latest_baseline_run(
    session: Session, data_mode: DataMode, as_of: datetime
) -> BaselineMaterializationRun | None:
    validation_run_id = session.info.get("validation_baseline_run_id")
    if validation_run_id is not None:
        run = session.get(BaselineMaterializationRun, validation_run_id)
        if run is None or run.status != CrawlRunStatus.SUCCEEDED.value:
            raise ListingNotAvailableForModeError("validation baseline run is unavailable")
        return run
    return session.scalar(
        select(BaselineMaterializationRun)
        .where(
            BaselineMaterializationRun.data_mode == data_mode.value,
            BaselineMaterializationRun.status == CrawlRunStatus.SUCCEEDED.value,
            BaselineMaterializationRun.input_cutoff_at <= as_of,
        )
        .order_by(
            BaselineMaterializationRun.as_of_date.desc(),
            BaselineMaterializationRun.finished_at.desc(),
        )
        .limit(1)
    )


def _resolve_best_baseline(
    session: Session,
    run: BaselineMaterializationRun | None,
    target: TargetProperty,
    observation_type: MarketObservationType,
    market_config: MarketBaselineConfig,
    valuation_config: ValuationConfig,
) -> BaselineEvidence | None:
    if run is None:
        return None
    for window_days in valuation_config.comparables.windows_days:
        resolved = _resolve_baseline(
            session,
            run,
            target,
            observation_type,
            window_days,
            market_config,
        )
        if resolved is not None:
            return resolved
    return None


def _resolve_baseline(
    session: Session,
    run: BaselineMaterializationRun,
    target: TargetProperty,
    observation_type: MarketObservationType,
    window_days: int,
    market_config: MarketBaselineConfig,
) -> BaselineEvidence | None:
    area_bucket = market_config.area_bucket_for(target.area_sqm)
    rows = list(
        session.scalars(
            select(MarketBaseline).where(
                MarketBaseline.materialization_run_id == run.id,
                MarketBaseline.observation_type == observation_type.value,
                MarketBaseline.window_days == window_days,
                MarketBaseline.area_bucket == area_bucket,
                MarketBaseline.layout == target.resolved_layout,
                or_(
                    and_(
                        MarketBaseline.level == BaselineLevel.COMMUNITY.value,
                        MarketBaseline.district == target.district,
                        MarketBaseline.submarket == target.submarket,
                        MarketBaseline.community == target.community,
                    ),
                    and_(
                        MarketBaseline.level == BaselineLevel.SUBMARKET.value,
                        MarketBaseline.district == target.district,
                        MarketBaseline.submarket == target.submarket,
                        MarketBaseline.community.is_(None),
                    ),
                    and_(
                        MarketBaseline.level == BaselineLevel.DISTRICT.value,
                        MarketBaseline.district == target.district,
                        MarketBaseline.submarket.is_(None),
                        MarketBaseline.community.is_(None),
                    ),
                    and_(
                        MarketBaseline.level == BaselineLevel.SHANGHAI.value,
                        MarketBaseline.district.is_(None),
                        MarketBaseline.submarket.is_(None),
                        MarketBaseline.community.is_(None),
                    ),
                ),
            )
        )
    )
    by_level = {BaselineLevel(row.level): row for row in rows}
    candidates = {
        level: BaselineCandidate(
            level=level,
            sample_count=row.observation_count,
            confidence=_baseline_confidence(row.confidence_level),
            quantiles={
                "price_p25": row.price_p25,
                "price_p50": row.price_p50,
                "price_p75": row.price_p75,
                "unit_price_p25": row.unit_price_p25,
                "unit_price_p50": row.unit_price_p50,
                "unit_price_p75": row.unit_price_p75,
            },
            baseline_version=row.baseline_version,
        )
        for level, row in by_level.items()
    }
    resolved = resolve_hierarchy(candidates, market_config.fallback)
    if resolved.level_used == "none":
        return None
    liquidity = _resolved_liquidity(by_level, resolved.level_used, resolved.local_weight)
    return BaselineEvidence(
        observation_type=observation_type.value,
        level_used=resolved.level_used,
        confidence=resolved.confidence.value,
        sample_count=resolved.sample_count,
        unit_price_p25=resolved.quantiles.get("unit_price_p25"),
        unit_price_p50=resolved.quantiles.get("unit_price_p50"),
        unit_price_p75=resolved.quantiles.get("unit_price_p75"),
        price_p50=resolved.quantiles.get("price_p50"),
        liquidity_score=liquidity,
        baseline_versions=resolved.baseline_versions,
        fallback_path=resolved.fallback_path,
        fallback_reason=resolved.fallback_reason,
        generated_at=max((row.generated_at for row in rows), default=None),
    )


def _choose_valuation_baseline(
    listing: BaselineEvidence | None,
    transaction: BaselineEvidence | None,
    config: ValuationConfig,
) -> BaselineEvidence | None:
    if (
        transaction is not None
        and transaction.sample_count >= config.comparables.strong_transaction_count
    ):
        return transaction
    return listing or transaction


def _resolved_liquidity(
    rows: dict[BaselineLevel, MarketBaseline],
    level_used: str,
    local_weight: Decimal | None,
) -> Decimal | None:
    if level_used == "community_submarket_shrunk":
        community = rows.get(BaselineLevel.COMMUNITY)
        submarket = rows.get(BaselineLevel.SUBMARKET)
        local = community.liquidity_score if community is not None else None
        parent = submarket.liquidity_score if submarket is not None else None
        if local is None:
            return parent
        if parent is None or local_weight is None:
            return local
        return local * local_weight + parent * (Decimal("1") - local_weight)
    try:
        row = rows.get(BaselineLevel(level_used))
    except ValueError:
        return None
    return row.liquidity_score if row is not None else None


def _target_from_models(
    observation: MarketObservation,
    listing: Listing,
    community: Community | None,
    data_mode: DataMode,
) -> TargetProperty:
    metadata = observation.observation_metadata
    assert observation.district is not None
    assert observation.submarket is not None
    assert observation.community is not None
    assert observation.area_sqm is not None
    assert observation.total_price is not None
    return TargetProperty(
        listing_id=listing.id,
        district=observation.district,
        submarket=observation.submarket,
        community=observation.community,
        area_sqm=observation.area_sqm,
        current_ask=observation.total_price,
        bedrooms=observation.bedrooms,
        layout=observation.layout,
        floor=_string_value(metadata, "floor", listing.floor),
        total_floors=_int_value(metadata, "total_floors", listing.total_floors),
        orientation=_string_value(metadata, "orientation", listing.orientation),
        year_built=_int_value(metadata, "year_built", listing.year_built),
        elevator=_bool_value(metadata, "elevator", listing.elevator),
        building_type=_string_value(metadata, "building_type", listing.building_type),
        metro_distance_m=community.metro_distance_m if community is not None else None,
        data_mode=data_mode.value,
    )


def _comparable_from_models(
    observation: MarketObservation, listing: Listing | None
) -> ComparableRecord:
    metadata = observation.observation_metadata
    assert observation.area_sqm is not None
    assert observation.total_price is not None
    assert observation.unit_price is not None
    return ComparableRecord(
        observation_id=observation.id,
        listing_id=observation.listing_id,
        observation_type=observation.observation_type,
        source=observation.source,
        source_record_id=observation.source_record_id,
        observed_at=observation.observed_at,
        source_confidence=observation.source_confidence,
        district=observation.district,
        submarket=observation.submarket,
        community=observation.community,
        area_sqm=observation.area_sqm,
        total_price=observation.total_price,
        unit_price=observation.unit_price,
        bedrooms=observation.bedrooms,
        layout=observation.layout,
        floor=_string_value(metadata, "floor", listing.floor if listing else None),
        total_floors=_int_value(
            metadata, "total_floors", listing.total_floors if listing else None
        ),
        orientation=_string_value(
            metadata, "orientation", listing.orientation if listing else None
        ),
        year_built=_int_value(metadata, "year_built", listing.year_built if listing else None),
        elevator=_bool_value(metadata, "elevator", listing.elevator if listing else None),
        building_type=_string_value(
            metadata, "building_type", listing.building_type if listing else None
        ),
        metro_distance_m=_int_value(metadata, "metro_distance_m", None),
        layout_quality=_string_value(metadata, "layout_quality", "unknown") or "unknown",
        road_noise_exposure=(
            _string_value(metadata, "road_noise_exposure", "unknown") or "unknown"
        ),
        metadata=metadata,
    )


def _load_price_history(
    session: Session,
    listing: Listing,
    data_mode: DataMode,
    as_of: datetime,
    current_ask: Decimal,
) -> PriceHistory:
    snapshots = session.execute(
        select(ListingSnapshot.snapshot_at, ListingSnapshot.total_price)
        .join(CrawlRun, CrawlRun.id == ListingSnapshot.crawl_run_id)
        .where(
            ListingSnapshot.listing_id == listing.id,
            CrawlRun.data_mode == data_mode.value,
            ListingSnapshot.snapshot_at <= as_of,
        )
    ).all()
    events = session.execute(
        select(ListingEvent.occurred_at, ListingEvent.event_type)
        .join(CrawlRun, CrawlRun.id == ListingEvent.crawl_run_id)
        .where(
            ListingEvent.listing_id == listing.id,
            CrawlRun.data_mode == data_mode.value,
            ListingEvent.occurred_at <= as_of,
        )
    ).all()
    price_cut_dates = [
        occurred_at
        for occurred_at, event_type in events
        if event_type == ListingEventType.PRICE_CUT.value
    ]
    relisting_dates = [
        occurred_at
        for occurred_at, event_type in events
        if event_type == ListingEventType.RELISTED.value
    ]
    return build_price_history(
        current_ask=current_ask,
        first_seen_at=listing.first_seen_at,
        snapshot_prices=[(snapshot_at, price) for snapshot_at, price in snapshots],
        price_cut_dates=price_cut_dates,
        relisting_dates=relisting_dates,
        as_of=as_of,
    )


def _baseline_confidence(value: str) -> BaselineConfidence:
    return BaselineConfidence(value)


def _string_value(metadata: dict[str, Any], key: str, fallback: str | None) -> str | None:
    value = metadata.get(key, fallback)
    return str(value) if value is not None else None


def _int_value(metadata: dict[str, Any], key: str, fallback: int | None) -> int | None:
    value = metadata.get(key, fallback)
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return fallback


def _bool_value(metadata: dict[str, Any], key: str, fallback: bool | None) -> bool | None:
    value = metadata.get(key, fallback)
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"true", "1", "yes"}:
            return True
        if normalized in {"false", "0", "no"}:
            return False
    return fallback

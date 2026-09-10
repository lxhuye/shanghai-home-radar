from __future__ import annotations

import uuid
from datetime import date
from enum import IntEnum
from typing import Annotated, Any, Literal, cast

from fastapi import APIRouter, Depends, HTTPException, Query
from home_radar_market.config import load_market_config
from home_radar_market.resolver import BaselineCandidate, resolve_hierarchy
from home_radar_models.collection import CrawlRun
from home_radar_models.community import Community
from home_radar_models.enums import (
    BaselineConfidence,
    BaselineLevel,
    DataMode,
    MarketObservationType,
)
from home_radar_models.market import (
    BaselineMaterializationRun,
    MarketBaseline,
    MarketObservation,
)
from home_radar_shared.config import get_settings
from sqlalchemy import ColumnElement, and_, case, func, or_, select
from sqlalchemy.orm import InstrumentedAttribute, Session

from home_radar_api.dependencies import get_db
from home_radar_api.schemas import (
    CommunityBaselineRead,
    LiquidityPage,
    LiquidityRead,
    MarketBaselinePage,
    MarketBaselineRead,
    MarketDataQualityRead,
)

AreaBucket = Literal["<40", "40-50", "50-65", "65-80", "80-100", "100+"]
Layout = Literal["1BR", "2BR", "3BR+"]


class WindowDays(IntEnum):
    DAYS_30 = 30
    DAYS_90 = 90
    DAYS_180 = 180
    DAYS_365 = 365


router = APIRouter(prefix="/api/v1/market", tags=["market"])


def _mode() -> DataMode:
    return DataMode(get_settings().market_data_mode)


def _notice(mode: DataMode) -> str:
    if mode is DataMode.LIVE:
        return "LIVE authorized-feed market evidence. Asking prices are not transactions."
    return (
        f"{mode.value.upper()} data only. "
        "It must not be used as live market evidence or fair value."
    )


def _label(observation_type: MarketObservationType) -> str:
    return {
        MarketObservationType.LISTING: "Listing Market Baseline",
        MarketObservationType.TRANSACTION: "Transaction Market Baseline",
        MarketObservationType.RENTAL: "Rental Market Baseline",
        MarketObservationType.OFFICIAL_INDEX: "Official Index Baseline",
        MarketObservationType.EXTERNAL_BASELINE: "External Market Baseline",
    }[observation_type]


def _latest_run(
    db: Session, mode: DataMode, as_of_date: date | None = None
) -> BaselineMaterializationRun | None:
    query = select(BaselineMaterializationRun).where(
        BaselineMaterializationRun.data_mode == mode.value,
        BaselineMaterializationRun.status == "succeeded",
    )
    if as_of_date is not None:
        query = query.where(BaselineMaterializationRun.as_of_date == as_of_date)
    return db.scalar(
        query.order_by(
            BaselineMaterializationRun.as_of_date.desc(),
            BaselineMaterializationRun.finished_at.desc(),
            BaselineMaterializationRun.id.desc(),
        ).limit(1)
    )


def _optional_filter(column: InstrumentedAttribute[Any], value: str | None) -> ColumnElement[bool]:
    if value is None:
        return column.is_(None)
    return column == value


def _resolved_layout(layout: Layout | None, bedrooms: int | None) -> Layout | None:
    if bedrooms is None:
        return layout
    derived = load_market_config().layout_for(bedrooms)
    assert derived is not None
    if layout is not None and layout != derived:
        raise HTTPException(status_code=422, detail="layout conflicts with bedrooms")
    return cast(Layout, derived)


def _baseline_page(
    db: Session,
    *,
    observation_type: MarketObservationType,
    window_days: WindowDays | None,
    level: BaselineLevel | None,
    district: str | None,
    submarket: str | None,
    community: str | None,
    area_bucket: AreaBucket | None,
    layout: Layout | None,
    as_of_date: date | None,
    limit: int,
    offset: int,
) -> MarketBaselinePage:
    mode = _mode()
    run = _latest_run(db, mode, as_of_date)
    if run is None:
        return MarketBaselinePage(
            available=False,
            data_mode=mode.value,
            data_notice=_notice(mode),
            baseline_label=_label(observation_type),
            materialization_run_id=None,
            items=[],
            total=0,
            limit=limit,
            offset=offset,
        )
    filters: list[ColumnElement[bool]] = [
        MarketBaseline.materialization_run_id == run.id,
        MarketBaseline.observation_type == observation_type.value,
    ]
    if window_days is not None:
        filters.append(MarketBaseline.window_days == window_days.value)
    if level is not None:
        filters.append(MarketBaseline.level == level.value)
    if district is not None:
        filters.append(MarketBaseline.district == district)
    if submarket is not None:
        filters.append(MarketBaseline.submarket == submarket)
    if community is not None:
        filters.append(MarketBaseline.community == community)
    if area_bucket is not None:
        filters.append(MarketBaseline.area_bucket == area_bucket)
    if layout is not None:
        filters.append(MarketBaseline.layout == layout)
    total = db.scalar(select(func.count()).select_from(MarketBaseline).where(*filters)) or 0
    items = db.scalars(
        select(MarketBaseline)
        .where(*filters)
        .order_by(
            MarketBaseline.level,
            MarketBaseline.district,
            MarketBaseline.submarket,
            MarketBaseline.community,
            MarketBaseline.area_bucket,
            MarketBaseline.layout,
        )
        .offset(offset)
        .limit(limit)
    ).all()
    return MarketBaselinePage(
        available=bool(items),
        data_mode=mode.value,
        data_notice=_notice(mode),
        baseline_label=_label(observation_type),
        materialization_run_id=run.id,
        items=[MarketBaselineRead.model_validate(item) for item in items],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get("/baselines", response_model=MarketBaselinePage)
def list_baselines(
    db: Annotated[Session, Depends(get_db)],
    observation_type: Annotated[MarketObservationType, Query()] = MarketObservationType.LISTING,
    window_days: Annotated[WindowDays | None, Query()] = None,
    level: Annotated[BaselineLevel | None, Query()] = None,
    district: Annotated[str | None, Query(max_length=80)] = None,
    submarket: Annotated[str | None, Query(max_length=120)] = None,
    community: Annotated[str | None, Query(max_length=160)] = None,
    area_bucket: Annotated[AreaBucket | None, Query()] = None,
    layout: Annotated[Layout | None, Query()] = None,
    bedrooms: Annotated[int | None, Query(ge=1, le=20)] = None,
    as_of_date: Annotated[date | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> MarketBaselinePage:
    return _baseline_page(
        db,
        observation_type=observation_type,
        window_days=window_days,
        level=level,
        district=district,
        submarket=submarket,
        community=community,
        area_bucket=area_bucket,
        layout=_resolved_layout(layout, bedrooms),
        as_of_date=as_of_date,
        limit=limit,
        offset=offset,
    )


@router.get("/baselines/{district}/{submarket}", response_model=MarketBaselinePage)
def get_submarket_baselines(
    district: str,
    submarket: str,
    db: Annotated[Session, Depends(get_db)],
    observation_type: Annotated[MarketObservationType, Query()] = MarketObservationType.LISTING,
    window_days: Annotated[WindowDays | None, Query()] = None,
    area_bucket: Annotated[AreaBucket | None, Query()] = None,
    layout: Annotated[Layout | None, Query()] = None,
    bedrooms: Annotated[int | None, Query(ge=1, le=20)] = None,
    as_of_date: Annotated[date | None, Query()] = None,
) -> MarketBaselinePage:
    return _baseline_page(
        db,
        observation_type=observation_type,
        window_days=window_days,
        level=BaselineLevel.SUBMARKET,
        district=district,
        submarket=submarket,
        community=None,
        area_bucket=area_bucket,
        layout=_resolved_layout(layout, bedrooms),
        as_of_date=as_of_date,
        limit=100,
        offset=0,
    )


@router.get("/communities/{community_id}/baseline", response_model=CommunityBaselineRead)
def get_community_baseline(
    community_id: uuid.UUID,
    db: Annotated[Session, Depends(get_db)],
    observation_type: Annotated[MarketObservationType, Query()] = MarketObservationType.LISTING,
    window_days: Annotated[WindowDays, Query()] = WindowDays.DAYS_90,
    area_bucket: Annotated[AreaBucket | None, Query()] = None,
    layout: Annotated[Layout | None, Query()] = None,
    bedrooms: Annotated[int | None, Query(ge=1, le=20)] = None,
    as_of_date: Annotated[date | None, Query()] = None,
) -> CommunityBaselineRead:
    community = db.get(Community, community_id)
    if community is None:
        raise HTTPException(status_code=404, detail="community not found")
    mode = _mode()
    run = _latest_run(db, mode, as_of_date)
    rows: list[MarketBaseline] = []
    if run is not None:
        rows = list(
            db.scalars(
                select(MarketBaseline).where(
                    MarketBaseline.materialization_run_id == run.id,
                    MarketBaseline.observation_type == observation_type.value,
                    MarketBaseline.window_days == window_days.value,
                    _optional_filter(MarketBaseline.area_bucket, area_bucket),
                    _optional_filter(MarketBaseline.layout, _resolved_layout(layout, bedrooms)),
                    or_(
                        and_(
                            MarketBaseline.level == BaselineLevel.COMMUNITY.value,
                            MarketBaseline.district == community.district,
                            MarketBaseline.submarket == community.submarket,
                            MarketBaseline.community == community.community,
                        ),
                        and_(
                            MarketBaseline.level == BaselineLevel.SUBMARKET.value,
                            MarketBaseline.district == community.district,
                            MarketBaseline.submarket == community.submarket,
                            MarketBaseline.community.is_(None),
                        ),
                        and_(
                            MarketBaseline.level == BaselineLevel.DISTRICT.value,
                            MarketBaseline.district == community.district,
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
    candidates = {
        BaselineLevel(row.level): BaselineCandidate(
            level=BaselineLevel(row.level),
            sample_count=row.observation_count,
            confidence=BaselineConfidence(row.confidence_level),
            quantiles={
                "price_p10": row.price_p10,
                "price_p25": row.price_p25,
                "price_p50": row.price_p50,
                "price_p75": row.price_p75,
                "price_p90": row.price_p90,
                "unit_price_p10": row.unit_price_p10,
                "unit_price_p25": row.unit_price_p25,
                "unit_price_p50": row.unit_price_p50,
                "unit_price_p75": row.unit_price_p75,
                "unit_price_p90": row.unit_price_p90,
            },
            baseline_version=row.baseline_version,
        )
        for row in rows
    }
    resolved = resolve_hierarchy(candidates, load_market_config().fallback)
    return CommunityBaselineRead(
        available=resolved.level_used != "none",
        data_mode=mode.value,
        data_notice=_notice(mode),
        baseline_label=_label(observation_type),
        community_id=community_id,
        observation_type=observation_type.value,
        window_days=window_days.value,
        level_used=resolved.level_used,
        confidence=resolved.confidence.value,
        sample_count=resolved.sample_count,
        quantiles=resolved.quantiles,
        baseline_versions=list(resolved.baseline_versions),
        fallback_path=list(resolved.fallback_path),
        fallback_reason=resolved.fallback_reason,
        local_weight=resolved.local_weight,
    )


@router.get("/liquidity", response_model=LiquidityPage)
def get_liquidity(
    db: Annotated[Session, Depends(get_db)],
    window_days: Annotated[WindowDays, Query()] = WindowDays.DAYS_90,
    district: Annotated[str | None, Query(max_length=80)] = None,
    submarket: Annotated[str | None, Query(max_length=120)] = None,
    community: Annotated[str | None, Query(max_length=160)] = None,
    area_bucket: Annotated[AreaBucket | None, Query()] = None,
    layout: Annotated[Layout | None, Query()] = None,
    bedrooms: Annotated[int | None, Query(ge=1, le=20)] = None,
) -> LiquidityPage:
    mode = _mode()
    run = _latest_run(db, mode)
    if run is None:
        return LiquidityPage(
            available=False,
            data_mode=mode.value,
            data_notice=_notice(mode),
            materialization_run_id=None,
            items=[],
        )
    filters: list[ColumnElement[bool]] = [
        MarketBaseline.materialization_run_id == run.id,
        MarketBaseline.observation_type == MarketObservationType.LISTING.value,
        MarketBaseline.window_days == window_days.value,
    ]
    if area_bucket is not None:
        filters.append(MarketBaseline.area_bucket == area_bucket)
    resolved_layout = _resolved_layout(layout, bedrooms)
    if resolved_layout is not None:
        filters.append(MarketBaseline.layout == resolved_layout)
    for column, value in (
        (MarketBaseline.district, district),
        (MarketBaseline.submarket, submarket),
        (MarketBaseline.community, community),
    ):
        if value is not None:
            filters.append(column == value)
    rows = db.scalars(select(MarketBaseline).where(*filters).limit(500)).all()
    return LiquidityPage(
        available=bool(rows),
        data_mode=mode.value,
        data_notice=_notice(mode),
        materialization_run_id=run.id,
        items=[LiquidityRead.model_validate(row) for row in rows],
    )


@router.get("/data-quality", response_model=MarketDataQualityRead)
def get_market_data_quality(
    db: Annotated[Session, Depends(get_db)],
) -> MarketDataQualityRead:
    mode = _mode()
    run = _latest_run(db, mode)
    run_counts = db.execute(
        select(CrawlRun.status, CrawlRun.completeness, func.count())
        .where(CrawlRun.data_mode == mode.value)
        .group_by(CrawlRun.status, CrawlRun.completeness)
        .order_by(CrawlRun.status, CrawlRun.completeness)
    ).all()
    observation_rows = db.execute(
        select(MarketObservation.observation_type, func.count())
        .where(MarketObservation.data_mode == mode.value)
        .group_by(MarketObservation.observation_type)
    ).all()
    observation_counts: dict[str, int] = {
        observation_type: int(count) for observation_type, count in observation_rows
    }
    coverage_rows = db.execute(
        select(
            MarketObservation.district,
            MarketObservation.submarket,
            func.count(),
            func.sum(case((MarketObservation.coverage_complete.is_(True), 1), else_=0)),
        )
        .where(MarketObservation.data_mode == mode.value)
        .group_by(MarketObservation.district, MarketObservation.submarket)
        .order_by(MarketObservation.district, MarketObservation.submarket)
    ).all()
    latest_observation = db.scalar(
        select(func.max(MarketObservation.observed_at)).where(
            MarketObservation.data_mode == mode.value
        )
    )
    gaps: list[dict[str, str]] = []
    for target in load_market_config().target_areas:
        scopes = target.submarkets or ("",)
        for submarket in scopes:
            filters = [
                MarketObservation.data_mode == mode.value,
                MarketObservation.district == target.district,
            ]
            if submarket:
                filters.append(MarketObservation.submarket == submarket)
            count = (
                db.scalar(select(func.count()).select_from(MarketObservation).where(*filters)) or 0
            )
            if count == 0:
                gaps.append({"district": target.district, "submarket": submarket})
    low_confidence: list[dict[str, object]] = []
    if run is not None:
        low_rows = db.scalars(
            select(MarketBaseline).where(
                MarketBaseline.materialization_run_id == run.id,
                MarketBaseline.level == BaselineLevel.COMMUNITY.value,
                MarketBaseline.confidence_level.in_(
                    [BaselineConfidence.LOW.value, BaselineConfidence.INSUFFICIENT.value]
                ),
            )
        ).all()
        low_confidence = [
            {
                "district": row.district,
                "submarket": row.submarket,
                "community": row.community,
                "observation_type": row.observation_type,
                "window_days": row.window_days,
                "confidence": row.confidence_level,
                "sample_count": row.observation_count,
                "baseline_version": row.baseline_version,
            }
            for row in low_rows
        ]
    return MarketDataQualityRead(
        data_mode=mode.value,
        data_notice=_notice(mode),
        latest_materialization_run_id=run.id if run else None,
        crawl_runs=[
            {"status": status, "completeness": completeness, "count": count}
            for status, completeness, count in run_counts
        ],
        observation_counts=observation_counts,
        area_coverage=[
            {
                "district": district,
                "submarket": submarket,
                "observation_count": int(count),
                "complete_coverage_count": int(complete_count or 0),
            }
            for district, submarket, count, complete_count in coverage_rows
        ],
        latest_observation_at=latest_observation,
        target_area_gaps=gaps,
        low_confidence_communities=low_confidence,
    )

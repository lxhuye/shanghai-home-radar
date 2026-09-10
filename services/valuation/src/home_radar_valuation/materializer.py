from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from home_radar_market.config import MarketBaselineConfig
from home_radar_models.enums import DataMode, ListingStatus, MarketObservationType
from home_radar_models.listing import Listing
from home_radar_models.market import MarketObservation
from sqlalchemy import select
from sqlalchemy.orm import Session

from home_radar_valuation.aggregation import InsufficientValuationEvidenceError
from home_radar_valuation.cache import evaluate_cached_listing
from home_radar_valuation.config import ValuationConfig


@dataclass(frozen=True)
class BatchValuationResult:
    evaluated: int
    cache_hits: int
    failed: int
    failures: tuple[str, ...]
    skipped_insufficient_data: int = 0
    skips: tuple[str, ...] = ()


def materialize_active_listings(
    session: Session,
    data_mode: DataMode,
    as_of: datetime,
    market_config: MarketBaselineConfig,
    valuation_config: ValuationConfig,
    *,
    limit: int | None = None,
) -> BatchValuationResult:
    statement = (
        select(Listing.id)
        .join(MarketObservation, MarketObservation.listing_id == Listing.id)
        .where(
            Listing.status == ListingStatus.ACTIVE.value,
            MarketObservation.data_mode == data_mode.value,
            MarketObservation.observation_type == MarketObservationType.LISTING.value,
            MarketObservation.observed_at <= as_of,
        )
        .distinct()
        .order_by(Listing.id)
    )
    if limit is not None:
        statement = statement.limit(limit)
    listing_ids = list(session.scalars(statement))
    evaluated = 0
    cache_hits = 0
    failures: list[str] = []
    skips: list[str] = []
    for listing_id in listing_ids:
        try:
            _, cache_hit = evaluate_cached_listing(
                session,
                listing_id,
                data_mode,
                as_of,
                market_config,
                valuation_config,
            )
            session.commit()
            evaluated += 1
            cache_hits += int(cache_hit)
        except InsufficientValuationEvidenceError as exc:
            session.rollback()
            skips.append(f"{listing_id}: {type(exc).__name__}: {exc}")
        except Exception as exc:  # batch isolation; failure text is returned to operations
            session.rollback()
            failures.append(f"{listing_id}: {type(exc).__name__}: {exc}")
    return BatchValuationResult(
        evaluated=evaluated,
        cache_hits=cache_hits,
        failed=len(failures),
        failures=tuple(failures),
        skipped_insufficient_data=len(skips),
        skips=tuple(skips),
    )

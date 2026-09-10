from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime

from home_radar_forecasting.config import FutureConfig
from home_radar_forecasting.repository import FutureInputsUnavailableError
from home_radar_market.config import MarketBaselineConfig
from home_radar_models.enums import DataMode, ListingStatus, MarketObservationType
from home_radar_models.listing import Listing
from home_radar_models.market import MarketObservation
from home_radar_valuation.aggregation import InsufficientValuationEvidenceError
from home_radar_valuation.config import ValuationConfig
from sqlalchemy import select
from sqlalchemy.orm import Session

from home_radar_decision.cache import evaluate_cached_decision
from home_radar_decision.config import DecisionConfig
from home_radar_decision.repository import DecisionInputsUnavailableError


@dataclass(frozen=True)
class BatchDecisionResult:
    evaluated: int
    cache_hits: int
    failed: int
    failures: tuple[str, ...]
    skipped_insufficient_data: int = 0
    skips: tuple[str, ...] = ()


def materialize_decisions(
    session: Session,
    data_mode: DataMode,
    as_of: datetime,
    market_config: MarketBaselineConfig,
    valuation_config: ValuationConfig,
    future_config: FutureConfig,
    decision_config: DecisionConfig,
    *,
    limit: int | None = None,
) -> BatchDecisionResult:
    listing_ids = _active_listing_ids(session, data_mode, as_of, limit)
    evaluated = 0
    cache_hits = 0
    failures: list[str] = []
    skips: list[str] = []
    for listing_id in listing_ids:
        try:
            _, cache_hit = evaluate_cached_decision(
                session,
                listing_id,
                data_mode,
                as_of,
                market_config,
                valuation_config,
                future_config,
                decision_config,
            )
            session.commit()
            evaluated += 1
            cache_hits += int(cache_hit)
        except (
            InsufficientValuationEvidenceError,
            FutureInputsUnavailableError,
            DecisionInputsUnavailableError,
        ) as exc:
            session.rollback()
            skips.append(f"{listing_id}: {type(exc).__name__}: {exc}")
        except Exception as exc:  # batch isolation is intentional
            session.rollback()
            failures.append(f"{listing_id}: {type(exc).__name__}: {exc}")
    return BatchDecisionResult(
        evaluated=evaluated,
        cache_hits=cache_hits,
        failed=len(failures),
        failures=tuple(failures),
        skipped_insufficient_data=len(skips),
        skips=tuple(skips),
    )


def _active_listing_ids(
    session: Session,
    data_mode: DataMode,
    as_of: datetime,
    limit: int | None,
) -> list[uuid.UUID]:
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
    return list(session.scalars(statement))

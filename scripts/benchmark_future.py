from __future__ import annotations

import argparse
import json
import statistics
import uuid
from datetime import datetime, timedelta
from time import perf_counter

from home_radar_forecasting.cache import evaluate_cached_future
from home_radar_forecasting.config import FutureConfig, load_future_config
from home_radar_forecasting.engine import DeterministicFutureEngine
from home_radar_forecasting.repository import load_future_inputs
from home_radar_market.config import MarketBaselineConfig, load_market_config
from home_radar_models.enums import DataMode
from home_radar_models.future import FutureAssessment, FutureFactorObservation
from home_radar_shared.config import get_settings
from home_radar_shared.database import get_session_factory
from home_radar_valuation.config import ValuationConfig, load_valuation_config
from sqlalchemy import func, select
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session


def main() -> None:
    parser = argparse.ArgumentParser(description="Benchmark deterministic P5 future engine")
    parser.add_argument("--iterations", type=int, default=1000)
    args = parser.parse_args()
    settings = get_settings()
    database_name = make_url(settings.database_url).database or ""
    if not database_name.endswith("_test"):
        raise RuntimeError("future benchmark refuses a database without a _test suffix")
    market_config = load_market_config(settings.market_baseline_config_path)
    valuation_config = load_valuation_config(settings.valuation_config_path)
    base_future_config = load_future_config(settings.forecasting_config_path)
    future_config = base_future_config.model_copy(
        update={"future_model_version": f"p5-benchmark-{uuid.uuid4().hex[:8]}"}
    )
    with get_session_factory()() as session:
        listing_id = session.scalar(
            select(FutureFactorObservation.listing_id)
            .where(
                FutureFactorObservation.data_mode == DataMode.SAMPLE.value,
                FutureFactorObservation.listing_id.is_not(None),
            )
            .limit(1)
        )
        latest = session.scalar(
            select(func.max(FutureFactorObservation.source_timestamp)).where(
                FutureFactorObservation.data_mode == DataMode.SAMPLE.value
            )
        )
        if listing_id is None or latest is None:
            raise RuntimeError("seed P5 integration evidence before benchmarking")
        as_of = latest + timedelta(hours=2)
        cold_started = perf_counter()
        cached, cold_hit = evaluate_cached_future(
            session,
            listing_id,
            DataMode.SAMPLE,
            as_of,
            market_config,
            valuation_config,
            future_config,
        )
        session.commit()
        cold_ms = (perf_counter() - cold_started) * 1000
        inputs = load_future_inputs(session, listing_id, DataMode.SAMPLE, as_of)
        engine = DeterministicFutureEngine(future_config)
        started = perf_counter()
        for _ in range(args.iterations):
            engine.evaluate(inputs, as_of)
        calculation_ms = (perf_counter() - started) * 1000
        hit_times = _cache_hit_times(
            session,
            listing_id,
            as_of,
            market_config,
            valuation_config,
            future_config,
        )
        direct_times = _direct_read_times(session, cached.id)
    print(
        json.dumps(
            {
                "iterations": args.iterations,
                "total_calculation_ms": round(calculation_ms, 3),
                "mean_calculation_ms": round(calculation_ms / args.iterations, 3),
                "cold_calculation_and_write_ms": round(cold_ms, 3),
                "cold_was_cache_hit": cold_hit,
                "median_validated_cache_hit_ms": round(statistics.median(hit_times), 3),
                "median_direct_cached_read_ms": round(statistics.median(direct_times), 3),
            },
            sort_keys=True,
        )
    )


def _cache_hit_times(
    session: Session,
    listing_id: uuid.UUID,
    as_of: datetime,
    market_config: MarketBaselineConfig,
    valuation_config: ValuationConfig,
    future_config: FutureConfig,
) -> list[float]:
    times: list[float] = []
    for _ in range(20):
        started = perf_counter()
        _, cache_hit = evaluate_cached_future(
            session,
            listing_id,
            DataMode.SAMPLE,
            as_of,
            market_config,
            valuation_config,
            future_config,
        )
        assert cache_hit
        times.append((perf_counter() - started) * 1000)
    return times


def _direct_read_times(session: Session, assessment_id: uuid.UUID) -> list[float]:
    times: list[float] = []
    for _ in range(100):
        started = perf_counter()
        session.scalar(select(FutureAssessment).where(FutureAssessment.id == assessment_id))
        times.append((perf_counter() - started) * 1000)
    return times


if __name__ == "__main__":
    main()

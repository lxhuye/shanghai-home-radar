from __future__ import annotations

import argparse
import json
import statistics
import uuid
from datetime import timedelta
from time import perf_counter

from home_radar_market.config import load_market_config
from home_radar_models.enums import DataMode, MarketObservationType
from home_radar_models.market import BaselineMaterializationRun, MarketObservation
from home_radar_models.valuation import ValuationResult
from home_radar_shared.config import get_settings
from home_radar_shared.database import get_session_factory
from home_radar_valuation.cache import evaluate_cached_listing
from home_radar_valuation.config import load_valuation_config
from home_radar_valuation.engine import DeterministicValuationEngine
from home_radar_valuation.repository import load_listing_inputs
from sqlalchemy import select
from sqlalchemy.engine import make_url


def main() -> None:
    parser = argparse.ArgumentParser(description="Benchmark deterministic P4 valuation")
    parser.add_argument("--iterations", type=int, default=1000)
    args = parser.parse_args()
    settings = get_settings()
    database_name = make_url(settings.database_url).database or ""
    if not database_name.endswith("_test"):
        raise RuntimeError("valuation benchmark refuses a database without a _test suffix")

    market_config = load_market_config(settings.market_baseline_config_path)
    base_config = load_valuation_config(settings.valuation_config_path)
    benchmark_config = base_config.model_copy(
        update={"valuation_model_version": f"p4-benchmark-{uuid.uuid4().hex[:8]}"}
    )
    with get_session_factory()() as session:
        listing_id = session.scalar(
            select(MarketObservation.listing_id)
            .where(
                MarketObservation.data_mode == DataMode.SAMPLE.value,
                MarketObservation.observation_type == MarketObservationType.LISTING.value,
                MarketObservation.listing_id.is_not(None),
            )
            .limit(1)
        )
        run = session.scalar(
            select(BaselineMaterializationRun)
            .where(BaselineMaterializationRun.data_mode == DataMode.SAMPLE.value)
            .order_by(BaselineMaterializationRun.input_cutoff_at.desc())
            .limit(1)
        )
        if listing_id is None or run is None:
            raise RuntimeError("seed sample listings and P3 baselines before benchmarking")
        as_of = run.input_cutoff_at + timedelta(hours=12)
        inputs = load_listing_inputs(
            session,
            listing_id,
            DataMode.SAMPLE,
            as_of,
            market_config,
            benchmark_config,
        )
        engine = DeterministicValuationEngine(benchmark_config)

        started = perf_counter()
        for _ in range(args.iterations):
            engine.evaluate(inputs, as_of)
        calculation_ms = (perf_counter() - started) * 1000

        cold_started = perf_counter()
        cached, cold_hit = evaluate_cached_listing(
            session,
            listing_id,
            DataMode.SAMPLE,
            as_of,
            market_config,
            benchmark_config,
        )
        session.commit()
        cold_ms = (perf_counter() - cold_started) * 1000

        hit_times: list[float] = []
        for _ in range(20):
            hit_started = perf_counter()
            _, cache_hit = evaluate_cached_listing(
                session,
                listing_id,
                DataMode.SAMPLE,
                as_of,
                market_config,
                benchmark_config,
            )
            assert cache_hit
            hit_times.append((perf_counter() - hit_started) * 1000)

        direct_times: list[float] = []
        for _ in range(100):
            read_started = perf_counter()
            session.scalar(select(ValuationResult).where(ValuationResult.id == cached.id))
            direct_times.append((perf_counter() - read_started) * 1000)

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


if __name__ == "__main__":
    main()

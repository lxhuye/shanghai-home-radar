from __future__ import annotations

import json
import os
import uuid
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from time import monotonic

from home_radar_market.config import load_market_config
from home_radar_market.materializer import materialize_baselines
from home_radar_models.enums import DataMode, MarketObservationType
from home_radar_models.market import MarketBaseline, MarketObservation
from sqlalchemy import create_engine, func, insert, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session


def main() -> None:
    database_url = os.environ["SHR_TEST_DATABASE_URL"]
    if not (make_url(database_url).database or "").endswith("_test"):
        raise RuntimeError("benchmark refuses to use a database without a _test suffix")
    engine = create_engine(database_url, pool_pre_ping=True)
    as_of_date = date(2026, 9, 1)
    created_at = datetime.now(UTC)
    rows: list[dict[str, object]] = []
    for index in range(10_000):
        observed_at = datetime(2026, 8, 20, tzinfo=UTC) + timedelta(minutes=index % 10_000)
        area = Decimal(55 + index % 10)
        price = Decimal(2_800_000 + index % 200 * 10_000)
        rows.append(
            {
                "id": uuid.uuid4(),
                "observation_type": MarketObservationType.LISTING.value,
                "data_mode": DataMode.SAMPLE.value,
                "source": f"benchmark-feed-{index % 4}",
                "source_record_id": f"benchmark-{index}",
                "district": "普陀",
                "submarket": "真如",
                "community": "性能测试社区",
                "area_sqm": area,
                "area_bucket": "50-65",
                "bedrooms": 2,
                "layout": "2BR",
                "total_price": price,
                "unit_price": price / area,
                "observed_at": observed_at,
                "source_confidence": Decimal("1"),
                "coverage_complete": True,
                "metadata": {
                    "listing_status": "active",
                    "first_seen_at": (observed_at - timedelta(days=15)).isoformat(),
                    "event_types": ["new"],
                    "benchmark": True,
                },
                "created_at": created_at,
            }
        )
    try:
        with Session(engine) as session:
            session.execute(
                text(
                    "TRUNCATE TABLE market_baseline, baseline_materialization_run, "
                    "market_observation CASCADE"
                )
            )
            session.execute(insert(MarketObservation), rows)
            session.commit()

            started = monotonic()
            result = materialize_baselines(
                session,
                as_of_date=as_of_date,
                data_mode=DataMode.SAMPLE,
                config=load_market_config(),
            )
            materialization_seconds = monotonic() - started

            query_started = monotonic()
            items = session.scalars(
                select(MarketBaseline)
                .where(
                    MarketBaseline.materialization_run_id == result.run_id,
                    MarketBaseline.observation_type == MarketObservationType.LISTING.value,
                    MarketBaseline.window_days == 90,
                )
                .limit(100)
            ).all()
            query_ms = (monotonic() - query_started) * 1000
            baseline_rows = session.scalar(select(func.count()).select_from(MarketBaseline)) or 0
            print(
                json.dumps(
                    {
                        "observations": len(rows),
                        "baseline_rows": baseline_rows,
                        "materialization_seconds": round(materialization_seconds, 3),
                        "query_rows": len(items),
                        "query_ms": round(query_ms, 3),
                        "input_signature": result.input_signature,
                    },
                    sort_keys=True,
                )
            )
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()

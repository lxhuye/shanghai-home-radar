from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from home_radar_models.community import Community
from home_radar_models.enums import DataMode, MarketObservationType
from home_radar_shared.config import get_settings
from home_radar_shared.database import get_session_factory
from sqlalchemy import select

from home_radar_market.config import load_market_config
from home_radar_market.materializer import materialize_baselines
from home_radar_market.observation_service import ObservationInput, ingest_observation


def main() -> None:
    settings = get_settings()
    mode = DataMode(settings.market_data_mode)
    if mode is not DataMode.DEMO:
        raise RuntimeError("demo seed requires SHR_MARKET_DATA_MODE=demo")
    config = load_market_config(settings.market_baseline_config_path)
    today = datetime.now(UTC).replace(hour=4, minute=0, second=0, microsecond=0)
    with get_session_factory()() as session:
        community = session.scalar(
            select(Community).where(
                Community.district == "普陀",
                Community.submarket == "真如",
                Community.community == "真如演示社区",
            )
        )
        if community is None:
            session.add(
                Community(
                    district="普陀",
                    submarket="真如",
                    community="真如演示社区",
                    active_listing_count=0,
                )
            )
        for index in range(12):
            area = Decimal(52 + index)
            initial = Decimal(3_600_000 + index * 75_000)
            current = initial - (Decimal(80_000) if index % 3 == 0 else Decimal())
            source_record_id = f"demo-listing-{index:02d}"
            first_seen = today - timedelta(days=60 - index)
            for observed_at, price, event_types in (
                (first_seen, initial, ["new"]),
                (
                    today - timedelta(days=index % 9 + 1),
                    current,
                    ["price_cut"] if current < initial else [],
                ),
            ):
                ingest_observation(
                    session,
                    ObservationInput(
                        observation_type=MarketObservationType.LISTING,
                        data_mode=DataMode.DEMO,
                        source="p3_demo_fixture",
                        source_record_id=source_record_id,
                        observed_at=observed_at,
                        district="普陀",
                        submarket="真如",
                        community="真如演示社区",
                        area_sqm=area,
                        bedrooms=2,
                        total_price=price,
                        unit_price=price / area,
                        coverage_complete=True,
                        metadata={
                            "listing_status": "active",
                            "first_seen_at": first_seen.isoformat(),
                            "event_types": event_types,
                            "fixture": True,
                        },
                    ),
                    config,
                )
        for index in range(6):
            area = Decimal(54 + index)
            monthly_rent = Decimal(6_000 + index * 250)
            ingest_observation(
                session,
                ObservationInput(
                    observation_type=MarketObservationType.RENTAL,
                    data_mode=DataMode.DEMO,
                    source="p3_demo_fixture",
                    source_record_id=f"demo-rental-{index:02d}",
                    observed_at=today - timedelta(days=index + 1),
                    district="普陀",
                    submarket="真如",
                    community="真如演示社区",
                    area_sqm=area,
                    bedrooms=2,
                    monthly_rent=monthly_rent,
                    rent_per_sqm=monthly_rent / area,
                    coverage_complete=True,
                    metadata={"fixture": True},
                ),
                config,
            )
        session.commit()
        result = materialize_baselines(
            session,
            as_of_date=today.date() - timedelta(days=1),
            data_mode=DataMode.DEMO,
            config=config,
        )
    print(f"demo observations ready; baseline rows={result.baseline_count}")

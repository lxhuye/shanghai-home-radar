from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest
from home_radar_market.config import load_market_config
from home_radar_market.materializer import (
    MaterializationInProgressError,
    materialization_lock_key,
    materialize_baselines,
)
from home_radar_market.observation_service import ObservationInput, ingest_observation
from home_radar_models.collection import CrawlRun
from home_radar_models.community import Community
from home_radar_models.enums import DataMode, MarketObservationType
from home_radar_models.market import BaselineMaterializationRun, MarketBaseline, MarketObservation
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

pytestmark = pytest.mark.integration


def _seed_observation(
    session: Session,
    *,
    record_id: str,
    observed_at: datetime,
    observation_type: MarketObservationType = MarketObservationType.LISTING,
    price: Decimal = Decimal("3200000"),
) -> None:
    config = load_market_config()
    ingest_observation(
        session,
        ObservationInput(
            observation_type=observation_type,
            data_mode=DataMode.SAMPLE,
            source="integration-feed",
            source_record_id=record_id,
            observed_at=observed_at,
            district="普陀",
            submarket="真如",
            community="集成测试社区",
            area_sqm=Decimal("60"),
            bedrooms=2,
            total_price=price,
            unit_price=price / Decimal("60"),
            coverage_complete=True,
            metadata={
                "listing_status": "active",
                "first_seen_at": (observed_at - timedelta(days=10)).isoformat(),
                "event_types": ["new"],
            },
        ),
        config,
    )


def test_materialization_is_idempotent_type_isolated_and_late_data_is_versioned(
    db_session: Session,
) -> None:
    as_of = date(2026, 9, 1)
    for index in range(4):
        _seed_observation(
            db_session,
            record_id=f"listing-{index}",
            observed_at=datetime(2026, 8, 20 + index, 8, tzinfo=UTC),
            price=Decimal(3_000_000 + index * 100_000),
        )
    _seed_observation(
        db_session,
        record_id="transaction-1",
        observed_at=datetime(2026, 8, 25, 8, tzinfo=UTC),
        observation_type=MarketObservationType.TRANSACTION,
        price=Decimal("2800000"),
    )
    db_session.commit()

    config = load_market_config()
    first = materialize_baselines(
        db_session, as_of_date=as_of, data_mode=DataMode.SAMPLE, config=config
    )
    replay = materialize_baselines(
        db_session, as_of_date=as_of, data_mode=DataMode.SAMPLE, config=config
    )
    assert replay.idempotent_replay is True
    assert replay.run_id == first.run_id
    assert db_session.scalar(select(func.count()).select_from(BaselineMaterializationRun)) == 1

    listing_row = db_session.scalar(
        select(MarketBaseline).where(
            MarketBaseline.materialization_run_id == first.run_id,
            MarketBaseline.observation_type == MarketObservationType.LISTING.value,
            MarketBaseline.window_days == 30,
            MarketBaseline.level == "shanghai",
            MarketBaseline.area_bucket.is_(None),
            MarketBaseline.layout.is_(None),
        )
    )
    transaction_row = db_session.scalar(
        select(MarketBaseline).where(
            MarketBaseline.materialization_run_id == first.run_id,
            MarketBaseline.observation_type == MarketObservationType.TRANSACTION.value,
            MarketBaseline.window_days == 30,
            MarketBaseline.level == "shanghai",
            MarketBaseline.area_bucket.is_(None),
            MarketBaseline.layout.is_(None),
        )
    )
    assert listing_row is not None and listing_row.observation_count == 4
    assert transaction_row is not None and transaction_row.observation_count == 1
    assert listing_row.price_p50 != transaction_row.price_p50

    _seed_observation(
        db_session,
        record_id="late-listing",
        observed_at=datetime(2026, 8, 19, 8, tzinfo=UTC),
        price=Decimal("3500000"),
    )
    db_session.commit()
    revised = materialize_baselines(
        db_session, as_of_date=as_of, data_mode=DataMode.SAMPLE, config=config
    )
    assert revised.run_id != first.run_id
    assert revised.input_signature != first.input_signature
    assert db_session.scalar(select(func.count()).select_from(BaselineMaterializationRun)) == 2
    assert (
        db_session.scalar(
            select(func.count())
            .select_from(MarketBaseline)
            .where(MarketBaseline.materialization_run_id == first.run_id)
        )
        == first.baseline_count
    )


def test_concurrent_materialization_for_same_date_and_mode_is_rejected(
    db_session: Session,
) -> None:
    as_of = date(2026, 9, 1)
    lock_key = materialization_lock_key(as_of, DataMode.SAMPLE)
    engine = db_session.get_bind()
    with engine.connect() as owner:
        owner.execute(text("SELECT pg_advisory_lock(:lock_key)"), {"lock_key": lock_key})
        try:
            with pytest.raises(MaterializationInProgressError):
                materialize_baselines(
                    db_session,
                    as_of_date=as_of,
                    data_mode=DataMode.SAMPLE,
                    config=load_market_config(),
                )
        finally:
            owner.execute(text("SELECT pg_advisory_unlock(:lock_key)"), {"lock_key": lock_key})


def test_database_rejects_invalid_mode_and_type_specific_missing_measure(
    db_session: Session,
) -> None:
    db_session.add(
        CrawlRun(
            source="invalid-mode-test",
            data_mode="production",
            scope_key="test",
            status="succeeded",
            completeness="complete",
            started_at=datetime.now(UTC),
        )
    )
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()

    db_session.add(
        MarketObservation(
            observation_type="rental",
            data_mode="sample",
            source="invalid-rental-test",
            source_record_id="missing-rent",
            observed_at=datetime.now(UTC),
            coverage_complete=True,
            observation_metadata={},
        )
    )
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()


def test_market_api_reads_only_materialized_sample_mode(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    from fastapi.testclient import TestClient
    from home_radar_api.dependencies import get_db
    from home_radar_api.main import app
    from home_radar_shared.config import get_settings

    observed_at = datetime(2026, 8, 25, 8, tzinfo=UTC)
    for index in range(8):
        _seed_observation(
            db_session,
            record_id=f"api-listing-{index}",
            observed_at=observed_at + timedelta(hours=index),
            price=Decimal(3_000_000 + index * 50_000),
        )
    community = Community(
        district="普陀", submarket="真如", community="集成测试社区", active_listing_count=0
    )
    db_session.add(community)
    db_session.commit()
    materialize_baselines(
        db_session,
        as_of_date=date(2026, 9, 1),
        data_mode=DataMode.SAMPLE,
        config=load_market_config(),
    )

    monkeypatch.setenv("SHR_MARKET_DATA_MODE", "sample")
    get_settings.cache_clear()

    def override_db() -> Iterator[Session]:
        yield db_session

    app.dependency_overrides[get_db] = override_db
    try:
        client = TestClient(app)
        response = client.get(
            "/api/v1/market/baselines",
            params={"window_days": 30, "as_of_date": "2026-09-01"},
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["available"] is True
        assert body["data_mode"] == "sample"
        assert body["baseline_label"] == "Listing Market Baseline"
        assert "must not be used" in body["data_notice"].lower()
        assert body["items"][0]["baseline_version"]
        assert body["items"][0]["provenance"]["asking_price_not_transaction"] is True

        all_windows = client.get("/api/v1/market/baselines")
        assert all_windows.status_code == 200
        assert {item["window_days"] for item in all_windows.json()["items"]} == {
            30,
            90,
            180,
            365,
        }
        by_bedrooms = client.get(
            "/api/v1/market/baselines",
            params={"window_days": 30, "bedrooms": 2, "area_bucket": "50-65"},
        )
        assert by_bedrooms.status_code == 200
        assert {item["layout"] for item in by_bedrooms.json()["items"]} == {"2BR"}
        conflict = client.get(
            "/api/v1/market/baselines",
            params={"window_days": 30, "bedrooms": 2, "layout": "1BR"},
        )
        assert conflict.status_code == 422

        transaction = client.get(
            "/api/v1/market/baselines",
            params={
                "observation_type": "transaction",
                "window_days": 30,
                "as_of_date": "2026-09-01",
            },
        )
        assert transaction.status_code == 200
        assert transaction.json()["available"] is False
        assert transaction.json()["items"] == []

        community_response = client.get(
            f"/api/v1/market/communities/{community.id}/baseline",
            params={"window_days": 30, "as_of_date": "2026-09-01"},
        )
        assert community_response.status_code == 200
        assert community_response.json()["level_used"] == "community"

        quality = client.get("/api/v1/market/data-quality")
        assert quality.status_code == 200
        assert quality.json()["observation_counts"]["listing"] == 8
        assert quality.json()["area_coverage"][0]["district"] == "普陀"
    finally:
        app.dependency_overrides.clear()
        get_settings.cache_clear()

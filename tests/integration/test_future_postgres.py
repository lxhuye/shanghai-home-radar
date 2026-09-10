from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from geoalchemy2.elements import WKTElement
from home_radar_api.dependencies import get_db
from home_radar_api.main import app
from home_radar_decision.blind_validation import (
    create_blind_batch,
    label_blind_item,
    reveal_blind_batch,
    validation_items,
)
from home_radar_decision.cache import evaluate_cached_decision
from home_radar_decision.config import load_decision_config
from home_radar_forecasting.cache import evaluate_cached_future
from home_radar_forecasting.config import load_future_config
from home_radar_market.config import load_market_config
from home_radar_market.materializer import materialize_baselines
from home_radar_models.collection import CrawlRun
from home_radar_models.community import Community
from home_radar_models.decision import DecisionAssessment
from home_radar_models.enums import DataMode
from home_radar_models.future import (
    EmploymentCenter,
    FutureAssessment,
    FutureFactorObservation,
    FutureProject,
)
from home_radar_models.listing import Listing, ListingSnapshot
from home_radar_models.market import MarketObservation
from home_radar_shared.config import get_settings
from home_radar_valuation.config import load_valuation_config
from sqlalchemy import func, select
from sqlalchemy.orm import Session

pytestmark = pytest.mark.integration

AS_OF = datetime(2026, 9, 2, 8, tzinfo=UTC)


def test_future_cache_api_versions_and_mode_isolation(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = _seed_future_world(db_session)
    first, first_hit = _evaluate(db_session, target, AS_OF)
    db_session.commit()
    replay, replay_hit = _evaluate(db_session, target, AS_OF)
    assert first_hit is False
    assert replay_hit is True
    assert replay.id == first.id
    assert first.calibration_state == "uncalibrated"
    assert first.future_score is not None
    assert first.obsolescence_risk is not None
    assert first.data_timestamp is not None
    assert first.data_timestamp <= AS_OF
    assert len(first.scenarios) == 9
    assert db_session.scalar(select(func.count()).select_from(FutureAssessment)) == 1
    assert all(factor["data_mode"] == "sample" for factor in first.factor_breakdown)

    monkeypatch.setenv("SHR_MARKET_DATA_MODE", "sample")
    get_settings.cache_clear()

    def override_db() -> Iterator[Session]:
        yield db_session

    app.dependency_overrides[get_db] = override_db
    try:
        client = TestClient(app)
        params = {"as_of": AS_OF.isoformat()}
        base = f"/api/v1/future/listings/{target.id}"
        response = client.get(base, params=params)
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["cache_hit"] is True
        assert body["data_mode"] == "sample"
        assert body["recommendation_status"] == "research_uncalibrated"
        assert body["investment_conclusion"] is None
        assert body["provenance"]["asking_price_used_as_forecast_anchor"] is False
        assert client.get(f"{base}/factors", params=params).status_code == 200
        scenarios = client.get(f"{base}/scenarios", params=params)
        assert scenarios.status_code == 200
        assert len(scenarios.json()["scenarios"]) == 9
        risks = client.get(f"{base}/risks", params=params)
        assert risks.status_code == 200
        assert "UNCALIBRATED_MODEL" in risks.json()["warnings"]
    finally:
        app.dependency_overrides.clear()
        get_settings.cache_clear()


def test_new_factor_observation_invalidates_future_assessment(
    db_session: Session,
) -> None:
    target = _seed_future_world(db_session)
    first, _ = _evaluate(db_session, target, AS_OF)
    db_session.commit()
    later = AS_OF + timedelta(days=1)
    db_session.add(
        _factor(
            target,
            "supply_scarcity",
            Decimal("76"),
            Decimal("62"),
            observed_at=later - timedelta(hours=1),
            source_record_id="supply-later",
        )
    )
    db_session.commit()
    second, second_hit = _evaluate(db_session, target, later)
    assert second_hit is False
    assert second.id != first.id
    assert second.input_fingerprint != first.input_fingerprint
    assert second.data_version != first.data_version


def test_decision_cache_api_and_blind_reveal(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = _seed_future_world(db_session)
    first, first_hit = _evaluate_decision(db_session, target, AS_OF)
    db_session.commit()
    replay, replay_hit = _evaluate_decision(db_session, target, AS_OF)
    db_session.commit()
    assert first_hit is False
    assert replay_hit is True
    assert replay.id == first.id
    assert first.workflow_state in {"PASS", "WATCH", "CONTACT", "VIEW"}
    assert first.provenance["opaque_total_score_created"] is False
    assert first.provenance["attack_enabled"] is False
    assert db_session.scalar(select(func.count()).select_from(DecisionAssessment)) == 1

    monkeypatch.setenv("SHR_MARKET_DATA_MODE", "sample")
    get_settings.cache_clear()

    def override_db() -> Iterator[Session]:
        yield db_session

    app.dependency_overrides[get_db] = override_db
    try:
        client = TestClient(app)
        params = {"as_of": AS_OF.isoformat()}
        response = client.get(f"/api/v1/decisions/listings/{target.id}", params=params)
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["investment_conclusion"] is None
        assert body["workflow_state"] != "ATTACK"
        opportunities = client.get("/api/v1/decisions/opportunities?limit=10")
        assert opportunities.status_code == 200, opportunities.text
        assert opportunities.json()["evaluated_count"] == 1
        why = client.get(f"/api/v1/decisions/listings/{target.id}/why-ranked", params=params)
        assert why.status_code == 200, why.text
        assert "summary" in why.json()

        headers = {"X-API-Key": "dev-only-change-me"}
        created = client.post(
            "/api/v1/decisions/validation/batches",
            headers=headers,
            json={
                "name": "P5.5 blind test",
                "listing_ids": [str(target.id)],
                "as_of": AS_OF.isoformat(),
            },
        )
        assert created.status_code == 200, created.text
        blind = created.json()
        assert blind["status"] == "blind_labeling"
        assert blind["items"][0]["model_result"] is None
        assert blind["items"][0]["frozen_rank"] is None

        batch_id = blind["id"]
        fetched = client.get(f"/api/v1/decisions/validation/batches/{batch_id}", headers=headers)
        assert fetched.status_code == 200, fetched.text
        assert fetched.json()["items"][0]["model_result"] is None
        labeled = client.post(
            f"/api/v1/decisions/validation/batches/{batch_id}/labels",
            headers=headers,
            json={
                "labels": [
                    {
                        "listing_id": str(target.id),
                        "human_label": "WORTH_VIEWING",
                    }
                ]
            },
        )
        assert labeled.status_code == 200, labeled.text
        assert labeled.json()["items"][0]["model_result"] is None
        revealed = client.post(
            f"/api/v1/decisions/validation/batches/{batch_id}/reveal",
            headers=headers,
        )
        assert revealed.status_code == 200, revealed.text
        assert revealed.json()["status"] == "revealed"
        assert revealed.json()["items"][0]["model_result"] is not None
    finally:
        app.dependency_overrides.clear()
        get_settings.cache_clear()


def test_blind_batch_is_frozen_before_labels(db_session: Session) -> None:
    target = _seed_future_world(db_session)
    batch = create_blind_batch(
        db_session,
        name="frozen P5.5 validation",
        listing_ids=[target.id],
        data_mode=DataMode.SAMPLE,
        as_of=AS_OF,
        market_config=load_market_config(),
        valuation_config=load_valuation_config(),
        future_config=load_future_config(),
        decision_config=load_decision_config(),
    )
    _, items = validation_items(db_session, batch.id)
    frozen_version = items[0].model_snapshot["decision_version"]
    label_blind_item(db_session, batch.id, target.id, "VALUE_TRAP", "manual blind label")
    revealed = reveal_blind_batch(db_session, batch.id, load_decision_config())
    _, revealed_items = validation_items(db_session, batch.id)
    assert revealed.status == "revealed"
    assert revealed_items[0].model_snapshot["decision_version"] == frozen_version
    assert revealed.metrics["labeled_count"] == 1


def _evaluate(session: Session, target: Listing, as_of: datetime) -> tuple[FutureAssessment, bool]:
    return evaluate_cached_future(
        session,
        target.id,
        DataMode.SAMPLE,
        as_of,
        load_market_config(),
        load_valuation_config(),
        load_future_config(),
    )


def _evaluate_decision(
    session: Session, target: Listing, as_of: datetime
) -> tuple[DecisionAssessment, bool]:
    return evaluate_cached_decision(
        session,
        target.id,
        DataMode.SAMPLE,
        as_of,
        load_market_config(),
        load_valuation_config(),
        load_future_config(),
        load_decision_config(),
    )


def _seed_future_world(session: Session) -> Listing:
    session.add(
        Community(
            district="普陀",
            submarket="真如",
            community="P5测试社区",
            metro_distance_m=520,
            active_listing_count=0,
        )
    )
    target = _seed_listing(
        session,
        0,
        Decimal("2760000"),
        datetime(2026, 8, 28, 8, tzinfo=UTC),
    )
    for index in range(1, 9):
        _seed_listing(
            session,
            index,
            Decimal(2_940_000 + index * 20_000),
            datetime(2026, 8, 20, 8, tzinfo=UTC) + timedelta(hours=index),
        )
    session.commit()
    materialize_baselines(
        session,
        as_of_date=date(2026, 9, 1),
        data_mode=DataMode.SAMPLE,
        config=load_market_config(),
        input_cutoff_at=AS_OF,
    )
    _seed_future_evidence(session, target)
    session.commit()
    return target


def _seed_listing(session: Session, index: int, price: Decimal, observed_at: datetime) -> Listing:
    run = CrawlRun(
        source=f"p5-feed-{index % 3}",
        data_mode="sample",
        scope_key=f"p5-{index}",
        status="succeeded",
        completeness="complete",
        started_at=observed_at - timedelta(minutes=2),
        finished_at=observed_at,
        raw_item_count=1,
        normalized_item_count=1,
    )
    listing = Listing(
        source=run.source,
        source_listing_id=f"p5-listing-{index}",
        source_url=f"https://authorized.invalid/p5/{index}",
        district="普陀",
        submarket="真如",
        community="P5测试社区",
        longitude=Decimal("121.4000000"),
        latitude=Decimal("31.2500000"),
        coordinates=WKTElement("POINT(121.4 31.25)", srid=4326),
        total_price=price,
        unit_price=price / Decimal("60"),
        area_sqm=Decimal("60"),
        bedrooms=2,
        living_rooms=1,
        floor="中楼层",
        total_floors=12,
        orientation="南北",
        year_built=2005,
        elevator=True,
        building_type="elevator",
        first_seen_at=observed_at - timedelta(days=90),
        last_seen_at=observed_at,
        status="active",
    )
    session.add_all([run, listing])
    session.flush()
    snapshot = ListingSnapshot(
        listing_id=listing.id,
        crawl_run_id=run.id,
        snapshot_at=observed_at,
        total_price=price,
        unit_price=price / Decimal("60"),
        status="active",
        raw_payload={"authorized": True},
    )
    session.add(snapshot)
    session.flush()
    session.add(
        MarketObservation(
            observation_type="listing",
            data_mode="sample",
            source=listing.source,
            source_record_id=str(snapshot.id),
            listing_id=listing.id,
            listing_snapshot_id=snapshot.id,
            crawl_run_id=run.id,
            district=listing.district,
            submarket=listing.submarket,
            community=listing.community,
            area_sqm=listing.area_sqm,
            area_bucket="50-65",
            bedrooms=2,
            layout="2BR",
            total_price=price,
            unit_price=price / Decimal("60"),
            longitude=listing.longitude,
            latitude=listing.latitude,
            observed_at=observed_at,
            source_confidence=Decimal("0.9"),
            coverage_complete=True,
            created_at=observed_at,
            observation_metadata={
                "floor": listing.floor,
                "total_floors": listing.total_floors,
                "orientation": listing.orientation,
                "year_built": listing.year_built,
                "elevator": listing.elevator,
                "building_type": listing.building_type,
                "parking_quality": "average",
                "property_management_quality": "good",
                "maintenance_quality": "good",
                "layout_mainstreamness": "excellent",
            },
        )
    )
    return listing


def _seed_future_evidence(session: Session, target: Listing) -> None:
    values = {
        "supply_scarcity": (Decimal("72"), Decimal("78")),
        "buyer_pool_depth": (Decimal("76"), Decimal("80")),
        "community_competitiveness": (Decimal("74"), Decimal("77")),
        "urban_renewal": (Decimal("50"), Decimal("58")),
        "rental_demand": (Decimal("68"), Decimal("70")),
        "public_services": (Decimal("75"), Decimal("78")),
        "market_cycle": (Decimal("50"), Decimal("53")),
    }
    session.add_all(
        [_factor(target, name, current, future) for name, (current, future) in values.items()]
    )
    session.add(
        _factor(
            target,
            "supply_scarcity",
            Decimal("5"),
            Decimal("5"),
            source_record_id="live-mode-must-not-leak",
            data_mode="live",
        )
    )
    session.add(
        EmploymentCenter(
            data_mode="sample",
            name="P5就业中心",
            category="mixed",
            coordinates=WKTElement("POINT(121.41 31.25)", srid=4326),
            current_employment_weight=Decimal("1"),
            future_employment_weight=Decimal("1.2"),
            effective_from=AS_OF - timedelta(days=100),
            source="authorized-test",
            source_record_id="employment-1",
            source_timestamp=AS_OF - timedelta(days=10),
            confidence=Decimal("0.8"),
        )
    )
    session.add_all(
        [
            _project("current", "current-line", "POINT(121.405 31.25)"),
            _project("approved", "approved-line", "POINT(121.401 31.25)"),
        ]
    )


def _factor(
    target: Listing,
    name: str,
    current: Decimal,
    future: Decimal,
    *,
    observed_at: datetime = AS_OF - timedelta(days=5),
    source_record_id: str | None = None,
    data_mode: str = "sample",
) -> FutureFactorObservation:
    return FutureFactorObservation(
        data_mode=data_mode,
        factor=name,
        scope_type="listing",
        listing_id=target.id,
        district=target.district,
        submarket=target.submarket,
        community=target.community,
        current_score=current,
        future_score=future,
        observed_at=observed_at,
        effective_from=observed_at,
        source="authorized-test",
        source_record_id=source_record_id or f"factor-{name}",
        source_timestamp=observed_at,
        confidence=Decimal("0.8"),
        explanation=f"Authorized integration evidence for {name}.",
    )


def _project(status: str, record_id: str, point: str) -> FutureProject:
    return FutureProject(
        data_mode="sample",
        name=record_id,
        project_type="transport",
        status=status,
        coordinates=WKTElement(point, srid=4326),
        district="普陀",
        submarket="真如",
        expected_completion=date(2028, 12, 1) if status != "current" else None,
        effective_from=AS_OF - timedelta(days=100),
        source="authorized-test",
        source_record_id=record_id,
        source_date=AS_OF - timedelta(days=20),
        confidence=Decimal("0.85"),
    )

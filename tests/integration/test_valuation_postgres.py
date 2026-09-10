from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest
from home_radar_market.config import load_market_config
from home_radar_market.materializer import materialize_baselines
from home_radar_models.collection import CrawlRun
from home_radar_models.community import Community
from home_radar_models.enums import DataMode, ListingEventType, MarketObservationType
from home_radar_models.listing import Listing, ListingEvent, ListingSnapshot
from home_radar_models.market import MarketObservation
from home_radar_models.valuation import ValuationResult
from home_radar_valuation.cache import evaluate_cached_listing
from home_radar_valuation.config import load_valuation_config
from home_radar_valuation.repository import ListingNotAvailableForModeError
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

pytestmark = pytest.mark.integration

BASELINE_DATE = date(2026, 9, 1)
AS_OF = datetime(2026, 9, 2, 8, tzinfo=UTC)


def _seed_world(
    session: Session,
    *,
    same_community_comparables: int = 8,
    neighboring_comparables: int = 0,
) -> Listing:
    session.add(
        Community(
            district="普陀",
            submarket="真如",
            community="P4测试社区",
            metro_distance_m=480,
            active_listing_count=0,
        )
    )
    target = _seed_listing(
        session,
        index=0,
        community="P4测试社区",
        price=Decimal("2760000"),
        observed_at=datetime(2026, 8, 28, 8, tzinfo=UTC),
    )
    for index in range(1, same_community_comparables + 1):
        _seed_listing(
            session,
            index=index,
            community="P4测试社区",
            price=Decimal(2_940_000 + index * 20_000),
            observed_at=datetime(2026, 8, 20, 8, tzinfo=UTC) + timedelta(hours=index),
        )
    for offset in range(neighboring_comparables):
        index = 100 + offset
        _seed_listing(
            session,
            index=index,
            community="P4邻近社区",
            price=Decimal(3_000_000 + offset * 10_000),
            observed_at=datetime(2026, 8, 18, 8, tzinfo=UTC) + timedelta(hours=offset),
        )
    session.commit()
    materialize_baselines(
        session,
        as_of_date=BASELINE_DATE,
        data_mode=DataMode.SAMPLE,
        config=load_market_config(),
        input_cutoff_at=AS_OF,
    )
    return target


def _seed_listing(
    session: Session,
    *,
    index: int,
    community: str,
    price: Decimal,
    observed_at: datetime,
) -> Listing:
    run = CrawlRun(
        source=f"valuation-feed-{index % 3}",
        data_mode=DataMode.SAMPLE.value,
        scope_key=f"p4-{index}",
        status="succeeded",
        completeness="complete",
        started_at=observed_at - timedelta(minutes=2),
        finished_at=observed_at,
        raw_item_count=1,
        normalized_item_count=1,
    )
    listing = Listing(
        source=f"valuation-feed-{index % 3}",
        source_listing_id=f"p4-listing-{index}",
        source_url=f"https://authorized.invalid/{index}",
        district="普陀",
        submarket="真如",
        community=community,
        total_price=price,
        unit_price=price / Decimal("60"),
        area_sqm=Decimal("60"),
        bedrooms=2,
        living_rooms=1,
        floor="中楼层",
        total_floors=6,
        orientation="南北",
        year_built=2005,
        elevator=False,
        building_type="walk_up",
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
            observation_type=MarketObservationType.LISTING.value,
            data_mode=DataMode.SAMPLE.value,
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
            bedrooms=listing.bedrooms,
            layout="2BR",
            total_price=price,
            unit_price=price / Decimal("60"),
            observed_at=observed_at,
            source_confidence=Decimal("0.9"),
            coverage_complete=True,
            created_at=observed_at,
            observation_metadata={
                "listing_status": "active",
                "first_seen_at": listing.first_seen_at.isoformat(),
                "floor": listing.floor,
                "total_floors": listing.total_floors,
                "orientation": listing.orientation,
                "year_built": listing.year_built,
                "elevator": listing.elevator,
                "building_type": listing.building_type,
                "event_types": ["new"],
            },
        )
    )
    return listing


def test_cached_valuation_is_idempotent_and_api_is_explainable(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    from fastapi.testclient import TestClient
    from home_radar_api.dependencies import get_db
    from home_radar_api.main import app
    from home_radar_shared.config import get_settings

    target = _seed_world(db_session)
    market_config = load_market_config()
    valuation_config = load_valuation_config()
    first, first_hit = evaluate_cached_listing(
        db_session,
        target.id,
        DataMode.SAMPLE,
        AS_OF,
        market_config,
        valuation_config,
    )
    db_session.commit()
    replay, replay_hit = evaluate_cached_listing(
        db_session,
        target.id,
        DataMode.SAMPLE,
        AS_OF,
        market_config,
        valuation_config,
    )
    assert first_hit is False
    assert replay_hit is True
    assert replay.id == first.id
    assert db_session.scalar(select(func.count()).select_from(ValuationResult)) == 1

    monkeypatch.setenv("SHR_MARKET_DATA_MODE", "sample")
    get_settings.cache_clear()

    def override_db() -> Iterator[Session]:
        yield db_session

    app.dependency_overrides[get_db] = override_db
    try:
        client = TestClient(app)
        params = {"as_of": AS_OF.isoformat()}
        response = client.get(f"/api/v1/valuation/listings/{target.id}", params=params)
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["cache_hit"] is True
        assert body["recommendation_status"] == "demo_only"
        assert body["valuation_basis"] == "listing_derived"
        assert Decimal(body["fair_value_low"]) <= Decimal(body["fair_value"])
        assert Decimal(body["fair_value"]) <= Decimal(body["fair_value_high"])
        assert body["comparables"][0]["reason"]
        assert body["provenance"]["llm_used_for_pricing"] is False

        comparables = client.get(
            f"/api/v1/valuation/listings/{target.id}/comparables", params=params
        )
        assert comparables.status_code == 200
        assert comparables.json()["comparable_count"] >= 3
        explanation = client.get(
            f"/api/v1/valuation/listings/{target.id}/explanation", params=params
        )
        assert explanation.status_code == 200
        assert explanation.json()["baseline_level_used"] == "community"
    finally:
        app.dependency_overrides.clear()
        get_settings.cache_clear()


def test_price_change_late_comparable_and_model_version_invalidate_cache(
    db_session: Session,
) -> None:
    target = _seed_world(db_session)
    market_config = load_market_config()
    config = load_valuation_config()
    initial, _ = evaluate_cached_listing(
        db_session, target.id, DataMode.SAMPLE, AS_OF, market_config, config
    )
    db_session.commit()

    _add_target_price_snapshot(db_session, target, Decimal("2600000"), AS_OF - timedelta(hours=1))
    db_session.commit()
    cheaper, _ = evaluate_cached_listing(
        db_session, target.id, DataMode.SAMPLE, AS_OF, market_config, config
    )
    db_session.commit()
    assert cheaper.input_fingerprint != initial.input_fingerprint
    assert cheaper.fair_value == initial.fair_value
    assert cheaper.value_score > initial.value_score

    candidate_id = db_session.scalar(
        select(MarketObservation.listing_id).where(
            MarketObservation.listing_id.is_not(None),
            MarketObservation.listing_id != target.id,
        )
    )
    assert candidate_id is not None
    db_session.add(
        MarketObservation(
            observation_type="listing",
            data_mode="sample",
            source="late-authorized-feed",
            source_record_id="late-comparable-revision",
            listing_id=candidate_id,
            district="普陀",
            submarket="真如",
            community="P4测试社区",
            area_sqm=Decimal("60"),
            area_bucket="50-65",
            bedrooms=2,
            layout="2BR",
            total_price=Decimal("3180000"),
            unit_price=Decimal("53000"),
            observed_at=AS_OF - timedelta(minutes=30),
            source_confidence=Decimal("0.9"),
            coverage_complete=True,
            observation_metadata={"year_built": 2005},
        )
    )
    db_session.commit()
    late, _ = evaluate_cached_listing(
        db_session, target.id, DataMode.SAMPLE, AS_OF, market_config, config
    )
    db_session.commit()
    assert late.input_fingerprint != cheaper.input_fingerprint

    changed_config = config.model_copy(update={"valuation_model_version": "p4-fair-value-v2-test"})
    versioned, _ = evaluate_cached_listing(
        db_session,
        target.id,
        DataMode.SAMPLE,
        AS_OF,
        market_config,
        changed_config,
    )
    db_session.commit()
    assert versioned.valuation_model_version == "p4-fair-value-v2-test"
    assert versioned.valuation_version != late.valuation_version


def test_transaction_evidence_is_visible_and_mode_isolation_is_strict(
    db_session: Session,
) -> None:
    target = _seed_world(db_session)
    for index in range(3):
        price = Decimal(2_850_000 + index * 25_000)
        db_session.add(
            MarketObservation(
                observation_type="transaction",
                data_mode="sample",
                source="authorized-transaction-feed",
                source_record_id=f"verified-close-{index}",
                district="普陀",
                submarket="真如",
                community="P4测试社区",
                area_sqm=Decimal("60"),
                area_bucket="50-65",
                bedrooms=2,
                layout="2BR",
                total_price=price,
                unit_price=price / Decimal("60"),
                observed_at=datetime(2026, 8, 25, 8 + index, tzinfo=UTC),
                source_confidence=Decimal("1"),
                coverage_complete=True,
                observation_metadata={
                    "floor": "中楼层",
                    "total_floors": 6,
                    "orientation": "南北",
                    "year_built": 2005,
                    "elevator": False,
                    "building_type": "walk_up",
                },
            )
        )
    db_session.commit()
    materialize_baselines(
        db_session,
        as_of_date=BASELINE_DATE,
        data_mode=DataMode.SAMPLE,
        config=load_market_config(),
        input_cutoff_at=AS_OF,
    )
    result, _ = evaluate_cached_listing(
        db_session,
        target.id,
        DataMode.SAMPLE,
        AS_OF,
        load_market_config(),
        load_valuation_config(),
    )
    assert result.transaction_support == "strong"
    assert result.valuation_basis in {"mixed_source", "transaction_supported"}
    assert any(item["observation_type"] == "transaction" for item in result.comparables)

    with pytest.raises(ListingNotAvailableForModeError):
        evaluate_cached_listing(
            db_session,
            target.id,
            DataMode.LIVE,
            AS_OF,
            load_market_config(),
            load_valuation_config(),
        )


def test_hierarchical_baseline_fallback_lowers_confidence(db_session: Session) -> None:
    target = _seed_world(db_session, same_community_comparables=2, neighboring_comparables=8)
    result, _ = evaluate_cached_listing(
        db_session,
        target.id,
        DataMode.SAMPLE,
        AS_OF,
        load_market_config(),
        load_valuation_config(),
    )
    assert result.baseline_level_used == "community_submarket_shrunk"
    assert "community_sample_below_threshold" in (result.fallback_reason or "")
    assert result.valuation_confidence != "high"


def test_database_constraints_and_structured_evaluate_api(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    from fastapi.testclient import TestClient
    from home_radar_api.dependencies import get_db
    from home_radar_api.main import app
    from home_radar_shared.config import get_settings

    _seed_world(db_session)
    invalid = ValuationResult(
        listing_id=db_session.scalar(select(Listing.id)),
        data_mode="sample",
        valuation_version="invalid",
        input_fingerprint="x" * 64,
        calculated_at=AS_OF,
        fair_value=Decimal("100"),
        fair_value_low=Decimal("200"),
        fair_value_high=Decimal("300"),
        valuation_basis="listing_derived",
        valuation_confidence_score=Decimal("0.5"),
        valuation_confidence="medium",
        transaction_support="none",
        current_ask=Decimal("100"),
        ask_discount_to_fair_value=Decimal("0"),
        value_score=Decimal("50"),
        decision="pass",
        comparable_count=0,
        effective_comparable_count=Decimal("0"),
        baseline_level_used="none",
        baseline_confidence="insufficient",
        baseline_version="none",
        valuation_model_version="v1",
        scoring_model_version="s1",
        configuration_version="c1",
    )
    db_session.add(invalid)
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()

    monkeypatch.setenv("SHR_MARKET_DATA_MODE", "sample")
    get_settings.cache_clear()

    def override_db() -> Iterator[Session]:
        yield db_session

    app.dependency_overrides[get_db] = override_db
    try:
        client = TestClient(app)
        response = client.post(
            "/api/v1/valuation/evaluate",
            json={
                "as_of": AS_OF.isoformat(),
                "target": {
                    "district": "普陀",
                    "submarket": "真如",
                    "community": "P4测试社区",
                    "area_sqm": 60,
                    "current_ask": 2700000,
                    "bedrooms": 2,
                    "floor": "中楼层",
                    "total_floors": 6,
                    "orientation": "南北",
                    "year_built": 2005,
                    "elevator": False,
                    "building_type": "walk_up",
                    "metro_distance_m": 480,
                    "layout_quality": "mainstream",
                    "hard_defects_known": True,
                },
                "price_history": {
                    "original_ask": 3000000,
                    "price_cut_count": 2,
                    "days_since_last_cut": 10,
                    "days_on_market": 120,
                    "relisting_flag": False,
                },
            },
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["listing_id"] is None
        assert body["estimated_executable_price"] is None
        assert body["recommendation_status"] == "demo_only"
    finally:
        app.dependency_overrides.clear()
        get_settings.cache_clear()


def _add_target_price_snapshot(
    session: Session,
    target: Listing,
    price: Decimal,
    observed_at: datetime,
) -> None:
    run = CrawlRun(
        source=target.source,
        data_mode="sample",
        scope_key="p4-target-price-change",
        status="succeeded",
        completeness="complete",
        started_at=observed_at - timedelta(minutes=1),
        finished_at=observed_at,
        raw_item_count=1,
        normalized_item_count=1,
    )
    session.add(run)
    session.flush()
    snapshot = ListingSnapshot(
        listing_id=target.id,
        crawl_run_id=run.id,
        snapshot_at=observed_at,
        total_price=price,
        unit_price=price / target.area_sqm,
        status="active",
        raw_payload={"authorized": True},
    )
    session.add(snapshot)
    session.flush()
    session.add_all(
        [
            ListingEvent(
                listing_id=target.id,
                crawl_run_id=run.id,
                event_type=ListingEventType.PRICE_CUT.value,
                occurred_at=observed_at,
                previous_value=str(target.total_price),
                current_value=str(price),
                details={"authorized": True},
            ),
            MarketObservation(
                observation_type="listing",
                data_mode="sample",
                source=target.source,
                source_record_id=str(snapshot.id),
                listing_id=target.id,
                listing_snapshot_id=snapshot.id,
                crawl_run_id=run.id,
                district=target.district,
                submarket=target.submarket,
                community=target.community,
                area_sqm=target.area_sqm,
                area_bucket="50-65",
                bedrooms=target.bedrooms,
                layout="2BR",
                total_price=price,
                unit_price=price / target.area_sqm,
                observed_at=observed_at,
                source_confidence=Decimal("0.9"),
                coverage_complete=True,
                observation_metadata={
                    "floor": target.floor,
                    "total_floors": target.total_floors,
                    "orientation": target.orientation,
                    "year_built": target.year_built,
                    "elevator": target.elevator,
                    "building_type": target.building_type,
                },
            ),
        ]
    )

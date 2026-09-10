from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest
from home_radar_collector.registry import build_configured_adapter
from home_radar_market.confidence import ConfidenceInputs, calculate_confidence
from home_radar_market.config import load_market_config
from home_radar_market.demo_seed import main as demo_seed_main
from home_radar_market.domain import (
    BaselineSliceKey,
    ObservationRecord,
    latest_per_entity,
    slice_keys,
)
from home_radar_market.liquidity import LiquidityInputs, calculate_liquidity
from home_radar_market.listing_metrics import calculate_listing_metrics
from home_radar_market.resolver import BaselineCandidate, resolve_hierarchy
from home_radar_market.statistics import detect_outliers, percentile, percentile_set
from home_radar_market.windows import closed_day_window
from home_radar_models.enums import BaselineConfidence, BaselineLevel
from home_radar_shared.config import Settings, get_settings


def _record(
    *,
    observed_at: datetime,
    listing_id: uuid.UUID | None = None,
    observation_type: str = "listing",
    price: Decimal | None = Decimal("3000000"),
    unit_price: Decimal | None = Decimal("50000"),
    complete: bool = True,
    status: str = "active",
    source_record_id: str = "record-1",
) -> ObservationRecord:
    return ObservationRecord(
        id=uuid.uuid4(),
        observation_type=observation_type,
        source="test-source",
        source_record_id=source_record_id,
        listing_id=listing_id,
        district="普陀",
        submarket="真如",
        community="测试社区",
        area_bucket="50-65",
        layout="2BR",
        total_price=price,
        unit_price=unit_price,
        monthly_rent=None,
        rent_per_sqm=None,
        index_value=None,
        observed_at=observed_at,
        created_at=observed_at + timedelta(minutes=1),
        coverage_complete=complete,
        metadata={
            "listing_status": status,
            "first_seen_at": (observed_at - timedelta(days=10)).isoformat(),
            "event_types": [],
        },
    )


def test_area_bucket_and_layout_boundaries() -> None:
    config = load_market_config()
    cases = {
        Decimal("39.99"): "<40",
        Decimal("40"): "40-50",
        Decimal("49.99"): "40-50",
        Decimal("50"): "50-65",
        Decimal("65"): "65-80",
        Decimal("80"): "80-100",
        Decimal("100"): "100+",
    }
    assert {value: config.area_bucket_for(value) for value in cases} == cases
    assert config.layout_for(None) is None
    assert config.layout_for(1) == "1BR"
    assert config.layout_for(2) == "2BR"
    assert config.layout_for(3) == "3BR+"


def test_shanghai_closed_day_window_is_half_open() -> None:
    window = closed_day_window(date(2026, 9, 1), 30)
    assert window.start_at == datetime(2026, 8, 2, 16, tzinfo=UTC)
    assert window.end_at == datetime(2026, 9, 1, 16, tzinfo=UTC)
    assert window.contains(window.start_at)
    assert not window.contains(window.end_at)


def test_decimal_type_7_percentiles() -> None:
    values = [Decimal("1"), Decimal("2"), Decimal("3"), Decimal("4")]
    result = percentile_set(values)
    assert result.p25 == Decimal("1.75")
    assert result.p50 == Decimal("2.5")
    assert result.p75 == Decimal("3.25")
    assert percentile([], Decimal("0.5")) is None
    assert percentile([Decimal("7")], Decimal("0.9")) == Decimal("7")


def test_outliers_are_contextual_and_retained() -> None:
    config = load_market_config()
    values = [Decimal(value) for value in range(100, 108)] + [Decimal("1000")]
    decision = detect_outliers(values, config.outliers)
    assert decision.outlier_indexes == frozenset({8})
    assert values[8] == Decimal("1000")
    degenerate = detect_outliers([Decimal("10")] * 9, config.outliers)
    assert degenerate.outlier_indexes == frozenset()
    assert degenerate.degenerate_dispersion is True


def test_latest_snapshot_is_one_entity_and_types_stay_separate() -> None:
    listing_id = uuid.uuid4()
    earlier = _record(observed_at=datetime(2026, 8, 1, tzinfo=UTC), listing_id=listing_id)
    later = _record(observed_at=datetime(2026, 8, 2, tzinfo=UTC), listing_id=listing_id)
    transaction = _record(
        observed_at=datetime(2026, 8, 2, tzinfo=UTC),
        observation_type="transaction",
        source_record_id="transaction-1",
    )
    assert latest_per_entity([earlier, later]) == [later]
    listing_keys = slice_keys(later, 90)
    transaction_keys = slice_keys(transaction, 90)
    assert all(key.observation_type == "listing" for key in listing_keys)
    assert all(key.observation_type == "transaction" for key in transaction_keys)
    assert listing_keys.isdisjoint(transaction_keys)


def test_confidence_applies_coverage_and_single_source_caps() -> None:
    config = load_market_config()
    result = calculate_confidence(
        ConfidenceInputs(
            sample_size=30,
            median_age_days=Decimal("1"),
            window_days=30,
            source_counts={"one": 30},
            complete_count=10,
            outlier_count=0,
            represented_time_bins=1,
            expected_time_bins=1,
            has_recent_complete_run=False,
        ),
        config.confidence,
    )
    assert result.level is BaselineConfidence.LOW
    assert "coverage_or_fresh_complete_run" in result.caps
    assert "single_source" in result.caps
    insufficient = calculate_confidence(
        ConfidenceInputs(2, Decimal(), 30, {"one": 2}, 2, 0, 1, 1, True),
        config.confidence,
    )
    assert insufficient.level is BaselineConfidence.INSUFFICIENT


def test_liquidity_exit_has_no_sale_semantics_and_missing_weights_reduce_confidence() -> None:
    config = load_market_config()
    result = calculate_liquidity(
        LiquidityInputs(
            active_inventory=20,
            new_listings=8,
            listing_exits=3,
            median_days_on_market=Decimal("35"),
            price_cut_ratio=Decimal("0.2"),
            relisting_rate=None,
            buyer_pool_ratio=None,
            baseline_confidence=Decimal("0.8"),
        ),
        config.liquidity,
    )
    assert result.score is not None
    assert "not interpreted as a sale" in result.components["listing_exit_velocity"].semantic_note
    assert result.available_weight < Decimal("1")
    assert result.confidence < Decimal("0.8")


def test_partial_negative_observation_does_not_reduce_inventory() -> None:
    listing_id = uuid.uuid4()
    end_at = datetime(2026, 9, 1, 16, tzinfo=UTC)
    complete_active = _record(
        observed_at=end_at - timedelta(days=10), listing_id=listing_id, complete=True
    )
    partial_inactive = _record(
        observed_at=end_at - timedelta(days=1),
        listing_id=listing_id,
        complete=False,
        status="inactive",
    )
    key = BaselineSliceKey("listing", 30, "community", "普陀", "真如", "测试社区", "50-65", "2BR")
    metrics = calculate_listing_metrics(
        [complete_active, partial_inactive], key, end_at - timedelta(days=30), end_at
    )
    assert metrics.active_inventory == 1
    assert metrics.inactive_count == 0


def test_hierarchy_shrinks_small_community_into_submarket() -> None:
    config = load_market_config()
    candidates = {
        BaselineLevel.COMMUNITY: BaselineCandidate(
            BaselineLevel.COMMUNITY,
            4,
            BaselineConfidence.LOW,
            {"price_p50": Decimal("400")},
            "community-v1",
        ),
        BaselineLevel.SUBMARKET: BaselineCandidate(
            BaselineLevel.SUBMARKET,
            20,
            BaselineConfidence.HIGH,
            {"price_p50": Decimal("300")},
            "submarket-v1",
        ),
    }
    result = resolve_hierarchy(candidates, config.fallback)
    expected_weight = Decimal(4) / Decimal(4 + config.fallback.prior_strength)
    assert result.level_used == "community_submarket_shrunk"
    assert result.local_weight == expected_weight
    assert result.quantiles["price_p50"] == (
        expected_weight * Decimal("400") + (Decimal("1") - expected_weight) * Decimal("300")
    )
    assert result.fallback_path == ("community", "submarket")


def test_live_mode_rejects_sample_or_local_feed() -> None:
    with pytest.raises(ValueError, match="LIVE mode"):
        build_configured_adapter(
            Settings(
                market_data_mode="live",
                collector_source="sample_json",
                collector_endpoint="data/sample/example_source_listings.json",
            )
        )


def test_demo_seed_refuses_non_demo_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SHR_MARKET_DATA_MODE", "sample")
    get_settings.cache_clear()
    try:
        with pytest.raises(RuntimeError, match="requires"):
            demo_seed_main()
    finally:
        get_settings.cache_clear()


def test_scheduler_registers_one_next_shanghai_day(monkeypatch: pytest.MonkeyPatch) -> None:
    import home_radar_market.scheduler as scheduler

    class FakeRedis:
        @staticmethod
        def from_url(_: str) -> object:
            return object()

    class FakeQueue:
        def __init__(self, _: str, connection: object) -> None:
            self.connection = connection
            self.enqueued: list[tuple[object, tuple[object, ...], dict[str, object]]] = []

        def fetch_job(self, _: str) -> None:
            return None

        def enqueue_at(self, when: object, *args: object, **kwargs: object) -> None:
            self.enqueued.append((when, args, kwargs))

    queue = FakeQueue("market", object())
    monkeypatch.setattr(scheduler, "Redis", FakeRedis)
    monkeypatch.setattr(scheduler, "Queue", lambda *_args, **_kwargs: queue)
    get_settings.cache_clear()
    try:
        job_id = scheduler.schedule_next_materialization(datetime(2026, 9, 2, 0, 0, tzinfo=UTC))
    finally:
        get_settings.cache_clear()
    assert job_id == "market-daily-sample-2026-09-02"
    assert len(queue.enqueued) == 1
    when, args, kwargs = queue.enqueued[0]
    assert when == datetime(2026, 9, 3, 1, 15, tzinfo=scheduler.SHANGHAI)
    assert args[1] == "2026-09-02"
    assert kwargs["job_id"] == job_id

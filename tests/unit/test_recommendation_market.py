from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from home_radar_market.config import MarketBaselineConfig
from home_radar_shared.config import load_yaml_config

from scripts.recommendation_market import MarketEvidenceStore

WINDOW_END = datetime.now(UTC) - timedelta(hours=1)


def record(**updates: object) -> dict:
    return {
        "source_id": "provider",
        "source_record_id": "1",
        "source_url": "https://example.org/aggregate",
        "observed_at": (WINDOW_END + timedelta(minutes=30)).isoformat(),
        "window_start": (WINDOW_END - timedelta(days=30)).isoformat(),
        "window_end": WINDOW_END.isoformat(),
        "coverage_complete": True,
        "district": "徐汇",
        "active_inventory": 25,
        "new_listings": 10,
        "listing_exits": 8,
        "median_days_on_market": 0,
        "price_cut_ratio": 0,
        "relisting_rate": 0,
        "buyer_pool_ratio": 1,
    } | updates


def payload(*records: dict) -> dict:
    return {"data_mode": "sample", "verified_by": "人工核验", "records": list(records)}


def config() -> MarketBaselineConfig:
    return MarketBaselineConfig.model_validate(
        load_yaml_config(Path("config/market_baseline.yaml"))
    )


def test_empty_and_real_engine_score_preserves_evidence(tmp_path: Path) -> None:
    store = MarketEvidenceStore(tmp_path)
    item = {"district": "徐汇区", "completeness": "partial"}
    assert store.liquidity(item, datetime.now(UTC), config()) is None
    assert store.latest_timestamp() is None
    store.import_evidence(payload(record()))
    result = store.liquidity(item, datetime.now(UTC), config())
    assert result is not None
    assert result["score"] == Decimal(100)
    assert result["confidence"] == Decimal("0.42")
    assert result["available_weight"] == Decimal(1)
    assert result["evidence"]["evidence_kind"] == "provider_reported_complete_aggregate"
    assert (
        "not interpreted as a sale"
        in result["components"]["listing_exit_velocity"]["semantic_note"]
    )
    assert item["completeness"] == "partial"
    assert store.liquidity(item, datetime(2025, 2, 1, tzinfo=UTC), config()) is None
    assert store.liquidity({"district": "闵行"}, datetime.now(UTC), config()) is None


@pytest.mark.parametrize(
    "updates",
    [
        {"active_inventory": True},
        {"new_listings": -1},
        {"listing_exits": 1.5},
        {"price_cut_ratio": "0.2"},
        {"buyer_pool_ratio": float("nan")},
        {"relisting_rate": 1.1},
        {"median_days_on_market": -1},
        {"coverage_complete": False},
        {"coverage_complete": 1},
        {"window_start": "2025-01-01"},
        {"window_start": "2025-01-02T00:00:00Z"},
        {"window_end": "2025-02-02T00:00:00Z"},
        {"observed_at": (datetime.now(UTC) + timedelta(days=1)).isoformat()},
        {"source_url": "http://example.org"},
        {"source_url": "https://u:p@example.org"},
        {"source_url": "https://example.org?token=secret"},
        {"community": "测试小区"},
    ],
)
def test_invalid_aggregate_never_writes(tmp_path: Path, updates: dict) -> None:
    store = MarketEvidenceStore(tmp_path)
    with pytest.raises(ValueError):
        store.import_evidence(payload(record(**updates)))
    assert not store.path.exists()


def test_idempotency_and_atomic_conflict(tmp_path: Path) -> None:
    store = MarketEvidenceStore(tmp_path)
    assert store.import_evidence(payload(record(), record()))["duplicates"] == 1
    before = store.path.read_bytes()
    assert store.import_evidence(payload(record()))["duplicates"] == 1
    assert store.path.read_bytes() == before
    with pytest.raises(ValueError, match="conflict"):
        store.import_evidence(payload(record(source_record_id="2"), record(active_inventory=26)))
    assert store.path.read_bytes() == before


def test_specific_scope_unknown_metrics_do_not_fallback_to_high_score(tmp_path: Path) -> None:
    store = MarketEvidenceStore(tmp_path)
    scoped = record(
        source_record_id="2",
        submarket="长桥",
        community="测试",
        active_inventory=1,
        new_listings=None,
        listing_exits=None,
        median_days_on_market=None,
        price_cut_ratio=None,
        relisting_rate=None,
        buyer_pool_ratio=None,
    )
    store.import_evidence(payload(record(), scoped))
    result = store.liquidity(
        {"district": "徐汇", "submarket": "长桥", "community": "测试"}, datetime.now(UTC), config()
    )
    assert result is not None
    assert result["score"] is None
    assert result["available_weight"] == Decimal("0.20")
    assert set(result["components"]) == {"inventory_depth"}
    assert result["evidence"]["source_record_id"] == "2"


def test_new_intake_of_old_window_cannot_create_current_liquidity(tmp_path: Path) -> None:
    store = MarketEvidenceStore(tmp_path)
    old_end = datetime.now(UTC) - timedelta(hours=37)
    store.import_evidence(
        payload(
            record(
                window_start=(old_end - timedelta(days=30)).isoformat(),
                window_end=old_end.isoformat(),
            )
        )
    )
    assert store.liquidity({"district": "徐汇"}, datetime.now(UTC), config()) is None

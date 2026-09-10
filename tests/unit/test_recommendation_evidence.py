from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from pydantic import ValidationError

from scripts.recommendation_evidence import (
    PropertyEvidence,
    RecommendationEvidenceStore,
    TransactionEvidence,
)


def property_record(**updates: object) -> dict:
    return {
        "listing_id": "S1",
        "field": "elevator",
        "value": True,
        "source_url": "https://example.org/property/1",
        "observed_at": "2025-01-02T00:00:00Z",
        "verified_by": "人工核验",
    } | updates


def transaction(**updates: object) -> dict:
    return {
        "record_kind": "closed_transaction",
        "source_record_id": "T1",
        "district": "徐汇",
        "submarket": "长桥",
        "community": "测试小区",
        "area_sqm": 60.0,
        "price_wan": 280.0,
        "bedrooms": 2,
        "sold_at": "2025-01-01T00:00:00Z",
        "source_url": "https://example.org/transaction/1",
        "observed_at": "2025-01-02T00:00:00Z",
        "verified_by": "人工核验",
    } | updates


def batch(*records: dict) -> dict:
    return {"source_id": "verified_transactions", "records": list(records)}


@pytest.mark.parametrize(
    "updates",
    [
        {"source_url": "http://example.org/1"},
        {"source_url": "https://user:password@example.org/1"},
        {"source_url": "https://example.org/1?api_key=secret"},
        {"observed_at": "2025-01-01T00:00:00"},
        {"observed_at": (datetime.now(UTC) + timedelta(days=1)).isoformat()},
        {"verified_by": " "},
        {"unexpected": "field"},
    ],
)
def test_invalid_provenance_rejected_for_both_evidence_types(updates: dict) -> None:
    with pytest.raises(ValidationError):
        PropertyEvidence.model_validate(property_record(**updates))
    with pytest.raises(ValidationError):
        TransactionEvidence.model_validate(transaction(**updates))


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("elevator", "true"),
        ("bedrooms", True),
        ("bedrooms", 2.5),
        ("year_built", datetime.now(UTC).year + 1),
        ("floor", []),
        ("seller_indicated_price_wan", float("nan")),
        ("seller_indicated_price_wan", float("inf")),
        ("seller_indicated_price_wan", True),
        ("commute_minutes", {"destination": "人民广场", "minutes": float("nan")}),
        ("commute_minutes", {"destination": "人民广场", "minutes": "30"}),
        ("commute_minutes", {"destination": "人民广场", "minutes": True}),
        ("commute_minutes", {"destination": " ", "minutes": 30}),
    ],
)
def test_property_field_value_types_are_strict(field: str, value: object) -> None:
    with pytest.raises(ValidationError):
        PropertyEvidence.model_validate(property_record(field=field, value=value))


@pytest.mark.parametrize(
    "updates",
    [
        {"price_wan": float("nan")},
        {"area_sqm": float("inf")},
        {"price_wan": True},
        {"bedrooms": True},
        {"elevator": "true"},
        {"sold_at": "2025-01-03T00:00:00Z"},
        {"sold_at": "2025-01-01T00:00:00"},
        {"year_built": 2026},
    ],
)
def test_invalid_transaction_values_rejected(updates: dict) -> None:
    with pytest.raises(ValidationError):
        TransactionEvidence.model_validate(transaction(**updates))


def test_property_append_idempotency_and_listing_isolation(tmp_path: Path) -> None:
    store = RecommendationEvidenceStore(tmp_path)
    first = store.append_property(property_record())
    fingerprint = store.fingerprint()
    assert store.append_property(property_record()) == first
    assert store.fingerprint() == fingerprint
    store.append_property(property_record(listing_id="S2", value=False))
    assert store.property_items("S1", datetime.now(UTC)) == [first]
    assert store.property_items("S1", datetime(2025, 1, 1, tzinfo=UTC)) == []
    assert store.latest_timestamp() is not None
    assert store.path.stat().st_mode & 0o777 == 0o600


def test_transactions_idempotent_within_and_across_batches(tmp_path: Path) -> None:
    store = RecommendationEvidenceStore(tmp_path)
    result = store.import_transactions(batch(transaction(), transaction()))
    assert result["imported"] == 1
    assert result["duplicates"] == 1
    before = store.fingerprint()
    assert store.import_transactions(batch(transaction()))["imported"] == 0
    assert store.fingerprint() == before
    assert len(store.transaction_items(datetime.now(UTC))) == 1
    assert store.transaction_items(datetime(2025, 1, 1, tzinfo=UTC)) == []


def test_batch_conflict_never_partially_writes(tmp_path: Path) -> None:
    store = RecommendationEvidenceStore(tmp_path)
    store.import_transactions(batch(transaction()))
    before = store.path.read_bytes()
    with pytest.raises(ValueError, match="冲突"):
        store.import_transactions(
            batch(transaction(source_record_id="T2"), transaction(price_wan=290))
        )
    assert store.path.read_bytes() == before
    empty = RecommendationEvidenceStore(tmp_path / "empty")
    with pytest.raises(ValueError, match="冲突"):
        empty.import_transactions(batch(transaction(), transaction(price_wan=290)))
    assert not empty.path.exists()


def test_seller_price_and_transactions_never_replace_asking_price(tmp_path: Path) -> None:
    store = RecommendationEvidenceStore(tmp_path)
    store.append_property(property_record(field="seller_indicated_price_wan", value=260))
    store.append_property(property_record())
    store.import_transactions(batch(transaction(price_wan=250)))
    original = {"listing_id": "S1", "price_wan": 280, "observed_at": "2025-01-01T00:00:00Z"}
    enriched = store.enrich(original, datetime.now(UTC))
    assert enriched["price_wan"] == original["price_wan"] == 280
    assert enriched["observed_at"] == original["observed_at"]
    assert "elevator" not in original
    assert enriched["elevator"] is True
    assert len(enriched["verified_evidence"]) == 2

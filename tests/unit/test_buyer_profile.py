from __future__ import annotations

import json
import stat
from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import ValidationError

from scripts.buyer_profile import BuyerProfile, BuyerProfileStore, match_listing


def profile(**updates: object) -> dict:
    return BuyerProfile(confirmed=True, commute_not_required=True).model_dump() | updates


def listing(**updates: object) -> dict:
    return {
        "price_wan": 280,
        "district": "徐汇区",
        "bedrooms": 2,
        "observed_at": "2026-09-08T07:00:00+00:00",
    } | updates


def test_defaults_are_explicitly_unconfirmed_and_not_matches(tmp_path: Path) -> None:
    value = BuyerProfileStore(tmp_path).get()
    assert value["confirmed"] is False
    assert value["assumed_fields"] == ["budget_min_wan", "budget_max_wan", "districts"]
    assert value["saved_at"] is None
    assert match_listing(listing(), value)["status"] == "PROFILE_INCOMPLETE"


def test_store_roundtrip_atomic_permissions_and_stable_revision(tmp_path: Path) -> None:
    store = BuyerProfileStore(tmp_path)
    first = store.save(profile())
    assert store.get() == first
    assert first["assumed_fields"] == []
    assert store.save(profile())["revision"] == first["revision"]
    assert store.save(profile(bedrooms_min=3))["revision"] != first["revision"]
    assert stat.S_IMODE(store.path.stat().st_mode) == 0o600
    assert not list(tmp_path.glob(".buyer-profile-*"))


@pytest.mark.parametrize(
    "updates",
    [
        {"budget_min_wan": 400},
        {"bedrooms_min": 3, "bedrooms_max": 2},
        {"budget_max_wan": float("inf")},
        {"districts": [" "]},
        {"elevator_required": "true"},
        {"commute_destination": "人民广场"},
        {"unsupported": 1},
    ],
)
def test_invalid_constraints_rejected(updates: dict) -> None:
    with pytest.raises(ValidationError):
        BuyerProfile.model_validate(profile(**updates))


def test_naive_saved_timestamp_rejected(tmp_path: Path) -> None:
    store = BuyerProfileStore(tmp_path)
    store.save(profile())
    value = json.loads(store.path.read_text())
    value["saved_at"] = "2026-09-08T07:00:00"
    store.path.write_text(json.dumps(value))
    with pytest.raises(ValueError, match="timezone"):
        store.get()


def test_unknown_optional_fields_do_not_block_but_required_fields_do() -> None:
    assert match_listing(listing(), profile())["status"] == "MATCH"
    assert match_listing(listing(), profile(elevator_required=True))["status"] == "NEEDS_EVIDENCE"
    assert (
        match_listing(listing(elevator=False), profile(elevator_required=True))["status"]
        == "EXCLUDED"
    )
    assert match_listing(listing(price_wan=None), profile())["status"] == "NEEDS_EVIDENCE"
    assert (
        match_listing(listing(bedrooms=None), profile(bedrooms_min=2))["status"] == "NEEDS_EVIDENCE"
    )
    assert match_listing(listing(price_wan=301), profile())["status"] == "EXCLUDED"
    assert match_listing(listing(district="闵行"), profile())["status"] == "EXCLUDED"


def test_commute_requires_confirmed_destination_duration_and_source() -> None:
    preferences = profile(
        commute_not_required=False, commute_destination="人民广场", commute_max_minutes=45
    )
    item = listing(metro_distance_m=100, commute_minutes=20)
    assert match_listing(item, preferences)["status"] == "NEEDS_EVIDENCE"
    record = {
        "field": "commute_minutes",
        "value": {"destination": "人民广场", "minutes": 35},
        "source_url": "https://map.example/route/1",
        "observed_at": "2026-09-08T06:00:00Z",
        "verified_by": "manual-route-check",
    }
    assert match_listing(item, preferences, [record])["status"] == "MATCH"
    for invalid in [
        record | {"source_url": ""},
        record | {"verified_by": ""},
        record | {"observed_at": "2026-09-08T08:00:00Z"},
        record | {"observed_at": "2026-09-08T06:00:00"},
        record | {"value": {"destination": "徐家汇", "minutes": 20}},
    ]:
        assert match_listing(item, preferences, [invalid])["status"] == "NEEDS_EVIDENCE"
    slow = record | {"value": {"destination": "人民广场", "minutes": 60}}
    assert match_listing(item, preferences, [record, slow])["status"] == "EXCLUDED"


def test_confirmation_does_not_fill_missing_commute() -> None:
    result = match_listing(listing(), profile(commute_not_required=False))
    assert result["status"] == "PROFILE_INCOMPLETE"
    assert "通勤目的地尚未明确" in result["missing"]


@pytest.mark.parametrize("floor", ["中层", "中楼层", "中层(共6层)", "中楼层（共 6 层）"])
def test_explicit_floor_labels_match_equivalent_source_wording(floor: str) -> None:
    assert (
        match_listing(listing(floor=floor), profile(allowed_floor_labels=["中层"]))["status"]
        == "MATCH"
    )


@pytest.mark.parametrize("floor", [None, "", "未知", "暂无楼层信息", "共6层", "3层", "中高层"])
def test_unclassified_floor_never_guesses_a_category(floor: str | None) -> None:
    result = match_listing(listing(floor=floor), profile(allowed_floor_labels=["中层"]))
    assert result["status"] == "NEEDS_EVIDENCE"


def test_explicit_floor_mismatch_and_numbered_requirement() -> None:
    assert (
        match_listing(listing(floor="高楼层"), profile(allowed_floor_labels=["中层"]))["status"]
        == "EXCLUDED"
    )
    assert (
        match_listing(listing(floor="3层"), profile(allowed_floor_labels=["3层"]))["status"]
        == "MATCH"
    )


def test_explicit_assessment_cutoff_accepts_newer_evidence_without_redating_listing() -> None:
    preferences = profile(
        commute_not_required=False, commute_destination="人民广场", commute_max_minutes=45
    )
    item = listing(observed_at="2025-01-01T00:00:00Z")
    record = {
        "field": "commute_minutes",
        "value": {"destination": "人民广场", "minutes": 35},
        "source_url": "https://map.example/route/1",
        "observed_at": "2025-01-02T00:00:00Z",
        "verified_by": "manual-route-check",
    }
    assert match_listing(item, preferences, [record])["status"] == "NEEDS_EVIDENCE"
    assert (
        match_listing(item, preferences, [record], as_of=datetime(2025, 1, 3, tzinfo=UTC))["status"]
        == "MATCH"
    )
    assert item["observed_at"] == "2025-01-01T00:00:00Z"
    with pytest.raises(ValueError, match="timezone"):
        match_listing(item, preferences, [record], as_of=datetime(2025, 1, 3))

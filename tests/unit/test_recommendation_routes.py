from __future__ import annotations

import copy
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from scripts.browser_handoff import Handoff, create_app
from scripts.research_pipeline import ResearchPipeline
from tests.unit.test_browser_handoff import FakeBrowser
from tests.unit.test_research_pipeline import feed

ACTION = {"x-handoff-action": "1"}


@pytest.fixture
def workspace(tmp_path: Path):
    browser = FakeBrowser()
    pipeline = ResearchPipeline(tmp_path / "exports")
    handoff = Handoff(
        browser, tmp_path / "state", tmp_path / "exports", min_cards=1, analyzer=pipeline
    )
    handoff.latest = feed(8)
    source = tmp_path / "exports" / "latest.canonical.json"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text(json.dumps(handoff.latest))
    handoff.state["status"] = "READY"
    pipeline.run(handoff.latest)
    with TestClient(create_app(handoff)) as client:
        yield client, handoff, browser, source


def confirmed_profile() -> dict:
    return {
        "budget_min_wan": 250,
        "budget_max_wan": 300,
        "districts": ["徐汇"],
        "confirmed": True,
        "commute_not_required": True,
        "bedrooms_min": 2,
        "elevator_required": True,
    }


def property_evidence() -> dict:
    return {
        "listing_id": "S0",
        "field": "elevator",
        "value": True,
        "source_url": "https://example.com/property/confirmed",
        "observed_at": datetime.now(UTC).isoformat(),
        "verified_by": "测试核验人",
    }


def transaction() -> dict:
    return {
        "record_kind": "closed_transaction",
        "source_record_id": "closed-1",
        "district": "徐汇",
        "submarket": "长桥",
        "community": "测试小区",
        "area_sqm": 66.5,
        "price_wan": 298.0,
        "bedrooms": 2,
        "floor": "低层",
        "year_built": 1996,
        "source_url": "https://example.com/transactions/closed-1",
        "observed_at": datetime.now(UTC).isoformat(),
        "verified_by": "测试核验人",
        "sold_at": (datetime.now(UTC) - timedelta(days=10)).isoformat(),
    }


def test_profile_persists_without_assuming_user_confirmation(workspace) -> None:
    client, handoff, _, _ = workspace
    default = client.get("/buyer-profile").json()
    assert default["confirmed"] is False
    response = client.put("/buyer-profile", json=confirmed_profile(), headers=ACTION)
    assert response.status_code == 200, response.text
    saved = response.json()
    assert saved["completeness_missing"] == []
    assert client.get("/buyer-profile").json()["revision"] == saved["revision"]
    with TestClient(create_app(handoff)) as restored:
        assert restored.get("/buyer-profile").json()["revision"] == saved["revision"]
    recommendations = client.get("/recommendations").json()
    assert recommendations["summary"]["needs_evidence"] == 8
    assert recommendations["summary"]["recommended"] == 0


def test_local_actions_reject_missing_header_and_cross_origin(workspace) -> None:
    client, _, _, _ = workspace
    payloads = {
        "/buyer-profile": ("put", confirmed_profile()),
        "/property-evidence": ("post", property_evidence()),
        "/transactions/import": ("post", {"source_id": "provider", "records": [transaction()]}),
        "/validation-runs": (
            "post",
            {"name": "test", "reviewer": "human", "independent_review": True},
        ),
    }
    for path, (method, payload) in payloads.items():
        request = getattr(client, method)
        assert request(path, json=payload).status_code == 403
        assert (
            request(
                path, json=payload, headers={**ACTION, "origin": "https://other.example"}
            ).status_code
            == 403
        )
    assert client.get("/property-evidence").json()["items"] == []


def test_property_reanalysis_preserves_source_observation_and_price(workspace) -> None:
    client, handoff, browser, source = workspace
    original_feed = copy.deepcopy(handoff.latest)
    original_bytes = source.read_bytes()
    old_report = client.get("/analysis").json()
    client.put("/buyer-profile", json=confirmed_profile(), headers=ACTION)
    response = client.post("/property-evidence", json=property_evidence(), headers=ACTION)
    assert response.status_code == 200, response.text
    assert response.json()["analysis_status"] == "complete"
    report = client.get("/analysis").json()
    assert report["input_fingerprint"] != old_report["input_fingerprint"]
    assert report["observed_at"] == old_report["observed_at"]
    assert report["evidence_cutoff_at"] > report["observed_at"]
    assert handoff.latest == original_feed and source.read_bytes() == original_bytes
    assert browser.navigations == [] and browser.initializations == 0
    recommendation = client.get("/recommendations").json()
    target = next(row for row in recommendation["items"] if row["listing_id"] == "S0")
    assert target["fit"]["status"] == "MATCH"
    assert target["current_ask_wan"] == original_feed["items"][0]["price_wan"]
    assert target["personal_rank"] is None
    assert target["verified_evidence"][0]["source_url"] == "https://example.com/property/confirmed"
    assert recommendation["summary"]["recommended"] == 0


def test_closed_transaction_keeps_provider_identity_and_source_date(workspace) -> None:
    client, handoff, browser, source = workspace
    original_bytes = source.read_bytes()
    stamp = handoff.latest["items"][0]["observed_at"]
    record = transaction()
    response = client.post(
        "/transactions/import",
        headers=ACTION,
        json={"source_id": "official_test_provider", "records": [record]},
    )
    assert response.status_code == 200, response.text
    assert response.json()["imported"] == 1
    assert response.json()["analysis_status"] == "complete"
    report = client.get("/analysis").json()
    assert report["observed_at"] == stamp
    assert report["summary"]["transaction_records"] == 1
    comparables = [
        comp
        for row in report["items"]
        for comp in (row.get("valuation") or {}).get("comparables", [])
        if comp["observation_type"] == "transaction"
    ]
    assert comparables
    assert all(comp["source"] == "official_test_provider" for comp in comparables)
    assert all(comp["source_record_id"] == "closed-1" for comp in comparables)
    assert all(comp["url"] == record["source_url"] for comp in comparables)
    assert all(comp["observed_at"] == record["sold_at"] for comp in comparables)
    assert source.read_bytes() == original_bytes and browser.navigations == []
    assert client.get("/recommendations").json()["summary"]["recommended"] == 0


@pytest.mark.parametrize("kind", [None, "listing", "seller_indication", "asking_price"])
def test_transaction_import_rejects_ambiguous_listing_prices(workspace, kind) -> None:
    client, _, _, _ = workspace
    record = transaction()
    if kind is None:
        record.pop("record_kind")
    else:
        record["record_kind"] = kind
    response = client.post(
        "/transactions/import", headers=ACTION, json={"source_id": "provider", "records": [record]}
    )
    assert response.status_code == 422
    assert client.get("/analysis").json()["summary"]["transaction_records"] == 0


def test_validation_rejects_malformed_input_without_creating_labels(workspace) -> None:
    client, _, _, _ = workspace
    for body in (
        [],
        {"name": "test", "reviewer": "human", "independent_review": "yes"},
        {"name": "test", "reviewer": "human"},
    ):
        assert client.post("/validation-runs", headers=ACTION, json=body).status_code == 422
    response = client.post(
        "/validation-runs",
        headers=ACTION,
        json={"name": "test", "reviewer": "human", "independent_review": True},
    )
    assert response.status_code == 422 and "50" in response.text
    assert client.get("/validation-runs").json()["items"] == []
    assert (
        client.post(
            "/validation-runs/invalid/labels", headers=ACTION, json={"human_class": "LOW_QUALITY"}
        ).status_code
        == 422
    )
    assert client.get("/validation-runs/invalid").status_code == 422


def test_invalid_json_property_target_and_future_evidence_are_rejected(workspace) -> None:
    client, _, _, _ = workspace
    assert client.put("/buyer-profile", headers=ACTION, content="not json").status_code == 422
    value = property_evidence()
    value["listing_id"] = "not-in-source"
    assert client.post("/property-evidence", headers=ACTION, json=value).status_code == 422
    value = property_evidence()
    value["observed_at"] = (datetime.now(UTC) + timedelta(days=1)).isoformat()
    assert client.post("/property-evidence", headers=ACTION, json=value).status_code == 422
    assert client.get("/property-evidence").json()["items"] == []


def test_reviewed_market_facts_reach_actual_liquidity_score(workspace) -> None:
    from tests.unit.test_recommendation_market import payload, record

    client, handoff, browser, source = workspace
    original = source.read_bytes()
    report_before = client.get("/analysis").json()
    response = client.post("/market-evidence/import", json=payload(record()), headers=ACTION)
    assert response.status_code == 200, response.text
    report_after = client.get("/analysis").json()
    assert report_after["input_fingerprint"] != report_before["input_fingerprint"]
    assert report_after["observed_at"] == report_before["observed_at"]
    for row in report_after["items"]:
        if row["decision"]:
            assert float(row["decision"]["liquidity_score"]) == 100
            assert row["liquidity_evidence"]["evidence"]["coverage_complete"] is True
    assert source.read_bytes() == original
    assert handoff.latest["completeness"] == "partial"
    assert browser.navigations == []


def test_reviewed_future_factor_reaches_assessment_without_fake_calibration(workspace) -> None:
    from tests.unit.test_recommendation_future import payload

    client, _, browser, _ = workspace
    before = client.get("/analysis").json()
    response = client.post("/future-evidence/import", json=payload(), headers=ACTION)
    assert response.status_code == 200, response.text
    after = client.get("/analysis").json()
    assert after["input_fingerprint"] != before["input_fingerprint"]
    assert after["observed_at"] == before["observed_at"]
    for row in after["items"]:
        assert any(factor["factor"] == "rental_demand" for factor in row["external_evidence"])
    assert client.get("/recommendations").json()["readiness"]["validation_status"] == "uncalibrated"
    assert browser.navigations == []

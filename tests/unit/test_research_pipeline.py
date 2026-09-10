from __future__ import annotations

import copy
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from scripts.prepare_public_monitor_pilot import SOURCE_ID
from scripts.research_pipeline import ResearchPipeline, listing_uuid


def feed(count: int = 4) -> dict:
    stamp = (datetime.now(UTC) - timedelta(minutes=5)).isoformat()
    return {
        "schema_version": "1.0",
        "source_id": SOURCE_ID,
        "scope": {"city": "shanghai", "query": "private-monitor-pilot"},
        "completeness": "partial",
        "metadata": {"data_mode": "sample", "changes": []},
        "items": [
            {
                "listing_id": f"S{index}",
                "url": f"https://shanghai.anjuke.com/prop/view/S{index}",
                "district": "徐汇",
                "submarket": "长桥",
                "community": "测试小区",
                "area_sqm": 60 + index,
                "price_wan": 270 + index * 3,
                "bedrooms": 2,
                "living_rooms": 1,
                "floor": "中层",
                "total_floors": 6,
                "orientation": "南",
                "year_built": 1996,
                "observed_at": stamp,
            }
            for index in range(count)
        ],
    }


def test_real_engines_evaluate_without_fabricating_recommendations(tmp_path: Path) -> None:
    envelope = feed()
    report = ResearchPipeline(tmp_path, evidence_dir=tmp_path / "empty").run(envelope)
    assert report["status"] == "complete"
    assert report["summary"]["evaluated"] == 4
    assert report["summary"]["eligible"] == 0
    assert report["observed_at"] == envelope["items"][0]["observed_at"]
    for row in report["items"]:
        assert row["future"]["calibration_state"] == "uncalibrated"
        assert row["future"]["future_score"] is None
        assert row["decision"]["eligibility_status"] == "INSUFFICIENT"
        assert row["rank"] is None
        assert row["valuation"]["baseline_level_used"] == "none"
        assert row["valuation"]["comparable_count"] == 3
        for comparable in row["valuation"]["comparables"]:
            assert comparable["listing_id"] != str(listing_uuid(SOURCE_ID, row["listing_id"]))
            assert comparable["source"] == SOURCE_ID
            assert comparable["observation_type"] == "listing"
            assert comparable["url"].startswith("https://shanghai.anjuke.com/prop/view/")


def test_singleton_and_suspected_duplicate_do_not_supply_self_valuation(tmp_path: Path) -> None:
    envelope = feed(1)
    duplicate = {**envelope["items"][0], "listing_id": "Sdup"}
    envelope["items"].append(duplicate)
    report = ResearchPipeline(tmp_path, evidence_dir=tmp_path / "empty").run(envelope)
    assert report["summary"]["without_comparables"] == 2
    assert all(row["valuation"] is None for row in report["items"])


def test_reports_are_idempotent_and_new_feed_invalidates_cache(tmp_path: Path) -> None:
    pipeline = ResearchPipeline(tmp_path, evidence_dir=tmp_path / "empty")
    envelope = feed()
    first = pipeline.run(envelope)
    assert first == pipeline.run(envelope)
    changed = copy.deepcopy(envelope)
    changed["items"][0]["price_wan"] -= 10
    assert pipeline.read(changed) is None
    assert first["input_fingerprint"] != pipeline.run(changed)["input_fingerprint"]
    assert len(list((tmp_path / "analysis").glob("*.json"))) == 2


def test_corrupted_derived_report_is_rebuilt(tmp_path: Path) -> None:
    pipeline = ResearchPipeline(tmp_path, evidence_dir=tmp_path / "empty")
    envelope = feed()
    first = pipeline.run(envelope)
    (tmp_path / "analysis" / f"{pipeline.key(envelope)}.json").write_text("invalid")
    assert pipeline.run(envelope)["summary"] == first["summary"]


def test_histories_are_isolated_by_source_scope_and_cutoff(tmp_path: Path) -> None:
    envelope = feed()
    history = tmp_path / "observations"
    history.mkdir()
    old = copy.deepcopy(envelope)
    old["items"][0]["price_wan"] += 20
    old["items"][0]["observed_at"] = (datetime.now(UTC) - timedelta(days=1)).isoformat()
    (history / "old.canonical.json").write_text(json.dumps(old))
    foreign = copy.deepcopy(old)
    foreign["source_id"] = "sample_json"
    foreign["items"][0]["price_wan"] = 9999
    (history / "foreign.canonical.json").write_text(json.dumps(foreign))
    report = ResearchPipeline(tmp_path, evidence_dir=tmp_path / "empty").run(envelope)
    item = report["items"][0]
    assert item["valuation"]["price_history"]["price_cut_count"] == 1
    assert float(item["valuation"]["price_history"]["original_ask"]) == 2900000


@pytest.mark.parametrize(
    "change",
    [
        {"source_id": "sample_json"},
        {"completeness": "complete"},
        {"metadata": {"data_mode": "live"}},
        {"items": []},
    ],
)
def test_wrong_source_mode_and_coverage_are_rejected(tmp_path: Path, change: dict) -> None:
    with pytest.raises(ValueError):
        ResearchPipeline(tmp_path, evidence_dir=tmp_path / "empty").run({**feed(), **change})


def test_partial_baseline_excludes_target_and_keeps_confidence_cap(tmp_path: Path) -> None:
    envelope = feed(12)
    report = ResearchPipeline(tmp_path, evidence_dir=tmp_path / "empty").run(envelope)
    rows = [row for row in report["items"] if row["baseline_evidence"]]
    assert rows
    for row in rows:
        assert row["baseline_evidence"]["sample_count"] < len(envelope["items"])
        assert row["baseline_evidence"]["confidence"] == "low"
        assert row["baseline_evidence"]["liquidity_score"] is None
        assert row["valuation"]["baseline_level_used"] != "none"
    assert report["summary"]["eligible"] == 0


def test_public_evidence_cutoff_scope_and_synthetic_rejection(
    tmp_path: Path, district_statistics: dict[str, Any]
) -> None:
    import yaml

    from scripts.build_district_factor_evidence import build_observations
    from scripts.import_future_evidence import EvidenceValidationError
    from scripts.research_evidence import ResearchEvidence

    cutoff = datetime(2026, 9, 2, 9, 55, 56, tzinfo=UTC)
    document = json.loads(
        json.dumps(
            {
                "data_mode": "sample",
                "cutoff": cutoff.isoformat(),
                "factor_observations": build_observations(district_statistics, cutoff=cutoff),
            },
            default=str,
        )
    )
    path = tmp_path / "district_factors.yearbook_2022_2024.yaml"
    path.write_text(yaml.safe_dump(document))
    evidence = ResearchEvidence(tmp_path)
    now = datetime.now(UTC)
    factors, _ = evidence.future({"district": "徐汇"}, now)
    assert {factor.factor for factor in factors} == {"supply_scarcity", "buyer_pool_depth"}
    assert all(factor.metadata["evidence_kind"] == "regional_proxy" for factor in factors)
    assert evidence.future({"district": "不存在的区"}, now)[0] == ()
    assert evidence.future({"district": "徐汇"}, datetime(2020, 1, 1, tzinfo=UTC))[0] == ()
    before = evidence.fingerprint()
    document["factor_observations"][0]["provenance"]["synthetic"] = True
    path.write_text(yaml.safe_dump(document))
    assert evidence.fingerprint() != before
    with pytest.raises(EvidenceValidationError, match="synthetic"):
        evidence.future({"district": "徐汇"}, now)


def test_baseline_ignores_observations_outside_window(tmp_path: Path) -> None:
    from dataclasses import replace

    pipeline = ResearchPipeline(tmp_path, evidence_dir=tmp_path / "empty")
    envelope = feed(12)
    target, *peers = [pipeline.comparable(item) for item in envelope["items"]]
    now = datetime.now(UTC)
    peers = [replace(peer, observed_at=now - timedelta(days=31)) for peer in peers]
    assert pipeline.baseline(target, peers, now) is None

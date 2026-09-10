from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from scripts.browser_validation import BrowserValidationStore, _hash


def inputs(count: int = 61, ranked: int = 0) -> tuple[dict, dict]:
    feed = {
        "source_id": "anjuke_public_research_pilot",
        "metadata": {"data_mode": "sample"},
        "items": [
            {
                "listing_id": f"source-{i:03}",
                "community": f"真实小区{i}",
                "district": "徐汇",
                "bedrooms": 2,
                "floor": "高层",
                "orientation": "南",
                "area_sqm": 70,
                "price_wan": 280,
                "observed_at": "2026-09-08T07:00:00+00:00",
            }
            for i in range(count)
        ],
    }
    report = {
        "status": "complete",
        "feed_fingerprint": _hash(feed),
        "input_fingerprint": "model-and-evidence-fingerprint",
        "profile_fingerprint": "profile-v1",
        "items": [
            {
                "listing_id": item["listing_id"],
                "rank": i + 1 if i < ranked else None,
                "value_score": 99,
                "decision": {
                    "opportunity_classification": "QUALITY_AT_DISCOUNT",
                    "workflow_state": "VIEW",
                    "eligibility_status": "ELIGIBLE",
                    "valuation_confidence": "high",
                    "future_confidence": "high",
                },
            }
            for i, item in enumerate(feed["items"])
        ],
    }
    return feed, report


@pytest.fixture
def store(tmp_path: Path) -> BrowserValidationStore:
    return BrowserValidationStore(tmp_path / "validation")


def create(store: BrowserValidationStore, count: int = 61, ranked: int = 0) -> dict:
    feed, report = inputs(count, ranked)
    return store.create(feed, report, "徐汇盲评", "独立评审甲", True)


def complete(store: BrowserValidationStore, batch: dict) -> dict:
    for item in batch["items"]:
        store.label(
            batch["batch_id"],
            item["listing_id"],
            {
                "human_class": "QUALITY_AT_DISCOUNT",
                "human_workflow": "VIEW",
                "human_would_visit": True,
                "notes": "经实地核验后填写的测试标签",
            },
        )
    return store.freeze(batch["batch_id"])


def test_blind_batch_is_deterministic_and_model_independent(store: BrowserValidationStore) -> None:
    feed, report = inputs()
    first = store.create(feed, report, "first", "reviewer", True)
    changed = copy.deepcopy(report)
    changed["items"].reverse()
    for index, row in enumerate(changed["items"]):
        row.update(rank=index + 1, value_score=2)
    second = store.create(feed, changed, "second", "reviewer", True)
    assert len(first["items"]) == 50
    assert [r["listing_id"] for r in first["items"]] == [r["listing_id"] for r in second["items"]]
    assert all(r["label"] is None for r in first["items"])
    public = json.dumps(first)
    assert "value_score" not in public and '"rank"' not in public and '"model"' not in public
    assert first["formal_gate_eligible"] is False
    assert len(store.list()) == 2
    assert "items" not in store.list()[0]


def test_dedup_never_pads_to_fifty(store: BrowserValidationStore) -> None:
    feed, report = inputs(50)
    feed["items"][1] = {**feed["items"][0], "listing_id": "source-001", "area_sqm": 70.2}
    report["feed_fingerprint"] = _hash(feed)
    with pytest.raises(ValueError, match="only 49"):
        store.create(feed, report, "dedup", "reviewer", True)
    with pytest.raises(ValueError, match="only 49"):
        create(store, 49)


def test_mismatched_analysis_and_duplicate_ids_rejected(store: BrowserValidationStore) -> None:
    feed, report = inputs()
    feed["items"][0]["price_wan"] = 270
    with pytest.raises(ValueError, match="exact source"):
        store.create(feed, report, "changed", "reviewer", True)
    report["feed_fingerprint"] = _hash(feed)
    report["items"].append(report["items"][0])
    with pytest.raises(ValueError, match="duplicate"):
        store.create(feed, report, "changed", "reviewer", True)


def test_no_early_reveal_or_incomplete_freeze(store: BrowserValidationStore) -> None:
    batch = create(store)
    with pytest.raises(ValueError, match="all 50"):
        store.freeze(batch["batch_id"])
    with pytest.raises(ValueError, match="freeze"):
        store.reveal(batch["batch_id"])
    assert store.read(batch["batch_id"])["status"] == "LABELING"


def test_freeze_is_immutable_and_reveal_never_unlocks_production(
    store: BrowserValidationStore,
) -> None:
    batch = create(store, ranked=61)
    frozen = complete(store, batch)
    assert frozen["status"] == "FROZEN"
    assert "model" not in frozen["items"][0] and "comparison" not in frozen
    with pytest.raises(ValueError, match="frozen"):
        store.label(
            batch["batch_id"],
            batch["items"][0]["listing_id"],
            {
                "human_class": "LOW_QUALITY",
                "human_workflow": "PASS",
                "human_would_visit": False,
                "notes": "changed after freezing",
            },
        )
    revealed = store.reveal(batch["batch_id"])
    assert revealed["status"] == "REVEALED"
    assert revealed["comparison"]["metrics"]["precision_at_5"] == "1.0000"
    assert revealed["comparison"]["formal_gate_eligible"] is False
    assert revealed["comparison"]["research_checks_passed"] is False
    assert revealed["comparison"]["checks"]["why_ranked_major_contradiction_rate"] is False
    assert store.reveal(batch["batch_id"]) == revealed


def test_one_ranked_case_cannot_pass_top_five_or_ten(store: BrowserValidationStore) -> None:
    batch = create(store, count=50, ranked=1)
    complete(store, batch)
    comparison = store.reveal(batch["batch_id"])["comparison"]
    assert comparison["metrics"]["ranked_count"] == 1
    for key in ("precision_at_5", "precision_at_10", "human_acceptance_at_10"):
        assert comparison["metrics"][key] is None
        assert comparison["checks"][key] is False
    assert comparison["checks"]["regret_at_5"] is False
    assert comparison["checks"]["value_traps_at_10"] is False


def test_input_snapshots_and_audit_detect_mutation(store: BrowserValidationStore) -> None:
    feed, report = inputs()
    batch = store.create(feed, report, "frozen inputs", "reviewer", True)
    report["profile_fingerprint"] = "mutated"
    path = store.directory / f"{batch['batch_id']}.json"
    saved = json.loads(path.read_text())
    assert saved["inputs"]["report"]["profile_fingerprint"] == "profile-v1"
    saved["inputs"]["report"]["profile_fingerprint"] = "tampered"
    path.write_text(json.dumps(saved))
    with pytest.raises(ValueError, match="integrity"):
        store.read(batch["batch_id"])


def test_public_profile_is_frozen_and_cannot_leak_model_fields(
    store: BrowserValidationStore,
) -> None:
    feed, report = inputs()
    profile = {
        "budget_max_wan": 300,
        "districts": ["徐汇"],
        "revision": "v1",
        "commute_destination": "人民广场",
        "personal_rank": 1,
        "value_score": 99,
    }
    report["profile"] = profile
    batch = store.create(feed, report, "profile snapshot", "reviewer", True)
    profile.update(budget_max_wan=400, revision="v2")
    public = store.read(batch["batch_id"])["buyer_profile"]
    assert public["budget_max_wan"] == 300 and public["revision"] == "v1"
    assert public["commute_destination"] == "人民广场"
    assert "personal_rank" not in public and "value_score" not in public


def test_practice_mode_and_strict_human_input(store: BrowserValidationStore) -> None:
    feed, report = inputs()
    batch = store.create(feed, report, "practice", "reviewer", False)
    assert batch["mode"] == "practice" and batch["independent_review"] is False
    with pytest.raises(ValueError, match="explicit boolean"):
        store.create(feed, report, "practice", "reviewer", "yes")
    with pytest.raises(ValueError, match="boolean"):
        store.label(
            batch["batch_id"],
            batch["items"][0]["listing_id"],
            {
                "human_class": "VALUE_TRAP",
                "human_workflow": "PASS",
                "human_would_visit": "false",
                "notes": "",
            },
        )
    assert store.read(batch["batch_id"])["labeled_count"] == 0


def test_invalid_ids_and_missing_files(store: BrowserValidationStore) -> None:
    with pytest.raises(ValueError):
        store.read("../secrets")
    with pytest.raises(FileNotFoundError):
        store.read("00000000-0000-0000-0000-000000000000")


def test_config_is_frozen_and_labels_are_audited(store: BrowserValidationStore) -> None:
    batch = create(store)
    listing_id = batch["items"][0]["listing_id"]
    payload = {
        "human_class": "INSUFFICIENT_INFORMATION",
        "human_workflow": "WATCH",
        "human_would_visit": False,
        "notes": "成交证据尚未提供",
    }
    store.label(batch["batch_id"], listing_id, payload)
    path = store.directory / f"{batch['batch_id']}.json"
    saved = json.loads(path.read_text())
    assert saved["inputs"]["config"]["gate"]["precision_at_5"] == "0.8"
    assert saved["audit"][-1]["input"] == {"listing_id": listing_id, **payload}
    assert path.stat().st_mode & 0o777 == 0o600


def reviewed(store: BrowserValidationStore, batch: dict) -> dict:
    store.reveal(batch["batch_id"])
    result = batch
    for item in batch["items"]:
        result = store.review(
            batch["batch_id"],
            item["listing_id"],
            {
                "why_ranked_verdict": "AGREE",
                "root_cause": "UNKNOWN",
                "notes": "独立复核模型理由",
            },
        )
    return result


def scoped_inputs(prefix: str) -> tuple[dict, dict]:
    from datetime import UTC, datetime

    source, model = inputs(50, ranked=50)
    source["scope"] = {"city": "shanghai", "query": "xuhui-250-300"}
    for listing, row in zip(source["items"], model["items"], strict=True):
        listing["listing_id"] = prefix + listing["listing_id"]
        listing["community"] = prefix + listing["community"]
        listing["observed_at"] = datetime.now(UTC).isoformat()
        row["listing_id"] = listing["listing_id"]
    model.update(
        feed_fingerprint=_hash(source),
        source_id=source["source_id"],
        source_scope=source["scope"],
        configuration_fingerprint="fixed-model-v1",
    )
    return source, model


def qualifying_batch(store: BrowserValidationStore, source: dict, model: dict) -> dict:
    batch = store.create(source, model, "qualification", "independent reviewer", True)
    complete(store, batch)
    return reviewed(store, batch)


def test_reason_review_requires_reveal_and_preserves_frozen_labels(
    store: BrowserValidationStore,
) -> None:
    batch = create(store, 50, 50)
    payload = {"why_ranked_verdict": "AGREE", "root_cause": "UNKNOWN", "notes": ""}
    with pytest.raises(ValueError, match="reveal"):
        store.review(batch["batch_id"], batch["items"][0]["listing_id"], payload)
    complete(store, batch)
    store.reveal(batch["batch_id"])
    partial = store.review(batch["batch_id"], batch["items"][0]["listing_id"], payload)
    assert partial["comparison"]["research_checks_passed"] is False
    assert partial["comparison"]["checks"]["all_cases_reviewed"] is False
    result = reviewed(store, batch)
    assert result["comparison"]["research_checks_passed"] is True
    assert result["comparison"]["metrics"]["why_ranked_major_contradiction_rate"] == "0.0000"
    assert result["reviewed_count"] == 50 and result["formal_gate_eligible"] is False
    assert result["items"][0]["label"]["human_class"] == "QUALITY_AT_DISCOUNT"
    assert result["items"][0]["review"]["why_ranked_verdict"] == "AGREE"
    with pytest.raises(ValueError, match="frozen"):
        store.label(batch["batch_id"], batch["items"][0]["listing_id"], result["items"][0]["label"])


def test_qualification_requires_two_fresh_disjoint_batches_and_matching_fingerprints(
    store: BrowserValidationStore,
) -> None:
    source, model = scoped_inputs("first-")
    profile = {"revision": model["profile_fingerprint"]}
    assert store.qualification(model, profile)["status"] == "uncalibrated"
    first = qualifying_batch(store, source, model)
    assert store.qualification(model, profile)["status"] == "awaiting_holdout"
    next_source, next_model = scoped_inputs("holdout-")
    second = qualifying_batch(store, next_source, next_model)
    qualification = store.qualification(next_model, profile)
    assert qualification["status"] == "validated_scoped"
    assert qualification["holdout_batch_ids"] == [first["batch_id"], second["batch_id"]]
    assert qualification["formal_gate_eligible"] is False
    assert (
        store.qualification({**next_model, "configuration_fingerprint": "new-model"}, profile)[
            "status"
        ]
        == "uncalibrated"
    )
    assert store.qualification(next_model, {"revision": "new-profile"})["status"] == "uncalibrated"
    assert (
        store.qualification({**next_model, "source_scope": {"query": "another"}}, profile)["status"]
        == "uncalibrated"
    )


def test_repeated_ids_or_same_homes_cannot_be_independent_holdout(
    store: BrowserValidationStore,
) -> None:
    source, model = scoped_inputs("same-")
    qualifying_batch(store, source, model)
    duplicate_source, duplicate_model = scoped_inputs("same-")
    qualifying_batch(store, duplicate_source, duplicate_model)
    profile = {"revision": model["profile_fingerprint"]}
    assert store.qualification(model, profile)["status"] == "awaiting_holdout"
    duplicate_source, duplicate_model = scoped_inputs("different-ad-")
    for item, original in zip(duplicate_source["items"], source["items"], strict=True):
        item["community"] = original["community"]
    duplicate_model["feed_fingerprint"] = _hash(duplicate_source)
    qualifying_batch(store, duplicate_source, duplicate_model)
    assert store.qualification(model, profile)["status"] == "awaiting_holdout"


def test_disjoint_but_preexisting_observations_are_not_fresh_holdout(
    store: BrowserValidationStore,
) -> None:
    source, model = scoped_inputs("calibration-")
    held_source, held_model = scoped_inputs("preexisting-")
    qualifying_batch(store, source, model)
    qualifying_batch(store, held_source, held_model)
    assert (
        store.qualification(model, {"revision": model["profile_fingerprint"]})["status"]
        == "awaiting_holdout"
    )

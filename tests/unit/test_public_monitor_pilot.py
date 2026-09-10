from __future__ import annotations

import copy
from typing import Any

import pytest

from scripts.prepare_public_monitor_pilot import prepare


def raw_record() -> dict[str, Any]:
    return {
        "raw_text": (
            "测试房源 2 室 1 厅 1 卫 60㎡ 南 中层(共6层) 1901年建造 "
            "测试小区 徐汇长桥测试路 满五年 280 万 46667元/㎡"
        ),
        "source_url": "https://shanghai.anjuke.com/prop/view/S123?tracking=abc",
        "district_expected": "徐汇",
        "search_page": "https://shanghai.anjuke.com/sale/xuhui/m13473/",
        "observed_at": "2026-09-05T01:00:00Z",
    }


def test_feed_is_partial_private_separate_and_preserves_evidence() -> None:
    envelope, changes = prepare([raw_record()], {"items": []})
    assert envelope["completeness"] == "partial"
    assert envelope["source_id"] != "sample_json"
    assert envelope["metadata"]["data_mode"] == "sample"
    assert envelope["metadata"]["unattended_monitoring_enabled"] is False
    item = envelope["items"][0]
    assert item["year_built"] is None
    assert item["listing_date"] is None
    assert item["elevator"] is None
    assert item["observed_at"] == raw_record()["observed_at"]
    assert item["url"].endswith("/S123")
    assert changes[0]["event"] == "FIRST_SEEN_IN_PILOT"
    assert "validation_role" not in item


def test_price_drop_and_other_evidence_changes_are_both_preserved() -> None:
    envelope, _ = prepare([raw_record()], {"items": []})
    old = copy.deepcopy(envelope)
    old["items"][0].update(price_wan=300, year_built=1990, observed_at="2026-09-02T00:00:00Z")
    _, changes = prepare([raw_record()], old)
    assert changes[0]["event"] == "ASKING_PRICE_DECREASED"
    assert changes[0]["delta_wan"] == "-20.0"
    assert "year_built" in changes[0]["identity_changes"]
    assert old["items"][0]["price_wan"] == 300


def test_missing_records_are_not_reported_as_delisted() -> None:
    envelope, _ = prepare([raw_record()], {"items": []})
    envelope["items"][0]["listing_id"] = "S456"
    _, changes = prepare([raw_record()], envelope)
    assert len(changes) == 1
    assert changes[0]["possible_duplicate_ids"] == ["S456"]


def test_empty_or_duplicate_observation_is_rejected() -> None:
    with pytest.raises(ValueError, match="empty"):
        prepare([], {"items": []})
    with pytest.raises(ValueError, match="duplicate"):
        prepare([raw_record(), raw_record()], {"items": []})


@pytest.mark.parametrize("timestamp", ["2026-09-05T00:00:00", "not-a-date"])
def test_missing_timezone_or_invalid_date_is_rejected(timestamp: str) -> None:
    record = raw_record()
    record["observed_at"] = timestamp
    with pytest.raises(ValueError):
        prepare([record], {"items": []})


def test_stale_replay_is_not_called_new_data() -> None:
    envelope, _ = prepare([raw_record()], {"items": []})
    with pytest.raises(ValueError, match="not newer"):
        prepare([raw_record()], envelope)


def test_wrong_source_rejected() -> None:
    record = raw_record()
    record["source_url"] = "https://example.com/prop/view/S123"
    with pytest.raises(ValueError, match="unexpected"):
        prepare([record], {"items": []})

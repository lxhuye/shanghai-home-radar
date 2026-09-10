from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import pytest
import yaml

from scripts import build_transport_evidence as builder
from scripts import import_future_evidence as importer

CUTOFF = datetime(2026, 9, 2, 9, 55, 56, tzinfo=UTC)


def _raw(*elements: dict[str, Any]) -> dict[str, Any]:
    return {
        "builder_version": builder.BUILDER_VERSION,
        "query": builder.overpass_query(CUTOFF),
        "query_sha256": "abc",
        "snapshot_at": CUTOFF.isoformat(),
        "retrieved_at": "2026-09-03T06:00:00+00:00",
        "elements": list(elements),
    }


def _node(node_id: int, name: str, *, timestamp: str, **tags: str) -> dict[str, Any]:
    return {
        "type": "node",
        "id": node_id,
        "lon": 121.43,
        "lat": 31.19,
        "timestamp": timestamp,
        "version": 7,
        "tags": {"railway": "station", "station": "subway", "name": name, **tags},
    }


def test_build_projects_keeps_pre_cutoff_nodes_with_osm_provenance() -> None:
    raw = _raw(
        _node(1, "徐家汇", timestamp="2024-05-01T00:00:00Z", line="1;9;11"),
        _node(2, "未来站", timestamp="2026-09-03T00:00:00Z"),
        {"type": "way", "id": 3, "tags": {"name": "ignored"}},
        {"type": "node", "id": 4, "lon": 121.0, "lat": 31.0, "timestamp": "2020-01-01T00:00:00Z"},
    )

    projects, summary = builder.build_projects(raw, cutoff=CUTOFF)

    assert summary["projects"] == 1
    assert [d["reason"] for d in summary["dropped"]] == [
        "edited after cutoff (2026-09-03)",
        "missing name or position",
    ]
    project = projects[0]
    assert project["name"] == "徐家汇站（1;9;11）"
    assert project["status"] == "current"
    assert project["source_record_id"] == "node/1"
    assert project["provenance"]["url"] == "https://www.openstreetmap.org/node/1"
    assert project["provenance"]["license"] == "ODbL 1.0"
    assert project["provenance"]["published_at"] == "2024-05-01T00:00:00+00:00"


def test_overpass_query_uses_the_frozen_historical_snapshot() -> None:
    query = builder.overpass_query(CUTOFF)

    assert '[date:"2026-09-02T09:55:56Z"]' in query


def test_fetch_retries_a_busy_overpass_response(monkeypatch: pytest.MonkeyPatch) -> None:
    responses = [
        httpx.Response(504, text="dispatcher busy"),
        httpx.Response(200, json={"osm3s": {}, "elements": []}),
    ]
    sleeps: list[float] = []

    def fake_post(*args: Any, **kwargs: Any) -> httpx.Response:
        return responses.pop(0)

    monkeypatch.setattr(httpx, "post", fake_post)
    monkeypatch.setattr(builder.time, "sleep", sleeps.append)

    result = builder.fetch_overpass(
        builder.overpass_query(CUTOFF),
        snapshot_at=CUTOFF,
        max_attempts=2,
        backoff_seconds=0.25,
    )

    assert result["elements"] == []
    assert sleeps == [0.25]


def test_fetch_reports_last_overpass_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_post(*args: Any, **kwargs: Any) -> httpx.Response:
        return httpx.Response(406, text="upstream rejected query")

    monkeypatch.setattr(httpx, "post", fake_post)
    monkeypatch.setattr(builder.time, "sleep", lambda _: None)

    with pytest.raises(ValueError, match="failed after 2 attempts.*upstream rejected query"):
        builder.fetch_overpass(builder.overpass_query(CUTOFF), snapshot_at=CUTOFF, max_attempts=2)


def test_build_rejects_a_raw_snapshot_from_another_cutoff() -> None:
    raw = _raw(_node(1, "徐家汇", timestamp="2024-05-01T00:00:00Z"))
    raw["snapshot_at"] = "2026-09-01T00:00:00Z"

    try:
        builder.build_projects(raw, cutoff=CUTOFF)
    except ValueError as exc:
        assert "does not match cutoff" in str(exc)
    else:
        raise AssertionError("mismatched historical snapshot was accepted")


def test_build_output_is_accepted_by_the_evidence_importer(tmp_path: Path) -> None:
    raw_path = tmp_path / "raw.json"
    raw_path.write_text(
        json.dumps(_raw(_node(10, "康健路", timestamp="2023-03-03T00:00:00Z"))), encoding="utf-8"
    )
    output = tmp_path / "transport.yaml"

    code = builder.main(
        [
            "build",
            "--raw",
            str(raw_path),
            "--cutoff",
            "2026-09-02T09:55:56Z",
            "--output",
            str(output),
        ]
    )

    assert code == 0
    document = yaml.safe_load(output.read_text(encoding="utf-8"))
    assert document["data_mode"] == "sample"
    evidence = importer.load_evidence_file(output, cutoff=CUTOFF)
    assert [r.table for r in evidence.records] == ["future_project"]
    values = evidence.records[0].values
    assert values["coordinates"] == "POINT(121.43 31.19)"
    assert values["provenance"]["synthetic"] is False

from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
import yaml

from scripts import import_future_evidence as importer

CUTOFF = datetime(2026, 9, 2, 9, 55, 56, tzinfo=UTC)

PUBLIC_PROVENANCE = {
    "url": "https://example.gov.cn/notice/2024-01",
    "publisher": "上海市规划和自然资源局",
    "published_at": "2024-01-15T00:00:00+08:00",
    "retrieved_at": "2026-09-03T10:00:00+08:00",
    "excerpt": "公开文件摘录",
}


def _center(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "name": "徐家汇",
        "category": "commercial",
        "coordinates": {"lon": 121.4372, "lat": 31.1946},
        "current_employment_weight": 1.0,
        "future_employment_weight": 1.1,
        "effective_from": "2018-01-04T00:00:00+08:00",
        "source_timestamp": "2018-01-04T00:00:00+08:00",
        "source": "shanghai-master-plan-2035",
        "source_record_id": "center-xujiahui",
        "confidence": 0.8,
        "provenance": dict(PUBLIC_PROVENANCE),
    }
    base.update(overrides)
    return base


def _project(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "name": "轨道交通示例线",
        "project_type": "transport",
        "status": "under_construction",
        "coordinates": {"lon": 121.40, "lat": 31.25},
        "district": "普陀",
        "expected_completion": "2027-12-31",
        "effective_from": "2021-06-01T00:00:00+08:00",
        "source_date": "2021-06-01T00:00:00+08:00",
        "source": "shanghai-metro-official",
        "source_record_id": "line-example",
        "confidence": 0.85,
        "provenance": dict(PUBLIC_PROVENANCE),
    }
    base.update(overrides)
    return base


def _observation(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "factor": "supply_scarcity",
        "scope_type": "district",
        "district": "徐汇",
        "current_score": 60,
        "future_score": 62,
        "observed_at": "2025-12-31T00:00:00+08:00",
        "source": "shanghai-statistical-yearbook",
        "source_record_id": "xuhui-supply-2025",
        "confidence": 0.6,
        "explanation": "Derived from published district housing completion data.",
        "provenance": dict(PUBLIC_PROVENANCE),
    }
    base.update(overrides)
    return base


def _write(tmp_path: Path, document: Mapping[str, Any]) -> Path:
    path = tmp_path / "evidence.yaml"
    path.write_text(yaml.safe_dump(dict(document), allow_unicode=True), encoding="utf-8")
    return path


def _document(**sections: Any) -> dict[str, Any]:
    return {"data_mode": "sample", **sections}


def test_loads_all_three_record_types(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        _document(
            employment_centers=[_center()],
            future_projects=[_project()],
            factor_observations=[_observation()],
        ),
    )

    evidence = importer.load_evidence_file(path, cutoff=CUTOFF)

    tables = [record.table for record in evidence.records]
    assert tables == ["employment_center", "future_project", "future_factor_observation"]
    center = evidence.records[0].values
    assert center["coordinates"] == "POINT(121.4372 31.1946)"
    assert center["effective_from"] == datetime(2018, 1, 3, 16, 0, tzinfo=UTC)
    assert center["confidence"] == Decimal("0.8")
    assert center["provenance"]["synthetic"] is False
    assert evidence.sha256 == importer.hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.mark.parametrize(
    ("section", "record", "field"),
    [
        ("employment_centers", _center, "effective_from"),
        ("employment_centers", _center, "source_timestamp"),
        ("future_projects", _project, "source_date"),
        ("factor_observations", _observation, "observed_at"),
    ],
)
def test_rejects_evidence_dated_after_cutoff(
    tmp_path: Path, section: str, record: Any, field: str
) -> None:
    path = _write(tmp_path, _document(**{section: [record(**{field: "2026-09-03T00:00:00Z"})]}))

    with pytest.raises(importer.EvidenceValidationError, match="after cutoff"):
        importer.load_evidence_file(path, cutoff=CUTOFF)


def test_rejects_naive_timestamps(tmp_path: Path) -> None:
    path = _write(tmp_path, _document(employment_centers=[_center(effective_from="2018-01-04")]))

    with pytest.raises(importer.EvidenceValidationError, match="timezone"):
        importer.load_evidence_file(path, cutoff=CUTOFF)


def test_sample_mode_rejects_synthetic_evidence(tmp_path: Path) -> None:
    provenance = {**PUBLIC_PROVENANCE, "synthetic": True}
    path = _write(tmp_path, _document(employment_centers=[_center(provenance=provenance)]))

    with pytest.raises(importer.EvidenceValidationError, match="only allowed in demo"):
        importer.load_evidence_file(path, cutoff=CUTOFF)


def test_sample_mode_requires_public_provenance(tmp_path: Path) -> None:
    provenance = {"notes": "no url"}
    path = _write(tmp_path, _document(employment_centers=[_center(provenance=provenance)]))

    with pytest.raises(importer.EvidenceValidationError, match="public provenance"):
        importer.load_evidence_file(path, cutoff=CUTOFF)


def test_rejects_provenance_published_after_cutoff(tmp_path: Path) -> None:
    provenance = {**PUBLIC_PROVENANCE, "published_at": "2026-09-03T00:00:00Z"}
    path = _write(tmp_path, _document(employment_centers=[_center(provenance=provenance)]))

    with pytest.raises(importer.EvidenceValidationError, match="after cutoff"):
        importer.load_evidence_file(path, cutoff=CUTOFF)


def test_rejects_retrieval_before_publication(tmp_path: Path) -> None:
    provenance = {
        **PUBLIC_PROVENANCE,
        "published_at": "2024-01-15T00:00:00Z",
        "retrieved_at": "2024-01-14T00:00:00Z",
    }
    path = _write(tmp_path, _document(employment_centers=[_center(provenance=provenance)]))

    with pytest.raises(importer.EvidenceValidationError, match="must not precede"):
        importer.load_evidence_file(path, cutoff=CUTOFF)


def test_rejects_reserved_import_provenance(tmp_path: Path) -> None:
    provenance = {**PUBLIC_PROVENANCE, "import": {"git_commit": "forged"}}
    path = _write(tmp_path, _document(employment_centers=[_center(provenance=provenance)]))

    with pytest.raises(importer.EvidenceValidationError, match="reserved"):
        importer.load_evidence_file(path, cutoff=CUTOFF)


def test_demo_mode_accepts_synthetic_evidence(tmp_path: Path) -> None:
    document = _document(employment_centers=[_center(provenance={"synthetic": True})])
    document["data_mode"] = "demo"
    path = _write(tmp_path, document)

    evidence = importer.load_evidence_file(path, cutoff=CUTOFF)

    assert evidence.records[0].values["provenance"] == {"synthetic": True}


def test_rejects_duplicate_natural_keys_inside_file(tmp_path: Path) -> None:
    path = _write(tmp_path, _document(employment_centers=[_center(), _center(name="重复")]))

    with pytest.raises(importer.EvidenceValidationError, match="duplicates"):
        importer.load_evidence_file(path, cutoff=CUTOFF)


def test_rejects_declared_cutoff_mismatch(tmp_path: Path) -> None:
    document = _document(employment_centers=[_center()])
    document["cutoff"] = "2026-09-01T00:00:00Z"
    path = _write(tmp_path, document)

    with pytest.raises(importer.EvidenceValidationError, match="declares cutoff"):
        importer.load_evidence_file(path, cutoff=CUTOFF)


def test_rejects_unknown_factor_and_scope_without_district(tmp_path: Path) -> None:
    path = _write(tmp_path, _document(factor_observations=[_observation(factor="vibes")]))
    with pytest.raises(importer.EvidenceValidationError, match="factor must be"):
        importer.load_evidence_file(path, cutoff=CUTOFF)


def test_rejects_invalid_listing_scope_uuid(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        _document(
            factor_observations=[
                _observation(
                    scope_type="listing",
                    district="徐汇",
                    submarket="康健",
                    community="寿山坊",
                    listing_id="not-a-uuid",
                )
            ]
        ),
    )

    with pytest.raises(importer.EvidenceValidationError, match="listing_id must be a UUID"):
        importer.load_evidence_file(path, cutoff=CUTOFF)

    path = _write(tmp_path, _document(factor_observations=[_observation(district=None)]))
    with pytest.raises(importer.EvidenceValidationError, match="requires district"):
        importer.load_evidence_file(path, cutoff=CUTOFF)


def test_transport_project_requires_coordinates(tmp_path: Path) -> None:
    path = _write(tmp_path, _document(future_projects=[_project(coordinates=None)]))

    with pytest.raises(importer.EvidenceValidationError, match="coordinates are required"):
        importer.load_evidence_file(path, cutoff=CUTOFF)


def test_plan_is_idempotent_and_detects_conflicts(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        _document(
            employment_centers=[
                _center(),
                _center(source_record_id="center-lujiazui", name="陆家嘴"),
            ],
            factor_observations=[_observation()],
        ),
    )
    evidence = importer.load_evidence_file(path, cutoff=CUTOFF)
    existing_center = dict(evidence.records[0].values)
    conflicting_observation = {**evidence.records[2].values, "future_score": Decimal("99")}

    def lookup(record: importer.EvidenceRecord) -> Mapping[str, Any] | None:
        if record.key == evidence.records[0].key:
            return existing_center
        if record.key == evidence.records[2].key:
            return conflicting_observation
        return None

    plan = importer.build_plan(evidence, cutoff=CUTOFF, lookup_existing=lookup)

    assert [record.label for record in plan.skipped] == [evidence.records[0].label]
    assert [record.label for record in plan.inserts] == [evidence.records[1].label]
    assert [(record.label, fields) for record, fields in plan.conflicts] == [
        (evidence.records[2].label, ["future_score"])
    ]
    summary = plan.summary()
    assert summary["inserts"] == {
        "employment_center": 1,
        "future_project": 0,
        "future_factor_observation": 0,
    }
    assert summary["skipped_identical"]["employment_center"] == 1
    assert summary["conflicts"][0]["fields"] == ["future_score"]


def test_plan_treats_equivalent_decimal_and_wkt_as_identical(tmp_path: Path) -> None:
    path = _write(tmp_path, _document(employment_centers=[_center()]))
    evidence = importer.load_evidence_file(path, cutoff=CUTOFF)
    stored = {
        **evidence.records[0].values,
        "confidence": Decimal("0.800000"),
        "current_employment_weight": Decimal("1.000000"),
        "coordinates": "POINT(121.4372 31.1946)",
    }

    plan = importer.build_plan(evidence, cutoff=CUTOFF, lookup_existing=lambda _record: stored)

    assert plan.inserts == []
    assert plan.conflicts == []
    assert len(plan.skipped) == 1


@pytest.mark.parametrize(
    ("field", "replacement"),
    [
        ("provenance", {**PUBLIC_PROVENANCE, "excerpt": "changed evidence"}),
        ("observation_metadata", {"formula": "changed"}),
    ],
)
def test_plan_detects_audit_evidence_changes(
    tmp_path: Path, field: str, replacement: dict[str, Any]
) -> None:
    path = _write(
        tmp_path,
        _document(factor_observations=[_observation(metadata={"formula": "original"})]),
    )
    evidence = importer.load_evidence_file(path, cutoff=CUTOFF)
    stored = dict(evidence.records[0].values)
    stored["provenance"] = {
        **stored["provenance"],
        "import": {"evidence_file_sha256": "previous"},
    }
    stored[field] = replacement

    plan = importer.build_plan(evidence, cutoff=CUTOFF, lookup_existing=lambda _record: stored)

    assert plan.conflicts[0][1] == [field]


def test_git_commit_uses_frozen_environment_value(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SHR_SOURCE_COMMIT", "frozen-commit")

    assert importer.git_commit() == "frozen-commit"


def test_validate_only_cli_writes_manifest_without_database(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = _write(tmp_path, _document(employment_centers=[_center()]))
    manifest = tmp_path / "out" / "manifest.json"

    code = importer.main(
        [
            str(path),
            "--cutoff",
            "2026-09-02T09:55:56Z",
            "--validate-only",
            "--manifest",
            str(manifest),
        ]
    )

    assert code == 0
    written = json.loads(manifest.read_text(encoding="utf-8"))
    assert written["status"] == "valid"
    assert written["records"] == {
        "employment_center": 1,
        "future_project": 0,
        "future_factor_observation": 0,
    }
    assert (
        json.loads(capsys.readouterr().out)["evidence_file_sha256"]
        == written["evidence_file_sha256"]
    )


def test_invalid_file_returns_exit_code_2(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = _write(tmp_path, _document(employment_centers=[_center(confidence=1.5)]))

    code = importer.main([str(path), "--cutoff", "2026-09-02T09:55:56Z", "--validate-only"])

    assert code == 2
    assert "confidence must be <= 1" in json.loads(capsys.readouterr().out)["error"]


def test_apply_plan_attaches_import_stamp_to_provenance(tmp_path: Path) -> None:
    path = _write(tmp_path, _document(factor_observations=[_observation()]))
    evidence = importer.load_evidence_file(path, cutoff=CUTOFF)
    plan = importer.build_plan(evidence, cutoff=CUTOFF, lookup_existing=lambda _record: None)
    stamp = importer.import_stamp(evidence, CUTOFF, datetime(2026, 9, 3, tzinfo=UTC))

    class _Session:
        def __init__(self) -> None:
            self.added: list[Any] = []

        def add(self, entity: Any) -> None:
            self.added.append(entity)

        def flush(self) -> None:
            pass

    session = _Session()
    importer.apply_plan(session, plan, stamp)

    assert len(session.added) == 1
    provenance = session.added[0].provenance
    assert provenance["url"] == PUBLIC_PROVENANCE["url"]
    assert provenance["import"]["evidence_file_sha256"] == evidence.sha256
    assert provenance["import"]["cutoff"] == CUTOFF.isoformat()
    assert provenance["import"]["importer_version"] == importer.IMPORTER_VERSION

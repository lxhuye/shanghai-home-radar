from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest

from scripts import p57_label_cli

FIELDS = [
    "blind_id",
    "district",
    "community",
    "reviewer_id",
    "opportunity_class",
    "workflow_recommendation",
    "weekend_top5_slot",
    "positive_reason_1",
    "positive_reason_2",
    "positive_reason_3",
    "major_risk_1",
    "major_risk_2",
    "major_risk_3",
    "reviewer_confidence",
    "notes",
]


def rows() -> list[dict[str, str]]:
    return [
        {
            field: (
                f"P57A-{index:03d}"
                if field == "blind_id"
                else "普陀"
                if field == "district"
                else "测试小区"
                if field == "community"
                else ""
            )
            for field in FIELDS
        }
        for index in range(1, 51)
    ]


def valid_values() -> dict[str, str]:
    return {
        "reviewer_id": "reviewer-a",
        "opportunity_class": "QUALITY_AT_DISCOUNT",
        "workflow_recommendation": "VIEW",
        "weekend_top5_slot": "YES",
        "positive_reason_1": "价格合理",
        "positive_reason_2": "",
        "positive_reason_3": "",
        "major_risk_1": "楼龄偏高",
        "major_risk_2": "",
        "major_risk_3": "",
        "reviewer_confidence": "HIGH",
        "notes": "",
    }


def write_csv(path: Path, values: list[dict[str, str]], fields: list[str] = FIELDS) -> None:
    with path.open("w", encoding="utf-8", newline="") as destination:
        writer = csv.DictWriter(destination, fieldnames=fields)
        writer.writeheader()
        writer.writerows(values)


def test_read_label_file_rejects_model_output_leak(tmp_path: Path) -> None:
    path = tmp_path / "labels.csv"
    fields = [*FIELDS, "future_score"]
    values = [{**row, "future_score": "84"} for row in rows()]
    write_csv(path, values, fields)

    with pytest.raises(ValueError, match="forbidden model fields"):
        p57_label_cli.read_label_file(path)


def test_apply_label_updates_one_row_and_progress(tmp_path: Path) -> None:
    path = tmp_path / "labels.csv"
    values = rows()
    p57_label_cli.apply_label(
        values,
        blind_id="P57A-001",
        values=valid_values(),
    )
    fingerprint = p57_label_cli.write_label_file(path, FIELDS, values)

    _, restored = p57_label_cli.read_label_file(path)
    state = p57_label_cli.progress(restored)
    assert state == {
        "total": 50,
        "completed": 1,
        "remaining": 49,
        "next_blind_id": "P57A-002",
        "ready_to_freeze": False,
    }
    assert len(fingerprint) == 64


def test_completed_label_cannot_be_silently_overwritten() -> None:
    values = rows()
    p57_label_cli.apply_label(
        values,
        blind_id="P57A-001",
        values=valid_values(),
    )

    with pytest.raises(ValueError, match="already complete"):
        p57_label_cli.apply_label(
            values,
            blind_id="P57A-001",
            values={**valid_values(), "notes": "changed"},
        )

    p57_label_cli.apply_label(
        values,
        blind_id="P57A-001",
        values={**valid_values(), "notes": "reviewed"},
        edit_existing=True,
    )
    assert values[0]["notes"] == "reviewed"


def test_label_validation_rejects_choice_and_reason_gaps() -> None:
    row = {**rows()[0], **valid_values(), "opportunity_class": "ATTACK"}
    with pytest.raises(ValueError, match="opportunity class"):
        p57_label_cli.validate_label(row)

    row = {
        **rows()[0],
        **valid_values(),
        "positive_reason_2": "",
        "positive_reason_3": "第三条不能越过第二条",
    }
    with pytest.raises(ValueError, match="must not contain gaps"):
        p57_label_cli.validate_label(row)


def test_frozen_manifest_closes_interactive_labeling(tmp_path: Path) -> None:
    labels = tmp_path / "P57_SET_A_HUMAN_LABELS.csv"
    write_csv(labels, rows())
    labels.with_name("P57_SET_A_RUN_MANIFEST.json").write_text(
        json.dumps({"status": "labels_frozen"}),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="closed"):
        p57_label_cli.assert_blind_labeling_open(labels)


def test_label_file_requires_exactly_fifty_unique_ids(tmp_path: Path) -> None:
    path = tmp_path / "labels.csv"
    write_csv(path, rows()[:-1])

    with pytest.raises(ValueError, match="50 unique"):
        p57_label_cli.read_label_file(path)

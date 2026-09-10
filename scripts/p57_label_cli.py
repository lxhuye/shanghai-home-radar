#!/usr/bin/env python3
"""Resume-safe terminal labeling for P5.7 raw blind evidence."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import re
import uuid
from pathlib import Path
from typing import Any

CLASSES = (
    "QUALITY_AT_DISCOUNT",
    "GOOD_BUT_EXPENSIVE",
    "VALUE_TRAP",
    "LOW_QUALITY",
    "INSUFFICIENT_INFORMATION",
)
WORKFLOWS = ("PASS", "WATCH", "CONTACT", "VIEW")
CONFIDENCES = ("LOW", "MEDIUM", "HIGH")
REQUIRED_LABELS = (
    "reviewer_id",
    "opportunity_class",
    "workflow_recommendation",
    "weekend_top5_slot",
    "positive_reason_1",
    "major_risk_1",
    "reviewer_confidence",
)
FORBIDDEN_MODEL_FIELDS = {
    "fair_value",
    "value_score",
    "future_score",
    "obsolescence_risk",
    "structural_alpha",
    "decision_classification",
    "rank",
    "why_ranked",
}
DISPLAY_FIELDS = (
    "blind_id",
    "district",
    "submarket",
    "community",
    "address",
    "price_wan",
    "area_sqm",
    "unit_price_yuan_sqm",
    "bedrooms",
    "living_rooms",
    "floor",
    "total_floors",
    "orientation",
    "year_built",
    "elevator",
    "metro_station",
    "metro_distance_m",
    "tags",
    "title",
)


def read_label_file(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    try:
        with path.open(encoding="utf-8-sig", newline="") as source:
            reader = csv.DictReader(source)
            fieldnames = list(reader.fieldnames or ())
            rows = [dict(row) for row in reader]
    except (OSError, UnicodeError, csv.Error):
        raise ValueError("label CSV could not be read") from None
    leaked = sorted(FORBIDDEN_MODEL_FIELDS & set(fieldnames))
    if leaked:
        raise ValueError(f"blind label CSV exposes forbidden model fields: {leaked}")
    missing = sorted(set(REQUIRED_LABELS) - set(fieldnames))
    if missing:
        raise ValueError(f"label CSV is missing required fields: {missing}")
    if len(rows) != 50 or len({row.get("blind_id") for row in rows}) != 50:
        raise ValueError("label CSV must contain 50 unique blind IDs")
    return fieldnames, rows


def validate_label(row: dict[str, str]) -> None:
    missing = [field for field in REQUIRED_LABELS if not row.get(field, "").strip()]
    if missing:
        raise ValueError(f"{row.get('blind_id')}: missing fields {missing}")
    if row["opportunity_class"] not in CLASSES:
        raise ValueError(f"{row['blind_id']}: invalid opportunity class")
    if row["workflow_recommendation"] not in WORKFLOWS:
        raise ValueError(f"{row['blind_id']}: invalid workflow recommendation")
    if row["weekend_top5_slot"] not in {"YES", "NO"}:
        raise ValueError(f"{row['blind_id']}: weekend slot must be YES or NO")
    if row["reviewer_confidence"] not in CONFIDENCES:
        raise ValueError(f"{row['blind_id']}: invalid reviewer confidence")
    _validate_reason_fields(row, "positive_reason")
    _validate_reason_fields(row, "major_risk")


def progress(rows: list[dict[str, str]]) -> dict[str, Any]:
    completed: list[str] = []
    for row in rows:
        try:
            validate_label(row)
        except ValueError:
            continue
        completed.append(row["blind_id"])
    pending = [row["blind_id"] for row in rows if row["blind_id"] not in set(completed)]
    return {
        "total": len(rows),
        "completed": len(completed),
        "remaining": len(pending),
        "next_blind_id": pending[0] if pending else None,
        "ready_to_freeze": len(pending) == 0,
    }


def apply_label(
    rows: list[dict[str, str]],
    *,
    blind_id: str,
    values: dict[str, str],
    edit_existing: bool = False,
) -> None:
    row = next((item for item in rows if item.get("blind_id") == blind_id), None)
    if row is None:
        raise ValueError(f"unknown blind ID: {blind_id}")
    already_complete = True
    try:
        validate_label(row)
    except ValueError:
        already_complete = False
    if already_complete and not edit_existing:
        raise ValueError(f"{blind_id}: label already complete; use --edit-existing")
    unexpected = sorted(set(values) - set(row))
    if unexpected:
        raise ValueError(f"unknown label fields: {unexpected}")
    candidate = {**row, **{key: value.strip() for key, value in values.items()}}
    validate_label(candidate)
    row.update(candidate)


def write_label_file(path: Path, fieldnames: list[str], rows: list[dict[str, str]]) -> str:
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with temporary.open("w", encoding="utf-8", newline="") as destination:
            writer = csv.DictWriter(destination, fieldnames=fieldnames, extrasaction="raise")
            writer.writeheader()
            writer.writerows(rows)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
    return hashlib.sha256(path.read_bytes()).hexdigest()


def assert_blind_labeling_open(labels_path: Path) -> None:
    manifest_path = labels_path.with_name("P57_SET_A_RUN_MANIFEST.json")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        raise ValueError("P5.7 run manifest is missing or unreadable") from None
    if not isinstance(manifest, dict) or manifest.get("status") != "blind_labeling":
        raise ValueError("P5.7 labels are closed or the run is not in blind_labeling")


def interactive_label(
    path: Path,
    *,
    reviewer_id: str,
    start_blind_id: str | None,
    edit_existing: bool,
) -> None:
    assert_blind_labeling_open(path)
    fieldnames, rows = read_label_file(path)
    started = start_blind_id is None
    for row in rows:
        if not started:
            started = row["blind_id"] == start_blind_id
            if not started:
                continue
        try:
            validate_label(row)
            complete = True
        except ValueError:
            complete = False
        if complete and not edit_existing:
            continue

        _print_evidence(row)
        values = {
            "reviewer_id": reviewer_id,
            "opportunity_class": _prompt_choice("机会类型", CLASSES, row),
            "workflow_recommendation": _prompt_choice("工作流", WORKFLOWS, row),
            "weekend_top5_slot": _prompt_choice("周末 Top-5", ("YES", "NO"), row),
            **_prompt_reasons("正面理由，1-3 条，用分号分隔", "positive_reason", row),
            **_prompt_reasons("主要风险，1-3 条，用分号分隔", "major_risk", row),
            "reviewer_confidence": _prompt_choice("置信度", CONFIDENCES, row),
            "notes": input(f"备注 [{row.get('notes', '')}]: ").strip() or row.get("notes", ""),
        }
        apply_label(
            rows,
            blind_id=row["blind_id"],
            values=values,
            edit_existing=edit_existing,
        )
        fingerprint = write_label_file(path, fieldnames, rows)
        state = progress(rows)
        print(
            json.dumps(
                {**state, "saved": row["blind_id"], "sha256": fingerprint},
                ensure_ascii=False,
            ),
            flush=True,
        )


def _validate_reason_fields(row: dict[str, str], prefix: str) -> None:
    reasons = [row.get(f"{prefix}_{index}", "").strip() for index in range(1, 4)]
    nonempty = [value for value in reasons if value]
    if not 1 <= len(nonempty) <= 3:
        raise ValueError(f"{row.get('blind_id')}: provide 1 to 3 {prefix} values")
    if any(not value for value in reasons[: len(nonempty)]):
        raise ValueError(f"{row.get('blind_id')}: {prefix} values must not contain gaps")


def _prompt_choice(label: str, choices: tuple[str, ...], row: dict[str, str]) -> str:
    current_key = {
        "机会类型": "opportunity_class",
        "工作流": "workflow_recommendation",
        "周末 Top-5": "weekend_top5_slot",
        "置信度": "reviewer_confidence",
    }[label]
    current = row.get(current_key, "")
    while True:
        print("  ".join(f"{index + 1}.{choice}" for index, choice in enumerate(choices)))
        raw = input(f"{label} [{current}]: ").strip()
        if not raw and current in choices:
            return current
        if raw.isdigit() and 1 <= int(raw) <= len(choices):
            return choices[int(raw) - 1]
        normalized = raw.upper()
        if normalized in choices:
            return normalized


def _prompt_reasons(label: str, prefix: str, row: dict[str, str]) -> dict[str, str]:
    current = [row.get(f"{prefix}_{index}", "") for index in range(1, 4)]
    default = "; ".join(value for value in current if value)
    while True:
        raw = input(f"{label} [{default}]: ").strip() or default
        values = [value.strip() for value in re.split(r"[;；]", raw) if value.strip()]
        if 1 <= len(values) <= 3:
            return {
                f"{prefix}_{index}": values[index - 1] if index <= len(values) else ""
                for index in range(1, 4)
            }


def _print_evidence(row: dict[str, str]) -> None:
    print("\n" + "=" * 72)
    for field in DISPLAY_FIELDS:
        value = row.get(field, "").strip()
        if value:
            print(f"{field}: {value}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--labels",
        type=Path,
        default=Path("data/exports/validation/p57_set_a_r2/P57_SET_A_HUMAN_LABELS.csv"),
    )
    parser.add_argument("--reviewer")
    parser.add_argument("--start")
    parser.add_argument("--edit-existing", action="store_true")
    parser.add_argument("--status", action="store_true")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        fieldnames, rows = read_label_file(args.labels)
        if args.status:
            print(json.dumps(progress(rows), ensure_ascii=False, indent=2))
            return 0
        if not args.reviewer or not args.reviewer.strip():
            raise ValueError("--reviewer is required for interactive labeling")
        del fieldnames
        interactive_label(
            args.labels,
            reviewer_id=args.reviewer.strip(),
            start_blind_id=args.start,
            edit_existing=args.edit_existing,
        )
        return 0
    except (EOFError, KeyboardInterrupt):
        print("\n标注已停止，已完成记录保持保存。")
        return 130
    except ValueError as exc:
        print(json.dumps({"status": "error", "error": str(exc)}, ensure_ascii=False))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

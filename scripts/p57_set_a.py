#!/usr/bin/env python3
"""Prepare and freeze the P5.7 Set A human blind-calibration run."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import subprocess
import uuid
from collections import Counter, defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from home_radar_decision.config import load_decision_config
from home_radar_decision.p56_config import load_real_world_validation_config
from home_radar_decision.real_world_validation import (
    create_validation_run,
    freeze_human_labels,
    label_validation_item,
)
from home_radar_forecasting.config import load_future_config
from home_radar_market.config import load_market_config
from home_radar_models.collection import CrawlRun, RawSourceRecord
from home_radar_models.decision import DecisionValidationBatch
from home_radar_models.enums import DataMode
from home_radar_models.listing import Listing
from home_radar_valuation.config import load_valuation_config
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

# Source import batch of the original (now terminated) Set A run. A rebuilt run must pass the
# crawl_run id of its own import via --source-batch-id.
DEFAULT_SOURCE_BATCH_ID = uuid.UUID("4ff2f079-775c-42b2-8ba0-8dca85c6c702")
DEFAULT_INPUT = Path("data/raw/calibration_set_a/market_pool.canonical.json")
DEFAULT_OUTPUT = Path("data/exports/validation/p57_set_a")
DEFAULT_DATABASE_URL = "postgresql+psycopg://radar:radar@127.0.0.1:55432/shanghai_home_radar"
GEOGRAPHY = {
    "徐汇": "OUTER_XUHUI",
    "闵行": "NORTHERN_MINHANG",
    "普陀": "PUTUO",
    "杨浦": "YANGPU",
    "浦东": "MATURE_PUDONG",
}
RAW_EVIDENCE_FIELDS = (
    "district",
    "submarket",
    "community",
    "address",
    "price_wan",
    "area_sqm",
    "unit_price_yuan_sqm",
    "bedrooms",
    "living_rooms",
    "bathrooms",
    "floor",
    "total_floors",
    "orientation",
    "year_built",
    "elevator",
    "building_type",
    "property_type",
    "metro_station",
    "metro_distance_m",
    "listing_date",
    "views_30d",
    "showings_30d",
    "government_verified",
    "tags",
    "title",
    "description",
    "observed_at",
)
LABEL_FIELDS = (
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
)
MODEL_FIELD_NAMES = {
    "fair_value",
    "value_score",
    "future_score",
    "obsolescence_risk",
    "structural_alpha",
    "decision_classification",
    "rank",
    "why_ranked",
}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("prepare", "freeze-labels"))
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--database-url", default=DEFAULT_DATABASE_URL)
    parser.add_argument(
        "--source-batch-id",
        type=uuid.UUID,
        default=DEFAULT_SOURCE_BATCH_ID,
        help="crawl_run id of the Set A import batch this run is built on",
    )
    parser.add_argument(
        "--input-cutoff",
        help="frozen model-input cutoff for prepare (ISO-8601 with timezone)",
    )
    args = parser.parse_args()
    if args.command == "prepare":
        if not args.input_cutoff:
            parser.error("prepare requires --input-cutoff")
        prepare(
            args.input,
            args.output,
            args.database_url,
            source_batch_id=args.source_batch_id,
            input_cutoff=parse_input_cutoff(args.input_cutoff),
        )
    else:
        freeze_labels(args.output, args.database_url)


def parse_input_cutoff(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("--input-cutoff must include a timezone")
    return parsed.astimezone(UTC)


def prepare(
    input_path: Path,
    output: Path,
    database_url: str,
    *,
    source_batch_id: uuid.UUID = DEFAULT_SOURCE_BATCH_ID,
    input_cutoff: datetime,
) -> None:
    envelope = json.loads(input_path.read_text(encoding="utf-8"))
    items = envelope.get("items")
    if not isinstance(items, list):
        raise ValueError("canonical envelope items must be an array")
    qa = audit_items(items, envelope.get("metadata", {}))
    if qa["blocking_errors"]:
        raise ValueError(f"Set A QA failed: {qa['blocking_errors']}")

    output.mkdir(parents=True, exist_ok=True)
    engine = create_engine(database_url, pool_pre_ping=True)
    with Session(engine, expire_on_commit=False) as session:
        listing_map, database_qa = load_source_batch(session, source_batch_id)
        qa["database"] = database_qa
        if database_qa["blocking_errors"]:
            raise ValueError(f"Set A database QA failed: {database_qa['blocking_errors']}")

        targets = sorted(
            (item for item in items if item["validation_role"] == "TARGET"),
            key=lambda item: (
                item["district"],
                hashlib.sha256(item["listing_id"].encode()).hexdigest(),
            ),
        )
        blind_rows: list[dict[str, Any]] = []
        internal_rows: list[dict[str, Any]] = []
        entries: list[dict[str, Any]] = []
        target_uuid_by_source = {
            source_id: str(listing.id) for source_id, listing in listing_map.items()
        }
        near_exclusions = {
            target_uuid_by_source[target]: [target_uuid_by_source[value] for value in contexts]
            for target, contexts in qa["target_context_near_duplicate_exclusions"].items()
        }
        context_source_ids = [
            item["listing_id"]
            for item in items
            if item["validation_role"] == "CONTEXT"
            and item["listing_id"] not in qa["p3_context_dedup_exclusions"]
        ]
        isolation = {
            "source_import_batch_id": str(source_batch_id),
            "policy": "P3_CONTEXT_ONLY_P4_CONTEXT_ONLY_TARGET_SELF_EXCLUDED",
            "raw_context_count": 450,
            "effective_p3_context_count": len(context_source_ids),
            "context_listing_ids": [target_uuid_by_source[value] for value in context_source_ids],
            "p3_context_dedup_exclusions": [
                target_uuid_by_source[value] for value in qa["p3_context_dedup_exclusions"]
            ],
            "target_near_duplicate_exclusions": near_exclusions,
        }
        for index, item in enumerate(targets, 1):
            blind_id = f"P57A-{index:03d}"
            evidence = blind_evidence(item)
            listing = listing_map[item["listing_id"]]
            row = {"blind_id": blind_id, **evidence}
            blind_rows.append(row)
            internal_rows.append(
                {
                    "blind_id": blind_id,
                    "listing_uuid": str(listing.id),
                    "source_listing_id": item["listing_id"],
                    "source_url": item["url"],
                    "district": item["district"],
                }
            )
            entries.append(
                {
                    "listing_id": listing.id,
                    "geography_bucket": GEOGRAPHY[item["district"]],
                    "archetypes": [archetype(item)],
                    "data_provenance": {
                        "kind": "PROPERLY_ANONYMIZED",
                        "reference": f"crawl_run:{source_batch_id}:{blind_id}",
                        "source_kind": "public_research",
                        "data_mode": "SAMPLE",
                        "allowed_use": "internal_research_and_calibration_only",
                    },
                    "blind_listing_snapshot": evidence,
                }
            )

        source_commit = git_value("rev-parse", "HEAD")
        source_tree_hash = source_tree_fingerprint()
        config_paths = tuple(sorted(Path("config").glob("*.yaml")))
        batch = existing_set_a_batch(session, source_batch_id, input_cutoff)
        if batch is None:
            batch = create_validation_run(
                session,
                name="P5.7 Calibration Set A",
                entries=entries,
                data_mode=DataMode.SAMPLE,
                as_of=input_cutoff,
                source_commit=source_commit,
                source_tree_hash=source_tree_hash,
                config_paths=config_paths,
                market_config=load_market_config(),
                valuation_config=load_valuation_config(),
                future_config=load_future_config(),
                decision_config=load_decision_config(),
                validation_config=load_real_world_validation_config(),
                validation_isolation=isolation,
            )
            session.commit()
        elif batch.status != "blind_labeling":
            raise ValueError(f"Set A validation run already advanced to {batch.status}")
        else:
            expected_configs = {
                str(path): file_sha256(path)
                for path in sorted(config_paths, key=lambda value: str(value))
            }
            frozen = batch.freeze_manifest
            if (
                frozen.get("git_commit") != source_commit
                or frozen.get("source_tree_hash") != source_tree_hash
                or frozen.get("yaml_sha256") != expected_configs
                or "model_input_fingerprints" not in frozen
            ):
                raise ValueError("existing Set A run has a different frozen runtime or input set")

    write_csv(output / "P57_SET_A_BLIND_EVIDENCE.csv", blind_rows)
    label_rows = [{**row, **{field: "" for field in LABEL_FIELDS}} for row in blind_rows]
    write_csv(output / "P57_SET_A_HUMAN_LABELS.csv", label_rows)
    write_json(output / "P57_SET_A_INTERNAL_ID_MAP.json", internal_rows)
    write_json(output / "P57_SET_A_QA.json", qa)
    freeze_configs(output / "frozen_config", config_paths)
    manifest = {
        "source_import_batch_id": str(source_batch_id),
        "validation_batch_id": str(batch.id),
        "validation_run_id": batch.validation_run_id,
        "status": batch.status,
        "dataset_fingerprint": batch.dataset_fingerprint,
        "source_commit": source_commit,
        "source_tree_hash": source_tree_hash,
        "input_cutoff_at": input_cutoff.isoformat(),
        "blind_evidence_sha256": file_sha256(output / "P57_SET_A_BLIND_EVIDENCE.csv"),
        "human_label_template_sha256": file_sha256(output / "P57_SET_A_HUMAN_LABELS.csv"),
        "model_outputs_generated": False,
        "model_outputs_revealed": False,
        "prepared_at": datetime.now(UTC).isoformat(),
    }
    write_json(output / "P57_SET_A_RUN_MANIFEST.json", manifest)
    write_reports(qa, manifest)
    print(json.dumps(manifest, ensure_ascii=False))


def freeze_labels(output: Path, database_url: str) -> None:
    manifest = read_json(output / "P57_SET_A_RUN_MANIFEST.json")
    internal = read_json(output / "P57_SET_A_INTERNAL_ID_MAP.json")
    rows = list(csv.DictReader((output / "P57_SET_A_HUMAN_LABELS.csv").open(encoding="utf-8-sig")))
    if len(rows) != 50:
        raise ValueError("human label file must contain exactly 50 rows")
    by_blind_id = {row["blind_id"]: row for row in internal}
    engine = create_engine(database_url, pool_pre_ping=True)
    with Session(engine, expire_on_commit=False) as session:
        batch_id = uuid.UUID(manifest["validation_batch_id"])
        batch = session.get(DecisionValidationBatch, batch_id)
        if batch is None:
            raise ValueError("validation batch not found")
        for row in rows:
            validate_label_row(row)
            mapping = by_blind_id[row["blind_id"]]
            label_validation_item(
                session,
                batch_id,
                listing_id=uuid.UUID(mapping["listing_uuid"]),
                opportunity_classification=row["opportunity_class"],
                workflow_recommendation=row["workflow_recommendation"],
                confidence=row["reviewer_confidence"],
                positive_reasons=nonempty(row, "positive_reason", 3),
                risks=nonempty(row, "major_risk", 3),
                would_visit=row["weekend_top5_slot"] == "YES",
                notes=row["notes"] or None,
            )
        freeze_human_labels(session, batch_id)
        session.commit()
        if batch.labels_frozen_at is None:
            raise ValueError("label freeze did not record a timestamp")
        manifest.update(
            {
                "status": batch.status,
                "human_labels_fingerprint": batch.human_labels_fingerprint,
                "human_label_file_sha256": file_sha256(output / "P57_SET_A_HUMAN_LABELS.csv"),
                "labels_frozen_at": batch.labels_frozen_at.isoformat(),
            }
        )
    write_json(output / "P57_SET_A_RUN_MANIFEST.json", manifest)
    print(json.dumps(manifest, ensure_ascii=False))


def audit_items(items: list[dict[str, Any]], metadata: dict[str, Any]) -> dict[str, Any]:
    blocking: list[str] = []
    role_counts = Counter(item.get("validation_role") for item in items)
    district_counts = Counter(item.get("district") for item in items)
    target_district_counts = Counter(
        item.get("district") for item in items if item.get("validation_role") == "TARGET"
    )
    if len(items) != 500:
        blocking.append(f"record_count={len(items)}")
    if role_counts != Counter({"CONTEXT": 450, "TARGET": 50}):
        blocking.append(f"role_counts={dict(role_counts)}")
    if district_counts != Counter({district: 100 for district in GEOGRAPHY}):
        blocking.append(f"district_counts={dict(district_counts)}")
    if target_district_counts != Counter({district: 10 for district in GEOGRAPHY}):
        blocking.append(f"target_district_counts={dict(target_district_counts)}")

    duplicate_source_ids = duplicates(item.get("listing_id") for item in items)
    duplicate_urls = duplicates(item.get("url") for item in items)
    if duplicate_source_ids:
        blocking.append("duplicate source listing identifiers")
    if duplicate_urls:
        blocking.append("duplicate source URLs")
    exact_cross_role = exact_cross_role_duplicates(items)
    if exact_cross_role:
        blocking.append("exact TARGET/CONTEXT duplicate leakage")

    near_pairs = likely_near_duplicates(items)
    target_context: dict[str, list[str]] = defaultdict(list)
    for pair in near_pairs:
        if pair["left_role"] == pair["right_role"]:
            continue
        target = pair["left_id"] if pair["left_role"] == "TARGET" else pair["right_id"]
        context = pair["right_id"] if pair["left_role"] == "TARGET" else pair["left_id"]
        target_context[target].append(context)

    context_exact_groups = context_duplicate_groups(items)
    p3_exclusions = [sorted(group)[-1] for group in context_exact_groups]
    model_year_1901 = sum(item.get("year_built") == 1901 for item in items)
    if model_year_1901:
        blocking.append(f"model-facing 1901 placeholders={model_year_1901}")
    provenance_missing = sum(not item.get("observed_at") or not item.get("url") for item in items)
    if provenance_missing:
        blocking.append(f"missing provenance/timestamp={provenance_missing}")
    missingness = {
        field: {
            "missing_count": sum(is_missing(item.get(field)) for item in items),
            "missing_rate": round(
                sum(is_missing(item.get(field)) for item in items) / len(items), 4
            ),
        }
        for field in RAW_EVIDENCE_FIELDS
    }
    return {
        "blocking_errors": blocking,
        "record_count": len(items),
        "role_counts": dict(sorted(role_counts.items())),
        "district_counts": dict(sorted(district_counts.items())),
        "target_district_counts": dict(sorted(target_district_counts.items())),
        "unique_source_listing_ids": len(items) - len(duplicate_source_ids),
        "unique_source_urls": len(items) - len(duplicate_urls),
        "exact_target_context_duplicates": exact_cross_role,
        "likely_near_duplicate_pairs": near_pairs,
        "target_context_near_duplicate_exclusions": {
            key: sorted(set(value)) for key, value in sorted(target_context.items())
        },
        "context_exact_duplicate_groups": context_exact_groups,
        "p3_context_dedup_exclusions": p3_exclusions,
        "normalized_year_1901_count": model_year_1901,
        "raw_source_year_1901_count": sum(
            item.get("source_year_built_raw") == 1901 for item in items
        ),
        "property_field_missingness": missingness,
        "data_mode": metadata.get("data_mode"),
        "source_kind": metadata.get("source_kind"),
        "allowed_use": metadata.get("allowed_use"),
        "metadata_observed_at": metadata.get("observed_at"),
    }


def load_source_batch(
    session: Session, batch_id: uuid.UUID
) -> tuple[dict[str, Listing], dict[str, Any]]:
    run = session.get(CrawlRun, batch_id)
    blocking: list[str] = []
    if run is None:
        raise ValueError(f"source import batch not found: {batch_id}")
    rows = session.execute(
        select(RawSourceRecord, Listing)
        .join(Listing, Listing.id == RawSourceRecord.listing_id)
        .where(RawSourceRecord.crawl_run_id == batch_id)
    ).all()
    listing_map = {raw.source_record_id: listing for raw, listing in rows}
    if len(rows) != 500:
        blocking.append(f"database record count={len(rows)}")
    if run.normalized_item_count != 500 or run.parse_error_count != 0:
        blocking.append(
            f"import normalized={run.normalized_item_count}, errors={run.parse_error_count}"
        )
    if run.data_mode != DataMode.SAMPLE.value:
        blocking.append(f"data mode={run.data_mode}")
    source_ids = [listing.source_listing_id for _, listing in rows]
    urls = [listing.source_url for _, listing in rows]
    if len(source_ids) != len(set(source_ids)):
        blocking.append("database source listing identifiers are not unique")
    if len(urls) != len(set(urls)):
        blocking.append("database source URLs are not unique")
    return listing_map, {
        "blocking_errors": blocking,
        "crawl_run_id": str(run.id),
        "status": run.status,
        "completeness": run.completeness,
        "data_mode": run.data_mode,
        "raw_item_count": run.raw_item_count,
        "normalized_item_count": run.normalized_item_count,
        "parse_error_count": run.parse_error_count,
        "linked_listing_count": len(rows),
        "unique_source_listing_ids": len(set(source_ids)),
        "unique_source_urls": len(set(urls)),
        "missing_observed_at": sum(raw.observed_at is None for raw, _ in rows),
        "missing_source_provenance": sum(
            not listing.source or not listing.source_url for _, listing in rows
        ),
        "model_year_1901_count": sum(listing.year_built == 1901 for _, listing in rows),
    }


def likely_near_duplicates(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    pairs: list[dict[str, Any]] = []
    for index, left in enumerate(items):
        for right in items[index + 1 :]:
            if left.get("district") != right.get("district"):
                continue
            if left.get("community") != right.get("community"):
                continue
            if left.get("bedrooms") != right.get("bedrooms"):
                continue
            if relative_difference(left.get("area_sqm"), right.get("area_sqm")) > 0.015:
                continue
            if relative_difference(left.get("price_wan"), right.get("price_wan")) > 0.05:
                continue
            signals = {
                "living_rooms": left.get("living_rooms") == right.get("living_rooms"),
                "total_floors": left.get("total_floors") == right.get("total_floors"),
                "floor_band": floor_band(left.get("floor")) == floor_band(right.get("floor")),
                "year_built": left.get("year_built") == right.get("year_built"),
                "orientation": left.get("orientation") == right.get("orientation"),
                "address": left.get("address") == right.get("address"),
            }
            score = sum(signals.values())
            if score < 4:
                continue
            pairs.append(
                {
                    "left_id": left["listing_id"],
                    "left_role": left["validation_role"],
                    "right_id": right["listing_id"],
                    "right_role": right["validation_role"],
                    "community": left["community"],
                    "signal_count": score,
                    "matching_signals": sorted(key for key, value in signals.items() if value),
                }
            )
    return sorted(pairs, key=lambda value: (value["left_id"], value["right_id"]))


def exact_cross_role_duplicates(items: list[dict[str, Any]]) -> list[list[str]]:
    groups: dict[tuple[Any, ...], list[dict[str, Any]]] = defaultdict(list)
    for item in items:
        groups[exact_property_key(item)].append(item)
    return [
        sorted(value["listing_id"] for value in group)
        for group in groups.values()
        if len(group) > 1 and len({value["validation_role"] for value in group}) > 1
    ]


def context_duplicate_groups(items: list[dict[str, Any]]) -> list[list[str]]:
    groups: dict[tuple[Any, ...], list[str]] = defaultdict(list)
    for item in items:
        if item.get("validation_role") == "CONTEXT":
            groups[exact_property_key(item)].append(item["listing_id"])
    return sorted(sorted(group) for group in groups.values() if len(group) > 1)


def exact_property_key(item: dict[str, Any]) -> tuple[Any, ...]:
    return (
        item.get("district"),
        item.get("community"),
        item.get("address"),
        item.get("area_sqm"),
        item.get("price_wan"),
        item.get("bedrooms"),
        item.get("living_rooms"),
        item.get("floor"),
        item.get("total_floors"),
        item.get("year_built"),
    )


def blind_evidence(item: dict[str, Any]) -> dict[str, Any]:
    evidence = {field: item.get(field) for field in RAW_EVIDENCE_FIELDS}
    leaked = MODEL_FIELD_NAMES & evidence.keys()
    if leaked:
        raise ValueError(f"blind evidence leaks model fields: {sorted(leaked)}")
    return evidence


def archetype(item: dict[str, Any]) -> str:
    year = item.get("year_built")
    age = (
        "YEAR_UNKNOWN"
        if year is None
        else "PRE_1990"
        if year < 1990
        else "1990_2005"
        if year <= 2005
        else "POST_2005"
    )
    area = float(item["area_sqm"])
    area_band = "COMPACT" if area < 50 else "MAINSTREAM" if area < 80 else "LARGE"
    return f"{age}_{area_band}_{floor_band(item.get('floor'))}"


def floor_band(value: Any) -> str:
    text = str(value or "")
    if "低层" in text:
        return "LOW"
    if "中层" in text:
        return "MIDDLE"
    if "高层" in text:
        return "HIGH"
    return "UNKNOWN"


def relative_difference(left: Any, right: Any) -> float:
    if left is None or right is None:
        return 1.0
    left_number = float(left)
    right_number = float(right)
    return abs(left_number - right_number) / max(left_number, right_number)


def duplicates(values: Any) -> list[str]:
    counts = Counter(str(value) for value in values)
    return sorted(value for value, count in counts.items() if count > 1)


def is_missing(value: Any) -> bool:
    return value is None or value == "" or value == []


def existing_set_a_batch(
    session: Session, source_batch_id: uuid.UUID, input_cutoff: datetime
) -> DecisionValidationBatch | None:
    batches = session.scalars(
        select(DecisionValidationBatch)
        .where(DecisionValidationBatch.name == "P5.7 Calibration Set A")
        .order_by(DecisionValidationBatch.created_at.desc())
    ).all()
    for batch in batches:
        isolation = batch.freeze_manifest.get("validation_isolation", {})
        if (
            isolation.get("source_import_batch_id") == str(source_batch_id)
            and batch.input_cutoff_at == input_cutoff
        ):
            return batch
    return None


def validate_label_row(row: dict[str, str]) -> None:
    required = {
        "reviewer_id",
        "opportunity_class",
        "workflow_recommendation",
        "weekend_top5_slot",
        "positive_reason_1",
        "major_risk_1",
        "reviewer_confidence",
    }
    missing = sorted(field for field in required if not row.get(field, "").strip())
    if missing:
        raise ValueError(f"{row.get('blind_id')}: missing label fields {missing}")
    if row["weekend_top5_slot"] not in {"YES", "NO"}:
        raise ValueError(f"{row['blind_id']}: weekend_top5_slot must be YES or NO")


def nonempty(row: dict[str, str], prefix: str, maximum: int) -> list[str]:
    return [
        row[f"{prefix}_{index}"].strip()
        for index in range(1, maximum + 1)
        if row[f"{prefix}_{index}"].strip()
    ]


def source_tree_fingerprint() -> str:
    roots = (
        Path("services/market/src"),
        Path("services/valuation/src"),
        Path("services/forecasting/src"),
        Path("services/decision/src"),
        Path("packages/models/src"),
    )
    paths = sorted(
        [path for root in roots for path in root.rglob("*.py")]
        + list(Path("config").glob("*.yaml"))
        + [Path(__file__)]
    )
    digest = hashlib.sha256()
    for path in paths:
        digest.update(str(path).encode())
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def git_value(*args: str) -> str:
    try:
        return subprocess.run(
            ("git", *args), check=True, capture_output=True, text=True
        ).stdout.strip()
    except (FileNotFoundError, subprocess.CalledProcessError):
        if args == ("rev-parse", "HEAD"):
            configured = os.environ.get("SHR_SOURCE_COMMIT", "").strip()
            if configured and configured != "unknown":
                return configured
        raise RuntimeError(
            "git metadata unavailable; set SHR_SOURCE_COMMIT to the exact source commit"
        ) from None


def freeze_configs(output: Path, paths: tuple[Path, ...]) -> None:
    output.mkdir(parents=True, exist_ok=True)
    for path in paths:
        (output / path.name).write_bytes(path.read_bytes())


def write_reports(qa: dict[str, Any], manifest: dict[str, Any]) -> None:
    missing = qa["property_field_missingness"]
    cross_role_exclusion_count = sum(
        len(value) for value in qa["target_context_near_duplicate_exclusions"].values()
    )
    report = [
        "# P5.7 Calibration Set A Report",
        "",
        "## Run status",
        "",
        f"- Source import batch: `{manifest['source_import_batch_id']}`",
        f"- Validation run: `{manifest['validation_run_id']}`",
        "- Stage: `BLIND_LABELING`",
        "- Model run: not started",
        "- Reveal: not started",
        "",
        (
            "The pipeline is intentionally stopped before model generation because no "
            "human blind labels existed for Set A. Model outputs remain hidden and unfrozen."
        ),
        "",
        "## Data QA",
        "",
        f"- Imported: {qa['record_count']} records",
        f"- Roles: {json.dumps(qa['role_counts'], ensure_ascii=False)}",
        f"- Districts: {json.dumps(qa['district_counts'], ensure_ascii=False)}",
        f"- TARGET districts: {json.dumps(qa['target_district_counts'], ensure_ascii=False)}",
        f"- Unique source identifiers: {qa['unique_source_listing_ids']}",
        f"- Unique source URLs: {qa['unique_source_urls']}",
        f"- Exact TARGET/CONTEXT duplicates: {len(qa['exact_target_context_duplicates'])}",
        f"- Likely near-duplicate pairs: {len(qa['likely_near_duplicate_pairs'])}",
        f"- TARGET/CONTEXT near-duplicate exclusions: {cross_role_exclusion_count}",
        f"- P3 context dedup exclusions: {len(qa['p3_context_dedup_exclusions'])}",
        f"- Model-facing 1901 values: {qa['normalized_year_1901_count']}",
        f"- Raw 1901 placeholders retained only as provenance: {qa['raw_source_year_1901_count']}",
        "- Data classification: SAMPLE / public research / internal calibration only",
        "",
        "## Missingness",
        "",
        "| Field | Missing | Rate |",
        "|---|---:|---:|",
    ]
    report.extend(
        f"| {field} | {values['missing_count']} | {values['missing_rate']:.1%} |"
        for field, values in missing.items()
    )
    report.extend(
        [
            "",
            "## Validation isolation",
            "",
            (
                "P3 validation baselines are defined from the deduplicated CONTEXT universe "
                "only. P4 comparables are limited to CONTEXT. TARGET records never enter the "
                "baseline or comparable universe. The five conservative cross-role "
                "near-duplicate matches are excluded for their corresponding TARGET."
            ),
            "",
            "## Metrics",
            "",
            (
                "Not calculated. Precision@5, Precision@10, Regret@5, ValueTrap@5, "
                "ValueTrap@10, VIEW Recall, and Why Ranked agreement require frozen human "
                "labels and a later frozen model run."
            ),
            "",
            "## Scope",
            "",
            (
                "Set A covers RMB 2.5M–3.0M. It does not validate performance across the "
                "full RMB 2.3M–3.3M target range."
            ),
            "",
            "## Recommendation",
            "",
            "MORE_CALIBRATION_REQUIRED",
        ]
    )
    Path("P57_CALIBRATION_SET_A_REPORT.md").write_text("\n".join(report) + "\n", encoding="utf-8")

    errors = [
        "# P5.7 Error Analysis",
        "",
        f"Validation run: `{manifest['validation_run_id']}`",
        "",
        (
            "Error analysis has not started because human labels and model outputs have not "
            "both been frozen and revealed."
        ),
        "",
        (
            "The post-reveal review will prioritize false-positive VALUE_TRAP listings, "
            "Top-5 regret cases, high-confidence model errors, and missed human VIEW "
            "listings. Root causes will use the existing P5.6 categories. No tuning is "
            "permitted during this phase."
        ),
    ]
    Path("P57_ERROR_ANALYSIS.md").write_text("\n".join(errors) + "\n", encoding="utf-8")


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        raise ValueError("cannot write empty CSV")
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


if __name__ == "__main__":
    main()

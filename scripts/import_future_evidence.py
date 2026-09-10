#!/usr/bin/env python3
"""Import P5A future evidence (employment centers, projects, factor observations).

Design constraints:

* Idempotent. Each record is keyed by the table's natural unique constraint. An identical
  record is skipped; a record with the same key but different content is reported as a
  conflict and the whole import is rejected. Nothing is ever updated or deleted.
* Cutoff-aware. Every temporal field (effective_from, source_timestamp / source_date,
  observed_at) must be at or before ``--cutoff`` so a frozen validation run cannot see
  look-ahead evidence.
* Provenance-complete. Non-demo modes require a public, reviewable source (url, publisher,
  published_at, retrieved_at). Records flagged ``synthetic: true`` are only accepted in
  ``demo`` mode.
* Auditable. The full provenance block from the evidence file is stored on each row together
  with the file hash, importer version, git commit, and cutoff used for the import.

Usage::

    python scripts/import_future_evidence.py evidence.yaml --cutoff 2026-09-02T09:55:56Z \
        --validate-only
    python scripts/import_future_evidence.py evidence.yaml --cutoff ... --dry-run
    python scripts/import_future_evidence.py evidence.yaml --cutoff ... --manifest out.json
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import uuid
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Literal

import yaml

sys.path.insert(0, str(Path(__file__).parent.parent))

IMPORTER_VERSION = "future-evidence-importer-v1"
DATA_MODES = ("demo", "sample", "live")
PROJECT_STATUSES = ("current", "under_construction", "approved", "planned", "conceptual")
PROJECT_TYPES = ("transport", "urban_renewal", "housing_supply", "public_service", "employment")
SCOPE_TYPES = ("shanghai", "district", "submarket", "community", "listing")
FACTORS = (
    "employment_accessibility",
    "transport_accessibility",
    "supply_scarcity",
    "buyer_pool_depth",
    "community_competitiveness",
    "urban_renewal",
    "rental_demand",
    "public_services",
    "planning_realization",
    "market_cycle",
    "building_aging",
    "product_obsolescence",
)
REQUIRED_PUBLIC_PROVENANCE = ("url", "publisher", "published_at", "retrieved_at")
TableName = Literal["employment_center", "future_project", "future_factor_observation"]


class EvidenceValidationError(ValueError):
    """Raised when the evidence file violates the import contract."""


@dataclass(frozen=True)
class EvidenceRecord:
    table: TableName
    key: tuple[str, ...]
    values: dict[str, Any]
    label: str


@dataclass
class ImportPlan:
    data_mode: str
    cutoff: datetime
    inserts: list[EvidenceRecord] = field(default_factory=list)
    skipped: list[EvidenceRecord] = field(default_factory=list)
    conflicts: list[tuple[EvidenceRecord, list[str]]] = field(default_factory=list)

    def summary(self) -> dict[str, Any]:
        def count(records: Iterable[EvidenceRecord]) -> dict[str, int]:
            counts = {"employment_center": 0, "future_project": 0, "future_factor_observation": 0}
            for record in records:
                counts[record.table] += 1
            return counts

        return {
            "data_mode": self.data_mode,
            "cutoff": self.cutoff.isoformat(),
            "inserts": count(self.inserts),
            "skipped_identical": count(self.skipped),
            "conflicts": [
                {"table": record.table, "label": record.label, "fields": fields}
                for record, fields in self.conflicts
            ],
        }


# --------------------------------------------------------------------------- parsing helpers


def parse_datetime(value: Any, *, field_name: str, label: str) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, date):
        parsed = datetime.combine(value, datetime.min.time())
    elif isinstance(value, str):
        text = value.strip().replace("Z", "+00:00")
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError as exc:
            raise EvidenceValidationError(
                f"{label}: {field_name} is not ISO-8601: {value}"
            ) from exc
    else:
        raise EvidenceValidationError(f"{label}: {field_name} is required")
    if parsed.tzinfo is None:
        raise EvidenceValidationError(
            f"{label}: {field_name} must carry a timezone offset (got {value!r})"
        )
    return parsed.astimezone(UTC)


def parse_optional_datetime(value: Any, *, field_name: str, label: str) -> datetime | None:
    if value is None:
        return None
    return parse_datetime(value, field_name=field_name, label=label)


def parse_date(value: Any, *, field_name: str, label: str) -> date | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value.strip())
        except ValueError as exc:
            raise EvidenceValidationError(f"{label}: {field_name} is not a date: {value}") from exc
    raise EvidenceValidationError(f"{label}: {field_name} must be a date")


def parse_decimal(
    value: Any,
    *,
    field_name: str,
    label: str,
    minimum: Decimal | None = None,
    maximum: Decimal | None = None,
    required: bool = True,
) -> Decimal | None:
    if value is None:
        if required:
            raise EvidenceValidationError(f"{label}: {field_name} is required")
        return None
    try:
        number = Decimal(str(value))
    except InvalidOperation as exc:
        raise EvidenceValidationError(f"{label}: {field_name} is not numeric: {value}") from exc
    if minimum is not None and number < minimum:
        raise EvidenceValidationError(f"{label}: {field_name} must be >= {minimum}")
    if maximum is not None and number > maximum:
        raise EvidenceValidationError(f"{label}: {field_name} must be <= {maximum}")
    return number


def parse_point(value: Any, *, label: str, required: bool) -> str | None:
    if value is None:
        if required:
            raise EvidenceValidationError(f"{label}: coordinates are required")
        return None
    if not isinstance(value, Mapping):
        raise EvidenceValidationError(f"{label}: coordinates must be a mapping with lon/lat")
    lon = parse_decimal(
        value.get("lon", value.get("longitude")),
        field_name="coordinates.lon",
        label=label,
        minimum=Decimal("-180"),
        maximum=Decimal("180"),
    )
    lat = parse_decimal(
        value.get("lat", value.get("latitude")),
        field_name="coordinates.lat",
        label=label,
        minimum=Decimal("-90"),
        maximum=Decimal("90"),
    )
    return f"POINT({lon} {lat})"


def require_str(raw: Mapping[str, Any], name: str, *, label: str, max_length: int) -> str:
    value = raw.get(name)
    if not isinstance(value, str) or not value.strip():
        raise EvidenceValidationError(f"{label}: {name} is required")
    if len(value) > max_length:
        raise EvidenceValidationError(f"{label}: {name} exceeds {max_length} characters")
    return value.strip()


def optional_str(raw: Mapping[str, Any], name: str, *, label: str, max_length: int) -> str | None:
    value = raw.get(name)
    if value is None:
        return None
    if not isinstance(value, str):
        raise EvidenceValidationError(f"{label}: {name} must be a string")
    if len(value) > max_length:
        raise EvidenceValidationError(f"{label}: {name} exceeds {max_length} characters")
    return value.strip() or None


def validate_provenance(
    raw: Mapping[str, Any], *, data_mode: str, cutoff: datetime, label: str
) -> dict[str, Any]:
    provenance = raw.get("provenance")
    if not isinstance(provenance, Mapping) or not provenance:
        raise EvidenceValidationError(f"{label}: provenance block is required")
    result = dict(provenance)
    if "import" in result:
        raise EvidenceValidationError(f"{label}: provenance.import is reserved for the importer")
    synthetic = bool(result.get("synthetic", False))
    if data_mode != "demo":
        if synthetic:
            raise EvidenceValidationError(
                f"{label}: synthetic evidence is only allowed in demo mode (data_mode={data_mode})"
            )
        missing = []
        for key in REQUIRED_PUBLIC_PROVENANCE:
            value = result.get(key)
            if key in {"url", "publisher"}:
                present = isinstance(value, str) and bool(value.strip())
            else:
                present = isinstance(value, str | datetime | date) and bool(str(value).strip())
            if not present:
                missing.append(key)
        if missing:
            raise EvidenceValidationError(
                f"{label}: non-demo evidence requires public provenance fields {missing}"
            )
        published = parse_datetime(
            result["published_at"], field_name="provenance.published_at", label=label
        )
        retrieved = parse_datetime(
            result["retrieved_at"], field_name="provenance.retrieved_at", label=label
        )
        enforce_cutoff(cutoff, label=label, provenance_published_at=published)
        if retrieved < published:
            raise EvidenceValidationError(
                f"{label}: provenance.retrieved_at must not precede provenance.published_at"
            )
        result["published_at"] = published.isoformat()
        result["retrieved_at"] = retrieved.isoformat()
    result["synthetic"] = synthetic
    return result


def enforce_cutoff(cutoff: datetime, *, label: str, **timestamps: datetime | None) -> None:
    for name, value in timestamps.items():
        if value is not None and value > cutoff:
            raise EvidenceValidationError(
                f"{label}: {name}={value.isoformat()} is after cutoff {cutoff.isoformat()}"
            )


# --------------------------------------------------------------------------- record builders


def build_employment_center(
    raw: Mapping[str, Any], *, data_mode: str, cutoff: datetime, index: int
) -> EvidenceRecord:
    label = f"employment_centers[{index}]"
    name = require_str(raw, "name", label=label, max_length=160)
    label = f"{label} ({name})"
    source = require_str(raw, "source", label=label, max_length=120)
    source_record_id = require_str(raw, "source_record_id", label=label, max_length=255)
    effective_from = parse_datetime(
        raw.get("effective_from"), field_name="effective_from", label=label
    )
    effective_to = parse_optional_datetime(
        raw.get("effective_to"), field_name="effective_to", label=label
    )
    source_timestamp = parse_datetime(
        raw.get("source_timestamp"), field_name="source_timestamp", label=label
    )
    enforce_cutoff(
        cutoff, label=label, effective_from=effective_from, source_timestamp=source_timestamp
    )
    if effective_to is not None and effective_to <= effective_from:
        raise EvidenceValidationError(f"{label}: effective_to must be after effective_from")
    values: dict[str, Any] = {
        "data_mode": data_mode,
        "name": name,
        "category": require_str(raw, "category", label=label, max_length=80),
        "coordinates": parse_point(raw.get("coordinates"), label=label, required=True),
        "current_employment_weight": parse_decimal(
            raw.get("current_employment_weight"),
            field_name="current_employment_weight",
            label=label,
            minimum=Decimal("0"),
        ),
        "future_employment_weight": parse_decimal(
            raw.get("future_employment_weight"),
            field_name="future_employment_weight",
            label=label,
            minimum=Decimal("0"),
        ),
        "effective_from": effective_from,
        "effective_to": effective_to,
        "source": source,
        "source_record_id": source_record_id,
        "source_timestamp": source_timestamp,
        "confidence": parse_decimal(
            raw.get("confidence"),
            field_name="confidence",
            label=label,
            minimum=Decimal("0"),
            maximum=Decimal("1"),
        ),
        "provenance": validate_provenance(raw, data_mode=data_mode, cutoff=cutoff, label=label),
    }
    key = (data_mode, source, source_record_id, effective_from.isoformat())
    return EvidenceRecord("employment_center", key, values, label)


def build_future_project(
    raw: Mapping[str, Any], *, data_mode: str, cutoff: datetime, index: int
) -> EvidenceRecord:
    label = f"future_projects[{index}]"
    name = require_str(raw, "name", label=label, max_length=200)
    label = f"{label} ({name})"
    project_type = require_str(raw, "project_type", label=label, max_length=48)
    if project_type not in PROJECT_TYPES:
        raise EvidenceValidationError(f"{label}: project_type must be one of {PROJECT_TYPES}")
    status = require_str(raw, "status", label=label, max_length=32)
    if status not in PROJECT_STATUSES:
        raise EvidenceValidationError(f"{label}: status must be one of {PROJECT_STATUSES}")
    source = require_str(raw, "source", label=label, max_length=120)
    source_record_id = require_str(raw, "source_record_id", label=label, max_length=255)
    effective_from = parse_datetime(
        raw.get("effective_from"), field_name="effective_from", label=label
    )
    effective_to = parse_optional_datetime(
        raw.get("effective_to"), field_name="effective_to", label=label
    )
    source_date = parse_datetime(raw.get("source_date"), field_name="source_date", label=label)
    enforce_cutoff(cutoff, label=label, effective_from=effective_from, source_date=source_date)
    if effective_to is not None and effective_to <= effective_from:
        raise EvidenceValidationError(f"{label}: effective_to must be after effective_from")
    district = optional_str(raw, "district", label=label, max_length=80)
    submarket = optional_str(raw, "submarket", label=label, max_length=120)
    community = optional_str(raw, "community", label=label, max_length=160)
    if submarket and not district:
        raise EvidenceValidationError(f"{label}: submarket scope requires district")
    if community and not submarket:
        raise EvidenceValidationError(f"{label}: community scope requires submarket")
    coordinates = parse_point(
        raw.get("coordinates"), label=label, required=project_type == "transport"
    )
    values: dict[str, Any] = {
        "data_mode": data_mode,
        "name": name,
        "project_type": project_type,
        "status": status,
        "coordinates": coordinates,
        "district": district,
        "submarket": submarket,
        "community": community,
        "expected_completion": parse_date(
            raw.get("expected_completion"), field_name="expected_completion", label=label
        ),
        "probability": parse_decimal(
            raw.get("probability"),
            field_name="probability",
            label=label,
            minimum=Decimal("0"),
            maximum=Decimal("1"),
            required=False,
        ),
        "effective_from": effective_from,
        "effective_to": effective_to,
        "source": source,
        "source_record_id": source_record_id,
        "source_date": source_date,
        "confidence": parse_decimal(
            raw.get("confidence"),
            field_name="confidence",
            label=label,
            minimum=Decimal("0"),
            maximum=Decimal("1"),
        ),
        "provenance": validate_provenance(raw, data_mode=data_mode, cutoff=cutoff, label=label),
    }
    key = (data_mode, source, source_record_id, effective_from.isoformat())
    return EvidenceRecord("future_project", key, values, label)


def build_factor_observation(
    raw: Mapping[str, Any], *, data_mode: str, cutoff: datetime, index: int
) -> EvidenceRecord:
    label = f"factor_observations[{index}]"
    factor = require_str(raw, "factor", label=label, max_length=64)
    if factor not in FACTORS:
        raise EvidenceValidationError(f"{label}: factor must be one of {FACTORS}")
    scope_type = require_str(raw, "scope_type", label=label, max_length=24)
    if scope_type not in SCOPE_TYPES:
        raise EvidenceValidationError(f"{label}: scope_type must be one of {SCOPE_TYPES}")
    district = optional_str(raw, "district", label=label, max_length=80)
    submarket = optional_str(raw, "submarket", label=label, max_length=120)
    community = optional_str(raw, "community", label=label, max_length=160)
    label = f"{label} ({factor}/{scope_type}/{district or '-'}/{submarket or '-'})"
    required_scope = {
        "shanghai": (),
        "district": ("district",),
        "submarket": ("district", "submarket"),
        "community": ("district", "submarket", "community"),
        "listing": ("district", "submarket", "community", "listing_id"),
    }[scope_type]
    scope_values = {
        "district": district,
        "submarket": submarket,
        "community": community,
        "listing_id": raw.get("listing_id"),
    }
    for scope_field in required_scope:
        if not scope_values[scope_field]:
            raise EvidenceValidationError(f"{label}: {scope_type} scope requires {scope_field}")
    listing_id: uuid.UUID | None = None
    if raw.get("listing_id") is not None:
        try:
            listing_id = uuid.UUID(str(raw["listing_id"]))
        except (TypeError, ValueError, AttributeError) as exc:
            raise EvidenceValidationError(f"{label}: listing_id must be a UUID") from exc
    source = require_str(raw, "source", label=label, max_length=120)
    source_record_id = require_str(raw, "source_record_id", label=label, max_length=255)
    observed_at = parse_datetime(raw.get("observed_at"), field_name="observed_at", label=label)
    effective_from = parse_datetime(
        raw.get("effective_from", raw.get("observed_at")),
        field_name="effective_from",
        label=label,
    )
    effective_to = parse_optional_datetime(
        raw.get("effective_to"), field_name="effective_to", label=label
    )
    source_timestamp = parse_datetime(
        raw.get("source_timestamp", raw.get("observed_at")),
        field_name="source_timestamp",
        label=label,
    )
    enforce_cutoff(
        cutoff,
        label=label,
        observed_at=observed_at,
        effective_from=effective_from,
        source_timestamp=source_timestamp,
    )
    if effective_to is not None and effective_to <= effective_from:
        raise EvidenceValidationError(f"{label}: effective_to must be after effective_from")
    current_score = parse_decimal(
        raw.get("current_score"),
        field_name="current_score",
        label=label,
        minimum=Decimal("0"),
        maximum=Decimal("100"),
        required=False,
    )
    future_score = parse_decimal(
        raw.get("future_score"),
        field_name="future_score",
        label=label,
        minimum=Decimal("0"),
        maximum=Decimal("100"),
        required=False,
    )
    if current_score is None and future_score is None:
        raise EvidenceValidationError(f"{label}: at least one of current_score/future_score")
    explanation = require_str(raw, "explanation", label=label, max_length=4000)
    metadata = raw.get("metadata", {})
    if not isinstance(metadata, Mapping):
        raise EvidenceValidationError(f"{label}: metadata must be a mapping")
    values: dict[str, Any] = {
        "data_mode": data_mode,
        "factor": factor,
        "scope_type": scope_type,
        "listing_id": listing_id,
        "district": district,
        "submarket": submarket,
        "community": community,
        "current_score": current_score,
        "future_score": future_score,
        "current_value": parse_decimal(
            raw.get("current_value"), field_name="current_value", label=label, required=False
        ),
        "future_value": parse_decimal(
            raw.get("future_value"), field_name="future_value", label=label, required=False
        ),
        "unit": optional_str(raw, "unit", label=label, max_length=48),
        "observed_at": observed_at,
        "effective_from": effective_from,
        "effective_to": effective_to,
        "source": source,
        "source_record_id": source_record_id,
        "source_timestamp": source_timestamp,
        "confidence": parse_decimal(
            raw.get("confidence"),
            field_name="confidence",
            label=label,
            minimum=Decimal("0"),
            maximum=Decimal("1"),
        ),
        "explanation": explanation,
        "observation_metadata": dict(metadata),
        "provenance": validate_provenance(raw, data_mode=data_mode, cutoff=cutoff, label=label),
    }
    key = (data_mode, factor, source, source_record_id, observed_at.isoformat())
    return EvidenceRecord("future_factor_observation", key, values, label)


# --------------------------------------------------------------------------- file level


@dataclass(frozen=True)
class EvidenceFile:
    path: Path
    sha256: str
    data_mode: str
    declared_cutoff: datetime | None
    records: tuple[EvidenceRecord, ...]


def load_evidence_file(path: Path, *, cutoff: datetime) -> EvidenceFile:
    payload = path.read_bytes()
    sha256 = hashlib.sha256(payload).hexdigest()
    document = yaml.safe_load(payload)
    if not isinstance(document, Mapping):
        raise EvidenceValidationError("evidence file must be a mapping")
    data_mode = document.get("data_mode")
    if data_mode not in DATA_MODES:
        raise EvidenceValidationError(f"data_mode must be one of {DATA_MODES}")
    declared_cutoff = parse_optional_datetime(
        document.get("cutoff"), field_name="cutoff", label="file"
    )
    if declared_cutoff is not None and declared_cutoff != cutoff:
        raise EvidenceValidationError(
            f"file declares cutoff {declared_cutoff.isoformat()} but --cutoff is "
            f"{cutoff.isoformat()}"
        )
    records: list[EvidenceRecord] = []
    builders: tuple[tuple[str, Callable[..., EvidenceRecord]], ...] = (
        ("employment_centers", build_employment_center),
        ("future_projects", build_future_project),
        ("factor_observations", build_factor_observation),
    )
    for section, builder in builders:
        entries = document.get(section, []) or []
        if not isinstance(entries, list):
            raise EvidenceValidationError(f"{section} must be a list")
        for index, raw in enumerate(entries):
            if not isinstance(raw, Mapping):
                raise EvidenceValidationError(f"{section}[{index}] must be a mapping")
            records.append(builder(raw, data_mode=data_mode, cutoff=cutoff, index=index))
    seen: dict[tuple[str, tuple[str, ...]], str] = {}
    for record in records:
        duplicate = seen.get((record.table, record.key))
        if duplicate is not None:
            raise EvidenceValidationError(
                f"{record.label}: duplicates {duplicate} (same natural key inside the file)"
            )
        seen[(record.table, record.key)] = record.label
    if not records:
        raise EvidenceValidationError("evidence file contains no records")
    return EvidenceFile(path, sha256, data_mode, declared_cutoff, tuple(records))


# --------------------------------------------------------------------------- planning


COMPARED_FIELDS: dict[str, tuple[str, ...]] = {
    "employment_center": (
        "name",
        "category",
        "coordinates",
        "current_employment_weight",
        "future_employment_weight",
        "effective_to",
        "source_timestamp",
        "confidence",
        "provenance",
    ),
    "future_project": (
        "name",
        "project_type",
        "status",
        "coordinates",
        "district",
        "submarket",
        "community",
        "expected_completion",
        "probability",
        "effective_to",
        "source_date",
        "confidence",
        "provenance",
    ),
    "future_factor_observation": (
        "scope_type",
        "district",
        "submarket",
        "community",
        "current_score",
        "future_score",
        "current_value",
        "future_value",
        "unit",
        "effective_from",
        "effective_to",
        "source_timestamp",
        "confidence",
        "explanation",
        "observation_metadata",
        "provenance",
    ),
}

ExistingLookup = Callable[[EvidenceRecord], Mapping[str, Any] | None]


def _normalize(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _normalize(item) for key, item in sorted(value.items())}
    if isinstance(value, (list, tuple)):
        return [_normalize(item) for item in value]
    if isinstance(value, Decimal):
        return format(value.normalize(), "f")
    if isinstance(value, datetime):
        return value.astimezone(UTC).isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if value is None:
        return None
    if isinstance(value, uuid.UUID):
        return str(value)
    return value


def _comparison_value(field_name: str, value: Any) -> Any:
    if field_name == "provenance" and isinstance(value, Mapping):
        value = {key: item for key, item in value.items() if key != "import"}
    return _normalize(value)


def build_plan(
    evidence: EvidenceFile, *, cutoff: datetime, lookup_existing: ExistingLookup
) -> ImportPlan:
    plan = ImportPlan(data_mode=evidence.data_mode, cutoff=cutoff)
    for record in evidence.records:
        existing = lookup_existing(record)
        if existing is None:
            plan.inserts.append(record)
            continue
        differing = [
            name
            for name in COMPARED_FIELDS[record.table]
            if _comparison_value(name, existing.get(name))
            != _comparison_value(name, record.values.get(name))
        ]
        if differing:
            plan.conflicts.append((record, differing))
        else:
            plan.skipped.append(record)
    return plan


# --------------------------------------------------------------------------- database


def git_commit() -> str | None:
    frozen_commit = os.environ.get("SHR_SOURCE_COMMIT", "").strip()
    if frozen_commit and frozen_commit != "unknown":
        return frozen_commit
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            check=True,
            cwd=Path(__file__).parent.parent,
        )
    except (OSError, subprocess.CalledProcessError):
        return None
    return result.stdout.strip() or None


def import_stamp(evidence: EvidenceFile, cutoff: datetime, imported_at: datetime) -> dict[str, Any]:
    return {
        "importer_version": IMPORTER_VERSION,
        "evidence_file": evidence.path.name,
        "evidence_file_sha256": evidence.sha256,
        "cutoff": cutoff.isoformat(),
        "imported_at": imported_at.isoformat(),
        "git_commit": git_commit(),
    }


def database_lookup(session: Any) -> ExistingLookup:
    from geoalchemy2 import functions as geo_functions
    from home_radar_models.future import (
        EmploymentCenter,
        FutureFactorObservation,
        FutureProject,
    )
    from sqlalchemy import Select, null, select

    def lookup(record: EvidenceRecord) -> Mapping[str, Any] | None:
        values = record.values
        statement: Select[Any]
        if record.table == "employment_center":
            statement = select(
                EmploymentCenter, geo_functions.ST_AsText(EmploymentCenter.coordinates)
            ).where(
                EmploymentCenter.data_mode == values["data_mode"],
                EmploymentCenter.source == values["source"],
                EmploymentCenter.source_record_id == values["source_record_id"],
                EmploymentCenter.effective_from == values["effective_from"],
            )
        elif record.table == "future_project":
            statement = select(
                FutureProject, geo_functions.ST_AsText(FutureProject.coordinates)
            ).where(
                FutureProject.data_mode == values["data_mode"],
                FutureProject.source == values["source"],
                FutureProject.source_record_id == values["source_record_id"],
                FutureProject.effective_from == values["effective_from"],
            )
        else:
            statement = select(FutureFactorObservation, null()).where(
                FutureFactorObservation.data_mode == values["data_mode"],
                FutureFactorObservation.factor == values["factor"],
                FutureFactorObservation.source == values["source"],
                FutureFactorObservation.source_record_id == values["source_record_id"],
                FutureFactorObservation.observed_at == values["observed_at"],
            )
        row = session.execute(statement).first()
        if row is None:
            return None
        entity, coordinates_wkt = row[0], row[1]
        snapshot = {name: getattr(entity, name, None) for name in COMPARED_FIELDS[record.table]}
        if "coordinates" in snapshot:
            snapshot["coordinates"] = coordinates_wkt
        return snapshot

    return lookup


def apply_plan(session: Any, plan: ImportPlan, stamp: Mapping[str, Any]) -> None:
    from geoalchemy2.elements import WKTElement
    from home_radar_models.future import (
        EmploymentCenter,
        FutureFactorObservation,
        FutureProject,
    )

    models = {
        "employment_center": EmploymentCenter,
        "future_project": FutureProject,
        "future_factor_observation": FutureFactorObservation,
    }
    for record in plan.inserts:
        values = dict(record.values)
        if values.get("coordinates") is not None:
            values["coordinates"] = WKTElement(values["coordinates"], srid=4326)
        values["provenance"] = {**values["provenance"], "import": dict(stamp)}
        session.add(models[record.table](**values))
    session.flush()


def run_import(
    evidence: EvidenceFile,
    *,
    cutoff: datetime,
    database_url: str,
    dry_run: bool,
) -> dict[str, Any]:
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session

    imported_at = datetime.now(UTC)
    engine = create_engine(database_url, pool_pre_ping=True)
    try:
        with Session(engine) as session:
            plan = build_plan(evidence, cutoff=cutoff, lookup_existing=database_lookup(session))
            summary = plan.summary()
            if plan.conflicts:
                session.rollback()
                summary["status"] = "rejected_conflicts"
                return summary
            stamp = import_stamp(evidence, cutoff, imported_at)
            apply_plan(session, plan, stamp)
            if dry_run:
                session.rollback()
                summary["status"] = "dry_run"
            else:
                session.commit()
                summary["status"] = "committed"
            summary["import"] = stamp
            return summary
    finally:
        engine.dispose()


# --------------------------------------------------------------------------- CLI


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("evidence", type=Path, help="YAML evidence file")
    parser.add_argument(
        "--cutoff",
        required=True,
        help="ISO-8601 input cutoff (with timezone); no evidence may be dated after it",
    )
    parser.add_argument(
        "--database-url",
        default=None,
        help="SQLAlchemy URL; defaults to SHR_DATABASE_URL from settings",
    )
    parser.add_argument(
        "--validate-only",
        action="store_true",
        help="Validate the file and cutoff without touching the database",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Compute the insert/skip/conflict plan against the database, then roll back",
    )
    parser.add_argument(
        "--manifest",
        type=Path,
        default=None,
        help="Write the JSON import summary to this path (audit record)",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    cutoff = parse_datetime(args.cutoff, field_name="--cutoff", label="cli")
    try:
        evidence = load_evidence_file(args.evidence, cutoff=cutoff)
    except EvidenceValidationError as exc:
        print(json.dumps({"status": "invalid", "error": str(exc)}, ensure_ascii=False))
        return 2

    if args.validate_only:
        counts = {"employment_center": 0, "future_project": 0, "future_factor_observation": 0}
        for record in evidence.records:
            counts[record.table] += 1
        summary: dict[str, Any] = {
            "status": "valid",
            "data_mode": evidence.data_mode,
            "cutoff": cutoff.isoformat(),
            "evidence_file_sha256": evidence.sha256,
            "records": counts,
        }
    else:
        database_url = args.database_url
        if database_url is None:
            from home_radar_shared.config import get_settings

            database_url = get_settings().database_url
        summary = run_import(
            evidence, cutoff=cutoff, database_url=database_url, dry_run=args.dry_run
        )

    if args.manifest is not None:
        args.manifest.parent.mkdir(parents=True, exist_ok=True)
        args.manifest.write_text(
            json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if summary["status"] in {"valid", "dry_run", "committed"} else 1


if __name__ == "__main__":
    raise SystemExit(main())

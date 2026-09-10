"""Reviewed future-evidence intake; provenance declarations are not external verification."""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from home_radar_collector.feed import reject_sensitive_fields
from home_radar_forecasting.domain import EmploymentCenterInput, FactorEvidence, FutureProjectInput

from scripts.import_future_evidence import (
    build_employment_center,
    build_factor_observation,
    build_future_project,
    parse_datetime,
)
from scripts.prepare_public_monitor_pilot import SOURCE_ID
from scripts.recommendation_evidence import digest, write_json

BUILDERS = {
    "factor_observations": build_factor_observation,
    "employment_centers": build_employment_center,
    "future_projects": build_future_project,
}


def _time(value: Any) -> datetime:
    return parse_datetime(value, field_name="timestamp", label="future evidence")


def _urls(value: Any, key: str = "") -> None:
    if isinstance(value, dict):
        for child, nested in value.items():
            _urls(nested, child)
    elif isinstance(value, list):
        for nested in value:
            _urls(nested, key)
    elif isinstance(value, str) and ("url" in key.lower() or value.startswith(("http:", "https:"))):
        parsed = urlsplit(value)
        if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
            raise ValueError("source references must be credential-free HTTPS URLs")
        reject_sensitive_fields({"source_url": value})


def _validate(kind: str, raw: dict[str, Any], cutoff: datetime) -> Any:
    reject_sensitive_fields(raw)
    _urls(raw)
    try:
        record = BUILDERS[kind](raw, data_mode="sample", cutoff=cutoff, index=0)
    except InvalidOperation as error:
        raise ValueError("evidence numbers must be finite") from error
    if any(
        isinstance(value, Decimal) and not value.is_finite() for value in record.values.values()
    ):
        raise ValueError("evidence numbers must be finite")
    provenance = record.values["provenance"]
    if _time(provenance["retrieved_at"]) > cutoff:
        raise ValueError("retrieval timestamp is after cutoff")
    if kind == "employment_centers" and (
        provenance.get("measurement_basis") != "observed_employment"
        or provenance.get("coordinates_verified") is not True
    ):
        raise ValueError("employment requires observed employment and verified coordinates")
    return record


class FutureEvidenceStore:
    def __init__(self, directory: Path) -> None:
        self.path = Path(directory) / "future_evidence.json"

    def _read(self) -> list[dict[str, Any]]:
        return json.loads(self.path.read_text())["records"] if self.path.exists() else []

    def fingerprint(self) -> str:
        return digest(self._read())

    def latest_timestamp(self) -> datetime | None:
        return max((_time(row["recorded_at"]) for row in self._read()), default=None)

    def import_evidence(self, payload: dict[str, Any]) -> dict[str, Any]:
        if set(payload) - {"data_mode", "verified_by", *BUILDERS}:
            raise ValueError("unknown future evidence payload field")
        if payload.get("data_mode") != "sample":
            raise ValueError("only non-synthetic sample evidence is accepted")
        reviewer = payload.get("verified_by")
        if not isinstance(reviewer, str) or not 0 < len(reviewer.strip()) <= 120:
            raise ValueError("verified_by is required")
        cutoff = datetime.now(UTC)
        rows = self._read()
        known = {(row["kind"], tuple(row["key"])): row for row in rows}
        added: list[dict[str, Any]] = []
        duplicates = 0
        count = 0
        for kind in BUILDERS:
            entries = payload.get(kind, [])
            if not isinstance(entries, list):
                raise ValueError("evidence collections must be lists")
            count += len(entries)
            if count > 2000:
                raise ValueError("at most 2000 evidence records per batch")
            for raw in entries:
                if not isinstance(raw, dict):
                    raise ValueError("evidence record must be an object")
                record = _validate(kind, raw, cutoff)
                identity = (kind, record.key)
                evidence_id = digest(record.values)
                if identity in known:
                    if known[identity]["evidence_id"] != evidence_id:
                        raise ValueError("natural key conflict; entire batch rejected")
                    duplicates += 1
                    continue
                row = {
                    "kind": kind,
                    "key": record.key,
                    "raw": raw,
                    "evidence_id": evidence_id,
                    "recorded_at": cutoff.isoformat(),
                    "verified_by": reviewer.strip(),
                    "verification_method": "human_supplied_provenance_not_independently_verified",
                }
                added.append(row)
                known[identity] = row
        if not count:
            raise ValueError("at least one evidence record is required")
        if added:
            write_json(self.path, {"records": rows + added})
        return {
            "imported": len(added),
            "duplicates": duplicates,
            "total": len(rows) + len(added),
            "verification_method": "human_supplied_provenance_not_independently_verified",
        }

    def inputs(
        self, item: dict[str, Any], as_of: datetime
    ) -> tuple[
        tuple[FactorEvidence, ...],
        tuple[EmploymentCenterInput, ...],
        tuple[FutureProjectInput, ...],
    ]:
        cutoff = _time(as_of)
        matches: dict[tuple[Any, ...], tuple[dict[str, Any], dict[str, Any]]] = {}
        ranks = {"shanghai": 0, "district": 1, "submarket": 2, "community": 3, "listing": 4}
        for row in self._read():
            if _time(row["recorded_at"]) > cutoff:
                continue
            # Revalidate the stored declaration at the original intake cutoff.
            values = _validate(row["kind"], row["raw"], _time(row["recorded_at"])).values
            if values["effective_from"] > cutoff or (
                values.get("effective_to") is not None and values["effective_to"] <= cutoff
            ):
                continue
            if any(
                values.get(key)
                and (
                    str(values[key]).removesuffix("区") != str(item.get(key, "")).removesuffix("区")
                    if key == "district"
                    else values[key] != item.get(key)
                )
                for key in ("district", "submarket", "community")
            ):
                continue
            if values.get("listing_id") and str(values["listing_id"]) != str(
                uuid.uuid5(
                    uuid.NAMESPACE_URL, f"{item.get('source_id', SOURCE_ID)}/{item['listing_id']}"
                )
            ):
                continue
            key: tuple[Any, ...]
            priority: tuple[Any, ...]
            if row["kind"] == "factor_observations":
                key = (row["kind"], values["factor"])
                priority = (
                    ranks[values["scope_type"]],
                    values["observed_at"],
                    values["source_timestamp"],
                    row["recorded_at"],
                )
            else:
                key = (row["kind"], values["source"], values["source_record_id"])
                priority = (values["effective_from"], row["recorded_at"])
            prior = matches.get(key)
            if prior is None or priority > prior[0]["priority"]:
                matches[key] = ({**row, "priority": priority}, values)
        factors, centers, projects = [], [], []
        for key in sorted(matches):
            row, value = matches[key]
            provenance = value["provenance"]
            if row["kind"] == "factor_observations":
                factors.append(
                    FactorEvidence(
                        factor=value["factor"],
                        current_score=value["current_score"],
                        future_score=value["future_score"],
                        confidence=value["confidence"],
                        source=provenance["url"],
                        source_timestamp=value["source_timestamp"],
                        data_mode="sample",
                        explanation=value["explanation"],
                        metadata={
                            **value["observation_metadata"],
                            "provenance": provenance,
                            "evidence_kind": "supplied_modeled_score",
                            "scope_type": value["scope_type"],
                            "current_value": value["current_value"],
                            "future_value": value["future_value"],
                            "unit": value["unit"],
                            "verified_by": row["verified_by"],
                            "verification_method": row["verification_method"],
                        },
                    )
                )
                continue
            coordinates = row["raw"].get("coordinates")
            lon = (
                Decimal(str(coordinates.get("lon", coordinates.get("longitude"))))
                if coordinates
                else None
            )
            lat = (
                Decimal(str(coordinates.get("lat", coordinates.get("latitude"))))
                if coordinates
                else None
            )
            if row["kind"] == "employment_centers":
                assert lon is not None and lat is not None
                centers.append(
                    EmploymentCenterInput(
                        name=value["name"],
                        category=value["category"],
                        longitude=lon,
                        latitude=lat,
                        current_employment_weight=value["current_employment_weight"],
                        future_employment_weight=value["future_employment_weight"],
                        source=provenance["url"],
                        source_timestamp=value["source_timestamp"],
                        confidence=value["confidence"],
                    )
                )
            else:
                completion = value["expected_completion"]
                projects.append(
                    FutureProjectInput(
                        name=value["name"],
                        project_type=value["project_type"],
                        status=value["status"],
                        longitude=lon,
                        latitude=lat,
                        source=provenance["url"],
                        source_date=value["source_date"],
                        confidence=value["confidence"],
                        probability=value["probability"],
                        expected_completion=datetime.combine(completion, datetime.min.time(), UTC)
                        if completion
                        else None,
                    )
                )
        return tuple(factors), tuple(centers), tuple(projects)

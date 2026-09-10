"""Append-only, human-reviewed property and transaction evidence for local research."""

from __future__ import annotations

import hashlib
import json
import os
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from home_radar_collector.feed import reject_sensitive_fields
from pydantic import BaseModel, ConfigDict, Field, HttpUrl, model_validator


class EvidenceModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    source_url: HttpUrl
    observed_at: datetime
    verified_by: str = Field(min_length=1, max_length=120)

    @model_validator(mode="after")
    def provenance(self) -> EvidenceModel:
        reject_sensitive_fields(self.model_dump(mode="json"))
        if self.source_url.scheme != "https":
            raise ValueError("证据来源须为 HTTPS 链接")
        if self.observed_at.utcoffset() is None or self.observed_at > datetime.now(UTC):
            raise ValueError("证据时间须包含时区且不能在未来")
        return self


class PropertyEvidence(EvidenceModel):
    listing_id: str = Field(min_length=1, max_length=255)
    field: Literal[
        "elevator",
        "year_built",
        "bedrooms",
        "property_type",
        "floor",
        "commute_minutes",
        "seller_indicated_price_wan",
    ]
    value: Any

    @model_validator(mode="after")
    def valid_value(self) -> PropertyEvidence:
        value = self.value
        if self.field == "elevator":
            valid = isinstance(value, bool)
        elif self.field in {"year_built", "bedrooms"}:
            low, high = (1800, datetime.now(UTC).year) if self.field == "year_built" else (0, 20)
            valid = type(value) is int and low <= value <= high
        elif self.field in {"property_type", "floor"}:
            valid = isinstance(value, str) and 0 < len(value.strip()) <= 160
        elif self.field == "commute_minutes":
            valid = (
                isinstance(value, dict)
                and set(value) == {"destination", "minutes"}
                and isinstance(value["destination"], str)
                and 0 < len(value["destination"].strip()) <= 200
                and type(value["minutes"]) in (int, float)
                and 0 < value["minutes"] <= 360
            )
        else:
            valid = type(value) in (int, float) and 0 < value <= 100000
        if not valid:
            raise ValueError("证据值与字段不符")
        return self


class TransactionEvidence(EvidenceModel):
    record_kind: Literal["closed_transaction"]
    source_record_id: str = Field(min_length=1, max_length=255)
    district: str = Field(min_length=1, max_length=80)
    submarket: str = Field(min_length=1, max_length=120)
    community: str = Field(min_length=1, max_length=160)
    area_sqm: float = Field(gt=0, le=10000, allow_inf_nan=False, strict=True)
    price_wan: float = Field(gt=0, le=100000, allow_inf_nan=False, strict=True)
    bedrooms: int = Field(ge=0, le=20, strict=True)
    floor: str | None = Field(default=None, max_length=80)
    total_floors: int | None = Field(default=None, ge=1, le=200, strict=True)
    year_built: int | None = Field(default=None, ge=1800, le=2200, strict=True)
    elevator: bool | None = Field(default=None, strict=True)
    orientation: str | None = Field(default=None, max_length=80)
    building_type: str | None = Field(default=None, max_length=80)
    sold_at: datetime

    @model_validator(mode="after")
    def closed_before_observed(self) -> TransactionEvidence:
        if self.sold_at.utcoffset() is None or self.sold_at > self.observed_at:
            raise ValueError("成交日期须包含时区且不晚于证据观测时间")
        if self.year_built is not None and self.year_built > self.sold_at.year:
            raise ValueError("建造年份不能晚于成交年份")
        return self


class TransactionBatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: Literal["1.0"] = "1.0"
    data_mode: Literal["sample"] = "sample"
    source_id: str = Field(pattern=r"^[a-zA-Z0-9_-]{1,50}$")
    records: list[TransactionEvidence] = Field(min_length=1, max_length=2000)


def digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, ensure_ascii=False, default=str).encode()
    ).hexdigest()


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with os.fdopen(
            os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "w"
        ) as handle:
            handle.write(json.dumps(value, ensure_ascii=False, indent=2) + "\n")
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


class RecommendationEvidenceStore:
    def __init__(self, directory: Path) -> None:
        self.path = directory / "evidence.json"

    def read(self) -> dict[str, Any]:
        if not self.path.exists():
            return {"items": [], "transactions": []}
        return dict(json.loads(self.path.read_text()))

    def fingerprint(self) -> str:
        return digest(self.read())

    def latest_timestamp(self) -> datetime | None:
        value = self.read()
        return max(
            (
                datetime.fromisoformat(row["recorded_at"])
                for row in value["items"] + value["transactions"]
            ),
            default=None,
        )

    def append_property(self, payload: dict[str, Any]) -> dict[str, Any]:
        value = PropertyEvidence.model_validate(payload).model_dump(mode="json")
        value["evidence_id"] = digest(value)
        stored = self.read()
        for prior in stored["items"]:
            if prior["evidence_id"] == value["evidence_id"]:
                return dict(prior)
        value.update(recorded_at=datetime.now(UTC).isoformat(), verification_method="human_review")
        stored["items"].append(value)
        write_json(self.path, stored)
        return value

    def import_transactions(self, payload: dict[str, Any]) -> dict[str, Any]:
        batch = TransactionBatch.model_validate(payload)
        stored = self.read()
        known = {(row["source_id"], row["source_record_id"]): row for row in stored["transactions"]}
        added = []
        for record in batch.records:
            value = record.model_dump(mode="json")
            value["source_id"] = batch.source_id
            value["evidence_id"] = digest(value)
            key = (batch.source_id, record.source_record_id)
            if key in known:
                if known[key]["evidence_id"] != value["evidence_id"]:
                    raise ValueError("同一来源成交编号存在冲突，未导入本批次")
                continue
            value.update(
                recorded_at=datetime.now(UTC).isoformat(), verification_method="human_review"
            )
            added.append(value)
            known[key] = value
        stored["transactions"].extend(added)
        write_json(self.path, stored)
        return {
            "imported": len(added),
            "duplicates": len(batch.records) - len(added),
            "total": len(stored["transactions"]),
            "verification_method": "human_review",
        }

    def property_items(self, listing_id: str, as_of: datetime) -> list[dict[str, Any]]:
        rows = [
            row
            for row in self.read()["items"]
            if row["listing_id"] == listing_id
            and datetime.fromisoformat(row["observed_at"]) <= as_of
            and datetime.fromisoformat(row["recorded_at"]) <= as_of
        ]
        return sorted(rows, key=lambda row: (row["observed_at"], row["recorded_at"]))

    def enrich(self, item: dict[str, Any], as_of: datetime) -> dict[str, Any]:
        value = dict(item)
        evidence = self.property_items(item["listing_id"], as_of)
        # Asking price is never overwritten with a seller indication or a transaction.
        for row in evidence:
            if row["field"] in {"elevator", "year_built", "bedrooms", "floor", "property_type"}:
                value[row["field"]] = row["value"]
        value["verified_evidence"] = evidence
        return value

    def transaction_items(self, as_of: datetime) -> list[dict[str, Any]]:
        return [
            row
            for row in self.read()["transactions"]
            if datetime.fromisoformat(row["observed_at"]) <= as_of
            and datetime.fromisoformat(row["recorded_at"]) <= as_of
        ]

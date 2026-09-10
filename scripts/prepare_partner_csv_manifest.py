from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import uuid
from datetime import datetime
from io import StringIO
from pathlib import Path
from typing import Any

from home_radar_collector.adapters.partner_csv import PartnerCsvManifest
from home_radar_collector.contracts import CrawlScope
from home_radar_collector.feed import CanonicalListingItem, reject_sensitive_fields
from home_radar_collector.normalizers import (
    REQUIRED_CSV_COLUMNS,
    CsvFeedContext,
    CsvFeedNormalizer,
)
from pydantic import ValidationError


def parse_exported_at(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise ValueError("exported-at must be an ISO-8601 timestamp") from None
    if parsed.tzinfo is None:
        raise ValueError("exported-at must include a timezone")
    return parsed


def inspect_csv(path: Path) -> tuple[bytes, int]:
    if path.suffix.lower() != ".csv" or path.name.endswith(".tmp.csv"):
        raise ValueError("CSV path must identify a finalized .csv file")
    try:
        payload = path.read_bytes()
        decoded = payload.decode("utf-8-sig")
    except (OSError, UnicodeDecodeError):
        raise ValueError("CSV file must exist and use UTF-8 encoding") from None

    reader = csv.DictReader(StringIO(decoded))
    if not REQUIRED_CSV_COLUMNS.issubset(set(reader.fieldnames or ())):
        raise ValueError("CSV file is missing required canonical columns")
    return payload, sum(1 for _ in reader)


def validate_rows(
    payload: bytes,
    *,
    source_id: str,
    scope: CrawlScope,
    exported_at: datetime,
) -> int:
    """Fail before signing if any row would fail canonical ingestion."""
    normalized = CsvFeedNormalizer(
        CsvFeedContext(
            source_id=source_id,
            scope=scope,
            default_observed_at=exported_at,
        )
    ).normalize(payload)
    raw_items = normalized.get("items")
    if not isinstance(raw_items, list):
        raise ValueError("CSV normalization did not produce an item list")
    try:
        reject_sensitive_fields(raw_items, path="items")
    except ValueError:
        raise ValueError("CSV contains a forbidden credential-shaped field") from None

    listing_ids: set[str] = set()
    urls: set[str] = set()
    for row_number, raw_item in enumerate(raw_items, start=2):
        try:
            item = CanonicalListingItem.model_validate(raw_item)
        except ValidationError:
            raise ValueError(f"CSV row {row_number} failed canonical validation") from None
        if item.listing_id in listing_ids:
            raise ValueError(f"CSV row {row_number} duplicates a listing_id")
        normalized_url = str(item.url)
        if normalized_url in urls:
            raise ValueError(f"CSV row {row_number} duplicates a listing URL")
        if item.observed_at is not None and item.observed_at > exported_at:
            raise ValueError(f"CSV row {row_number} was observed after the export timestamp")
        listing_ids.add(item.listing_id)
        urls.add(normalized_url)
    return len(raw_items)


def build_manifest(
    *,
    csv_path: Path,
    source_id: str,
    provider: str,
    license_reference: str,
    exported_at: datetime,
    scope: CrawlScope,
    declare_complete: bool,
    reported_total: int | None,
) -> dict[str, Any]:
    payload, row_count = inspect_csv(csv_path)
    validated_count = validate_rows(
        payload,
        source_id=source_id,
        scope=scope,
        exported_at=exported_at,
    )
    if validated_count != row_count:
        raise ValueError("validated row count does not match the CSV row count")
    if declare_complete and reported_total is None:
        raise ValueError("reported-total is required when declaring a complete export")
    if reported_total is not None and reported_total != row_count:
        raise ValueError("provider reported-total does not match the CSV row count")

    coverage: dict[str, int] | None = None
    if reported_total is not None:
        coverage = {
            "reported_total": reported_total,
            "items_returned": row_count,
        }
    manifest = {
        "manifest_version": "1.0",
        "source_id": source_id,
        "provider": provider,
        "license_reference": license_reference,
        "exported_at": exported_at.isoformat(),
        "scope": scope.model_dump(mode="json"),
        "completeness": "complete" if declare_complete else "partial",
        "coverage": coverage,
        "file_name": csv_path.name,
        "file_sha256": hashlib.sha256(payload).hexdigest(),
    }
    return PartnerCsvManifest.model_validate(manifest).model_dump(mode="json", exclude_none=True)


def write_manifest(csv_path: Path, manifest: dict[str, Any]) -> Path:
    destination = Path(f"{csv_path}.manifest.json")
    temporary = destination.with_name(f".{destination.name}.{uuid.uuid4().hex}.tmp")
    temporary.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, destination)
    return destination


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Create an auditable companion manifest for an authorized partner CSV"
    )
    parser.add_argument("--csv", required=True, type=Path)
    parser.add_argument("--source-id", default="partner_csv")
    parser.add_argument("--provider", required=True)
    parser.add_argument("--license-reference", required=True)
    parser.add_argument("--exported-at", required=True, type=parse_exported_at)
    parser.add_argument("--city", default="shanghai")
    parser.add_argument("--district")
    parser.add_argument("--submarket")
    parser.add_argument("--query")
    parser.add_argument("--declare-complete", action="store_true")
    parser.add_argument("--reported-total", type=int)
    return parser


def main() -> None:
    args = build_parser().parse_args()
    manifest = build_manifest(
        csv_path=args.csv,
        source_id=args.source_id,
        provider=args.provider,
        license_reference=args.license_reference,
        exported_at=args.exported_at,
        scope=CrawlScope(
            city=args.city,
            district=args.district,
            submarket=args.submarket,
            query=args.query,
        ),
        declare_complete=args.declare_complete,
        reported_total=args.reported_total,
    )
    destination = write_manifest(args.csv, manifest)
    print(json.dumps({"manifest": str(destination), "completeness": manifest["completeness"]}))


if __name__ == "__main__":
    main()

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from home_radar_collector.contracts import CrawlScope

from scripts.prepare_partner_csv_manifest import build_manifest, parse_exported_at, write_manifest

CSV_TEXT = (
    "listing_id,url,district,submarket,community,price_wan,area_sqm\n"
    "one,https://example.invalid/one,Xuhui,Huajing,Garden,300,60\n"
)


def test_builds_complete_manifest_only_with_matching_provider_total(tmp_path: Path) -> None:
    path = tmp_path / "listings.csv"
    path.write_text(CSV_TEXT, encoding="utf-8")

    manifest = build_manifest(
        csv_path=path,
        source_id="partner_csv",
        provider="Broker",
        license_reference="contract-1",
        exported_at=datetime(2026, 9, 4, tzinfo=UTC),
        scope=CrawlScope(city="shanghai"),
        declare_complete=True,
        reported_total=1,
    )
    destination = write_manifest(path, manifest)

    assert manifest["completeness"] == "complete"
    assert manifest["coverage"] == {"reported_total": 1, "items_returned": 1}
    assert json.loads(destination.read_text(encoding="utf-8")) == manifest


def test_refuses_to_invent_or_ignore_complete_export_total(tmp_path: Path) -> None:
    path = tmp_path / "listings.csv"
    path.write_text(CSV_TEXT, encoding="utf-8")
    kwargs = {
        "csv_path": path,
        "source_id": "partner_csv",
        "provider": "Broker",
        "license_reference": "contract-1",
        "exported_at": datetime(2026, 9, 4, tzinfo=UTC),
        "scope": CrawlScope(city="shanghai"),
        "declare_complete": True,
    }

    with pytest.raises(ValueError, match="required"):
        build_manifest(**kwargs, reported_total=None)  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="does not match"):
        build_manifest(**kwargs, reported_total=2)  # type: ignore[arg-type]


def test_partial_manifest_does_not_invent_coverage(tmp_path: Path) -> None:
    path = tmp_path / "listings.csv"
    path.write_text(CSV_TEXT, encoding="utf-8")

    manifest = build_manifest(
        csv_path=path,
        source_id="partner_csv",
        provider="Broker",
        license_reference="contract-1",
        exported_at=datetime(2026, 9, 4, tzinfo=UTC),
        scope=CrawlScope(city="shanghai"),
        declare_complete=False,
        reported_total=None,
    )

    assert manifest["completeness"] == "partial"
    assert "coverage" not in manifest


def test_export_timestamp_requires_timezone() -> None:
    assert parse_exported_at("2026-09-04T01:00:00Z").tzinfo is not None
    with pytest.raises(ValueError, match="timezone"):
        parse_exported_at("2026-09-04T01:00:00")


@pytest.mark.parametrize(
    ("original", "changed", "message"),
    [
        ("300,60", "not-a-price,60", "canonical validation"),
        (
            "one,https://example.invalid/one",
            "one,https://user:pass@example.invalid/one",
            "credential-shaped",
        ),
    ],
)
def test_manifest_refuses_rows_that_canonical_ingestion_would_reject(
    tmp_path: Path, original: str, changed: str, message: str
) -> None:
    path = tmp_path / "listings.csv"
    path.write_text(CSV_TEXT.replace(original, changed), encoding="utf-8")

    with pytest.raises(ValueError, match=message):
        _build(path)


@pytest.mark.parametrize(
    ("extra_row", "message"),
    [
        (
            "one,https://example.invalid/two,Xuhui,Huajing,Garden,299,60\n",
            "duplicates a listing_id",
        ),
        (
            "two,https://example.invalid/one,Xuhui,Huajing,Garden,299,60\n",
            "duplicates a listing URL",
        ),
    ],
)
def test_manifest_refuses_duplicate_feed_identities(
    tmp_path: Path, extra_row: str, message: str
) -> None:
    path = tmp_path / "listings.csv"
    path.write_text(CSV_TEXT + extra_row, encoding="utf-8")

    with pytest.raises(ValueError, match=message):
        _build(path, reported_total=2)


def test_manifest_refuses_observation_after_export_time(tmp_path: Path) -> None:
    path = tmp_path / "listings.csv"
    path.write_text(
        CSV_TEXT.replace("area_sqm\n", "area_sqm,observed_at\n").replace(
            "300,60\n", "300,60,2026-09-04T01:00:01Z\n"
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="observed after"):
        _build(path)


def test_manifest_refuses_credential_shaped_extra_column(tmp_path: Path) -> None:
    path = tmp_path / "listings.csv"
    path.write_text(
        CSV_TEXT.replace("area_sqm\n", "area_sqm,api_token\n").replace(
            "300,60\n", "300,60,secret\n"
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="credential-shaped"):
        _build(path)


def _build(path: Path, *, reported_total: int = 1) -> dict[str, object]:
    return build_manifest(
        csv_path=path,
        source_id="partner_csv",
        provider="Broker",
        license_reference="contract-1",
        exported_at=datetime(2026, 9, 4, 1, 0, tzinfo=UTC),
        scope=CrawlScope(city="shanghai"),
        declare_complete=True,
        reported_total=reported_total,
    )

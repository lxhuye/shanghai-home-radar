from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from home_radar_collector.adapters.partner_csv import PartnerCsvFeedAdapter
from home_radar_collector.contracts import CrawlScope
from home_radar_collector.errors import SourcePayloadValidationError
from home_radar_collector.feed import ProviderCapabilities
from home_radar_models.enums import CrawlCompleteness

CSV_TEXT = (
    "listing_id,url,district,submarket,community,price_wan,area_sqm,elevator,observed_at\n"
    "csv-1,https://example.invalid/csv-1,Xuhui,Huajing,Garden,300,60,false,"
    "2026-09-04T01:00:00Z\n"
)


def adapter(path: Path, *, scope: CrawlScope | None = None) -> PartnerCsvFeedAdapter:
    return PartnerCsvFeedAdapter(
        source_id="partner_csv",
        endpoint=str(path),
        scope=scope or CrawlScope(city="shanghai"),
        capabilities=ProviderCapabilities(supports_reported_total=True),
    )


def write_csv(path: Path) -> None:
    path.write_text(CSV_TEXT, encoding="utf-8")


def write_manifest(path: Path, **overrides: object) -> Path:
    payload: dict[str, object] = {
        "manifest_version": "1.0",
        "source_id": "partner_csv",
        "provider": "Authorized Broker Export",
        "license_reference": "internal-contract-2026-01",
        "exported_at": "2026-09-04T01:05:00Z",
        "scope": {"city": "shanghai", "filters": {}},
        "completeness": "complete",
        "coverage": {"reported_total": 1, "items_returned": 1},
        "file_name": path.name,
        "file_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
    }
    payload.update(overrides)
    manifest_path = Path(f"{path}.manifest.json")
    manifest_path.write_text(json.dumps(payload), encoding="utf-8")
    return manifest_path


@pytest.mark.asyncio
async def test_complete_manifest_proves_csv_coverage(tmp_path: Path) -> None:
    path = tmp_path / "listings.csv"
    write_csv(path)
    write_manifest(path)

    source = adapter(path)
    result = await source.fetch()
    observation = source.normalize(result.raw_items[0])

    assert result.completeness is CrawlCompleteness.COMPLETE
    assert result.metadata["coverage_complete"] is True
    assert result.metadata["provider_metadata"]["manifest_present"] is True
    assert result.metadata["provider_metadata"]["license_reference"] == (
        "internal-contract-2026-01"
    )
    assert observation.source_listing_id == "csv-1"
    assert observation.elevator is False


@pytest.mark.asyncio
async def test_manifest_export_time_fills_missing_row_observation_time(tmp_path: Path) -> None:
    path = tmp_path / "listings.csv"
    write_csv(path)
    path.write_text(
        CSV_TEXT.replace(",observed_at\n", "\n").replace(",2026-09-04T01:00:00Z\n", "\n"),
        encoding="utf-8",
    )
    write_manifest(path)

    source = adapter(path)
    result = await source.fetch()
    observation = source.normalize(result.raw_items[0])

    assert observation.observed_at.isoformat() == "2026-09-04T01:05:00+00:00"


@pytest.mark.asyncio
async def test_missing_manifest_is_always_partial(tmp_path: Path) -> None:
    path = tmp_path / "listings.csv"
    write_csv(path)

    result = await adapter(path).fetch()

    assert result.completeness is CrawlCompleteness.PARTIAL
    assert result.metadata["provider_metadata"]["manifest_present"] is False
    assert "provider_declared_partial" in result.metadata["coverage_reasons"]


@pytest.mark.asyncio
async def test_complete_without_coverage_is_downgraded_to_partial(tmp_path: Path) -> None:
    path = tmp_path / "listings.csv"
    write_csv(path)
    write_manifest(path, coverage=None)

    result = await adapter(path).fetch()

    assert result.completeness is CrawlCompleteness.PARTIAL
    assert "reported_total_missing" in result.metadata["coverage_reasons"]


@pytest.mark.asyncio
async def test_stale_complete_export_is_downgraded_to_partial(tmp_path: Path) -> None:
    path = tmp_path / "listings.csv"
    write_csv(path)
    write_manifest(path, exported_at=(datetime.now(UTC) - timedelta(hours=2)).isoformat())
    source = PartnerCsvFeedAdapter(
        source_id="partner_csv",
        endpoint=str(path),
        scope=CrawlScope(city="shanghai"),
        capabilities=ProviderCapabilities(supports_reported_total=True),
        max_export_age_hours=1,
    )

    result = await source.fetch()

    assert result.completeness is CrawlCompleteness.PARTIAL
    assert result.metadata["provider_metadata"]["source_age_guard_triggered"] is True
    assert "provider_declared_partial" in result.metadata["coverage_reasons"]


@pytest.mark.asyncio
async def test_checksum_mismatch_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "listings.csv"
    write_csv(path)
    write_manifest(path, file_sha256="0" * 64)

    with pytest.raises(SourcePayloadValidationError, match="checksum"):
        await adapter(path).fetch()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"source_id": "other"}, "source mismatch"),
        ({"scope": {"city": "beijing", "filters": {}}}, "scope mismatch"),
        ({"file_name": "other.csv"}, "file name mismatch"),
    ],
)
async def test_manifest_identity_mismatch_is_rejected(
    tmp_path: Path, overrides: dict[str, object], message: str
) -> None:
    path = tmp_path / "listings.csv"
    write_csv(path)
    write_manifest(path, **overrides)

    with pytest.raises(SourcePayloadValidationError, match=message):
        await adapter(path).fetch()


@pytest.mark.asyncio
async def test_manifest_rejects_credential_shaped_fields(tmp_path: Path) -> None:
    path = tmp_path / "listings.csv"
    write_csv(path)
    write_manifest(path, api_token="must-not-be-stored")

    with pytest.raises(SourcePayloadValidationError, match="manifest validation"):
        await adapter(path).fetch()


@pytest.mark.asyncio
async def test_manifest_change_during_read_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "listings.csv"
    write_csv(path)
    source = adapter(path)
    source._read_optional_manifest = AsyncMock(side_effect=[b"old", b"new"])  # type: ignore[method-assign]

    with pytest.raises(SourcePayloadValidationError, match="changed during read"):
        await source.fetch()


@pytest.mark.parametrize(
    "endpoint",
    ["https://partner.example/listings.csv", "listings.json", "listings.tmp.csv"],
)
def test_endpoint_must_be_a_finalized_local_csv(endpoint: str) -> None:
    with pytest.raises(ValueError, match="partner CSV endpoint|finalized"):
        PartnerCsvFeedAdapter(
            source_id="partner_csv",
            endpoint=endpoint,
            scope=CrawlScope(city="shanghai"),
        )

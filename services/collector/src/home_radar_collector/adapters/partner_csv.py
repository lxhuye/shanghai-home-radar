from __future__ import annotations

import hashlib
import json
import re
from asyncio import to_thread
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from home_radar_models.enums import CrawlCompleteness
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from home_radar_collector.adapters.canonical_json import CanonicalJsonFeedAdapter
from home_radar_collector.contracts import CrawlScope, FetchResult, ListingObservation
from home_radar_collector.errors import SourcePayloadValidationError
from home_radar_collector.feed import (
    CanonicalFeedScope,
    FeedCoverage,
    ProviderCapabilities,
    reject_sensitive_fields,
)
from home_radar_collector.normalizers import CsvFeedContext, CsvFeedNormalizer

MANIFEST_VERSION_PATTERN = re.compile(r"^1\.\d+$")
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")


class PartnerCsvManifest(BaseModel):
    """Signed-off facts accompanying one immutable provider CSV export."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid", frozen=True)

    manifest_version: str = "1.0"
    source_id: str = Field(min_length=1, max_length=50)
    provider: str = Field(min_length=1, max_length=160)
    license_reference: str = Field(min_length=1, max_length=255)
    exported_at: datetime
    scope: CanonicalFeedScope
    completeness: CrawlCompleteness
    coverage: FeedCoverage | None = None
    file_name: str = Field(min_length=1, max_length=255)
    file_sha256: str

    @field_validator("manifest_version")
    @classmethod
    def require_supported_version(cls, value: str) -> str:
        if MANIFEST_VERSION_PATTERN.fullmatch(value) is None:
            raise ValueError("unsupported partner CSV manifest version")
        return value

    @field_validator("exported_at")
    @classmethod
    def require_export_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("exported_at must be timezone-aware")
        return value

    @field_validator("completeness")
    @classmethod
    def reject_unknown_completeness(cls, value: CrawlCompleteness) -> CrawlCompleteness:
        if value is CrawlCompleteness.UNKNOWN:
            raise ValueError("partner CSV manifest cannot declare unknown completeness")
        return value

    @field_validator("file_name")
    @classmethod
    def require_plain_file_name(cls, value: str) -> str:
        if Path(value).name != value or value in {".", ".."}:
            raise ValueError("file_name must not contain a path")
        return value

    @field_validator("file_sha256")
    @classmethod
    def require_sha256(cls, value: str) -> str:
        normalized = value.lower()
        if SHA256_PATTERN.fullmatch(normalized) is None:
            raise ValueError("file_sha256 must be a hexadecimal SHA-256 digest")
        return normalized


class PartnerCsvFeedAdapter:
    """Read an authorized local CSV export with an optional fail-closed manifest."""

    def __init__(
        self,
        *,
        source_id: str,
        endpoint: str,
        scope: CrawlScope,
        capabilities: ProviderCapabilities | None = None,
        environment: str = "development",
        max_export_age_hours: float | None = None,
    ) -> None:
        if endpoint.startswith(("https://", "http://")):
            raise ValueError("partner CSV endpoint must be a local file")
        self.endpoint = endpoint
        self.path = Path(endpoint)
        if self.path.suffix.lower() != ".csv" or self.path.name.endswith(".tmp.csv"):
            raise ValueError("partner CSV endpoint must be a finalized .csv file")

        self.manifest_path = Path(f"{endpoint}.manifest.json")
        self.source_id = source_id
        self.scope = scope
        self.scope_key = scope.stable_key(source_id)
        self.capabilities = capabilities or ProviderCapabilities(supports_reported_total=True)
        self.environment = environment
        if max_export_age_hours is not None and max_export_age_hours <= 0:
            raise ValueError("partner CSV maximum export age must be positive")
        self.max_export_age_hours = max_export_age_hours
        self._item_adapter = CanonicalJsonFeedAdapter(
            source_id=source_id,
            endpoint=endpoint,
            scope=scope,
            capabilities=self.capabilities,
            environment=environment,
        )

    @property
    def name(self) -> str:
        return self.source_id

    async def fetch(self) -> FetchResult:
        first_manifest_bytes = await self._read_optional_manifest()
        csv_bytes = await self._read_csv()
        second_manifest_bytes = await self._read_optional_manifest()
        if first_manifest_bytes != second_manifest_bytes:
            raise SourcePayloadValidationError("partner CSV manifest changed during read")

        csv_sha256 = hashlib.sha256(csv_bytes).hexdigest()
        manifest = self._parse_manifest(first_manifest_bytes)
        if manifest is None:
            context = CsvFeedContext(
                source_id=self.source_id,
                scope=self.scope,
                metadata={
                    "manifest_present": False,
                    "csv_sha256": csv_sha256,
                },
            )
        else:
            self._validate_manifest(manifest, csv_sha256=csv_sha256)
            export_age_hours = max(
                0.0,
                (datetime.now(UTC) - manifest.exported_at.astimezone(UTC)).total_seconds() / 3600,
            )
            source_age_guard_triggered = (
                self.max_export_age_hours is not None
                and export_age_hours > self.max_export_age_hours
            )
            context = CsvFeedContext(
                source_id=self.source_id,
                scope=self.scope,
                completeness=(
                    CrawlCompleteness.PARTIAL
                    if source_age_guard_triggered
                    else manifest.completeness
                ),
                coverage=manifest.coverage,
                metadata={
                    "manifest_present": True,
                    "manifest_version": manifest.manifest_version,
                    "manifest_sha256": hashlib.sha256(first_manifest_bytes or b"").hexdigest(),
                    "csv_sha256": csv_sha256,
                    "provider": manifest.provider,
                    "license_reference": manifest.license_reference,
                    "exported_at": manifest.exported_at.isoformat(),
                    "export_age_hours": round(export_age_hours, 3),
                    "source_age_guard_triggered": source_age_guard_triggered,
                    "file_name": manifest.file_name,
                },
                default_observed_at=manifest.exported_at,
            )

        canonical = CanonicalJsonFeedAdapter(
            source_id=self.source_id,
            endpoint=self.endpoint,
            scope=self.scope,
            capabilities=self.capabilities,
            environment=self.environment,
            normalizer=CsvFeedNormalizer(context),
        )
        return canonical.parse_payload(csv_bytes)

    def normalize(self, raw_item: Mapping[str, Any]) -> ListingObservation:
        return self._item_adapter.normalize(raw_item)

    def source_record_id(self, raw_item: Mapping[str, Any]) -> str | None:
        return self._item_adapter.source_record_id(raw_item)

    async def _read_csv(self) -> bytes:
        try:
            return await to_thread(self.path.read_bytes)
        except OSError:
            raise SourcePayloadValidationError("partner CSV file could not be read") from None

    async def _read_optional_manifest(self) -> bytes | None:
        try:
            return await to_thread(self.manifest_path.read_bytes)
        except FileNotFoundError:
            return None
        except OSError:
            raise SourcePayloadValidationError("partner CSV manifest could not be read") from None

    @staticmethod
    def _parse_manifest(payload: bytes | None) -> PartnerCsvManifest | None:
        if payload is None:
            return None
        try:
            decoded = json.loads(payload)
            reject_sensitive_fields(decoded, path="manifest")
            return PartnerCsvManifest.model_validate(decoded)
        except (UnicodeDecodeError, json.JSONDecodeError, ValidationError, ValueError):
            raise SourcePayloadValidationError("partner CSV manifest validation failed") from None

    def _validate_manifest(self, manifest: PartnerCsvManifest, *, csv_sha256: str) -> None:
        if manifest.source_id != self.source_id:
            raise SourcePayloadValidationError("partner CSV manifest source mismatch")
        if manifest.scope.to_crawl_scope().stable_key(self.source_id) != self.scope_key:
            raise SourcePayloadValidationError("partner CSV manifest scope mismatch")
        if manifest.file_name != self.path.name:
            raise SourcePayloadValidationError("partner CSV manifest file name mismatch")
        if manifest.file_sha256 != csv_sha256:
            raise SourcePayloadValidationError("partner CSV file checksum mismatch")

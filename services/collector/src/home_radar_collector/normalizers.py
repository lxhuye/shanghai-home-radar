from __future__ import annotations

import csv
import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from io import StringIO
from typing import Any, Protocol

from home_radar_models.enums import CrawlCompleteness

from home_radar_collector.contracts import CrawlScope
from home_radar_collector.errors import SourcePayloadValidationError
from home_radar_collector.feed import FeedCoverage

REQUIRED_CSV_COLUMNS = {
    "listing_id",
    "url",
    "district",
    "submarket",
    "community",
    "price_wan",
    "area_sqm",
}


class FeedNormalizer(Protocol):
    """Convert an authorized provider payload into the canonical feed envelope."""

    def normalize(self, payload: str | bytes) -> dict[str, Any]: ...


class CanonicalJsonFeedNormalizer:
    def normalize(self, payload: str | bytes) -> dict[str, Any]:
        try:
            decoded = payload.decode("utf-8") if isinstance(payload, bytes) else payload
            value = json.loads(decoded)
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise SourcePayloadValidationError("canonical JSON payload is malformed") from None
        if not isinstance(value, dict):
            raise SourcePayloadValidationError("canonical JSON feed must be an object")
        return value


@dataclass(frozen=True)
class CsvFeedContext:
    source_id: str
    scope: CrawlScope
    schema_version: str = "1.0"
    completeness: CrawlCompleteness = CrawlCompleteness.PARTIAL
    coverage: FeedCoverage | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)
    default_observed_at: datetime | None = None


class CsvFeedNormalizer:
    """Local conversion example for a provider-exported CSV file."""

    def __init__(self, context: CsvFeedContext) -> None:
        self.context = context

    def normalize(self, payload: str | bytes) -> dict[str, Any]:
        try:
            decoded = payload.decode("utf-8-sig") if isinstance(payload, bytes) else payload
        except UnicodeDecodeError:
            raise SourcePayloadValidationError("CSV payload is not valid UTF-8") from None

        reader = csv.DictReader(StringIO(decoded))
        fieldnames = set(reader.fieldnames or ())
        if not REQUIRED_CSV_COLUMNS.issubset(fieldnames):
            raise SourcePayloadValidationError("CSV feed is missing required canonical columns")

        items = [self._clean_row(row) for row in reader]
        if self.context.default_observed_at is not None:
            default_observed_at = self.context.default_observed_at.isoformat()
            for item in items:
                item.setdefault("observed_at", default_observed_at)
        item_count = len(items)
        coverage = (
            self.context.coverage.model_dump(mode="json", exclude_none=True)
            if self.context.coverage is not None
            else {}
        )
        coverage.setdefault("items_returned", item_count)
        return {
            "schema_version": self.context.schema_version,
            "source_id": self.context.source_id,
            "scope": self.context.scope.model_dump(mode="json"),
            "completeness": self.context.completeness.value,
            "coverage": coverage,
            "metadata": {
                "normalizer": "csv",
                **dict(self.context.metadata),
            },
            "items": items,
        }

    @staticmethod
    def _clean_row(row: Mapping[str, str | None]) -> dict[str, Any]:
        return {
            key.strip(): value.strip()
            for key, value in row.items()
            if key is not None and value is not None and value.strip() != ""
        }

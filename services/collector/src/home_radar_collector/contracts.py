from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, Protocol

from home_radar_models.enums import CrawlCompleteness, ListingStatus
from pydantic import BaseModel, ConfigDict, Field, HttpUrl, field_validator, model_validator


class CrawlScope(BaseModel):
    """The exact source query boundary that may be reconciled together."""

    model_config = ConfigDict(str_strip_whitespace=True, frozen=True)

    city: str = Field(default="shanghai", min_length=1, max_length=80)
    district: str | None = Field(default=None, max_length=80)
    submarket: str | None = Field(default=None, max_length=120)
    query: str | None = Field(default=None, max_length=120)
    filters: dict[str, str] = Field(default_factory=dict)

    @field_validator("filters")
    @classmethod
    def normalize_filters(cls, value: dict[str, str]) -> dict[str, str]:
        return {
            str(key).strip().lower(): str(filter_value).strip().lower()
            for key, filter_value in value.items()
        }

    def stable_key(self, source_id: str) -> str:
        source_segment = source_id.strip().lower()
        city_segment = self.city.strip().lower()
        district_segment = self._segment(self.district)
        submarket_segment = self._segment(self.submarket)
        query_segment = self._segment(self.query)
        normalized = {
            "source": source_segment,
            "city": city_segment,
            "district": district_segment,
            "submarket": submarket_segment,
            "query": query_segment,
            "filters": dict(sorted(self.filters.items())),
        }
        scope_json = json.dumps(normalized, ensure_ascii=True, separators=(",", ":"))
        digest = hashlib.sha256(scope_json.encode()).hexdigest()[:16]
        readable = ":".join(
            part
            for part in (
                source_segment,
                city_segment,
                district_segment,
                submarket_segment,
                query_segment,
            )
            if part
        )
        return f"{readable or 'scope'}:{digest}"

    @staticmethod
    def _segment(value: str | None) -> str:
        return value.strip().lower() if value else ""


class FetchResult(BaseModel):
    """Validated source response before individual records are normalized."""

    source_id: str = Field(min_length=1, max_length=50)
    scope_key: str = Field(min_length=1, max_length=255)
    raw_items: list[dict[str, Any]]
    completeness: CrawlCompleteness
    metadata: dict[str, Any] = Field(default_factory=dict)


class ListingObservation(BaseModel):
    """Canonical data accepted by ingestion, independent from any source payload."""

    model_config = ConfigDict(str_strip_whitespace=True)

    source: str = Field(min_length=1, max_length=50)
    source_listing_id: str = Field(min_length=1, max_length=255)
    source_url: HttpUrl
    district: str = Field(min_length=1, max_length=80)
    submarket: str = Field(min_length=1, max_length=120)
    community: str = Field(min_length=1, max_length=160)
    longitude: Decimal | None = Field(default=None, ge=-180, le=180)
    latitude: Decimal | None = Field(default=None, ge=-90, le=90)
    total_price: Decimal = Field(gt=0)
    unit_price: Decimal | None = Field(default=None, gt=0)
    area_sqm: Decimal = Field(gt=0)
    bedrooms: int | None = Field(default=None, ge=0, le=20)
    living_rooms: int | None = Field(default=None, ge=0, le=10)
    floor: str | None = Field(default=None, max_length=80)
    total_floors: int | None = Field(default=None, ge=1, le=200)
    orientation: str | None = Field(default=None, max_length=80)
    year_built: int | None = Field(default=None, ge=1800, le=2200)
    elevator: bool | None = None
    building_type: str | None = Field(default=None, max_length=80)
    status: ListingStatus = ListingStatus.ACTIVE
    observed_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @field_validator("observed_at")
    @classmethod
    def require_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("observed_at must be timezone-aware")
        return value

    @model_validator(mode="after")
    def complete_derived_fields(self) -> ListingObservation:
        if self.unit_price is None:
            self.unit_price = (self.total_price / self.area_sqm).quantize(Decimal("0.01"))
        if (self.longitude is None) != (self.latitude is None):
            raise ValueError("longitude and latitude must be supplied together")
        return self


class SourceAdapter(Protocol):
    source_id: str
    scope_key: str

    async def fetch(self) -> FetchResult: ...

    def normalize(self, raw_item: Mapping[str, Any]) -> ListingObservation: ...

    def source_record_id(self, raw_item: Mapping[str, Any]) -> str | None: ...

from __future__ import annotations

import re
from datetime import datetime
from decimal import Decimal
from typing import Any
from urllib.parse import parse_qsl, urlsplit

from home_radar_models.enums import CrawlCompleteness, ListingStatus
from pydantic import BaseModel, ConfigDict, Field, HttpUrl, field_validator, model_validator

from home_radar_collector.contracts import CrawlScope

CANONICAL_SCHEMA_MAJOR = 1
SCHEMA_VERSION_PATTERN = re.compile(r"^(?P<major>\d+)\.(?P<minor>\d+)$")
SENSITIVE_KEY_PARTS = {
    "api_key",
    "apikey",
    "auth",
    "authorization",
    "bearer",
    "cookie",
    "credential",
    "password",
    "secret",
    "session",
    "token",
    "username",
}


def _normalized_key(value: object) -> str:
    return str(value).strip().lower().replace("-", "_")


def _contains_sensitive_key(value: object) -> bool:
    normalized = _normalized_key(value)
    return any(part in normalized for part in SENSITIVE_KEY_PARTS)


def reject_sensitive_fields(value: Any, *, path: str = "feed") -> None:
    """Fail closed before provider-controlled values can reach persistence."""

    if isinstance(value, dict):
        for key, nested in value.items():
            if _contains_sensitive_key(key):
                raise ValueError(f"sensitive field is forbidden in canonical feed: {path}.{key}")
            normalized_key = _normalized_key(key)
            if isinstance(nested, str) and normalized_key.endswith("url"):
                parsed = urlsplit(nested)
                if parsed.username or parsed.password:
                    raise ValueError(f"credential URL is forbidden in canonical feed: {path}.{key}")
                if any(_contains_sensitive_key(name) for name, _ in parse_qsl(parsed.query)):
                    raise ValueError(
                        f"credential query is forbidden in canonical feed: {path}.{key}"
                    )
            reject_sensitive_fields(nested, path=f"{path}.{key}")
    elif isinstance(value, list):
        for index, nested in enumerate(value):
            reject_sensitive_fields(nested, path=f"{path}[{index}]")


class CanonicalFeedScope(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid", frozen=True)

    city: str = Field(min_length=1, max_length=80)
    district: str | None = Field(default=None, max_length=80)
    submarket: str | None = Field(default=None, max_length=120)
    query: str | None = Field(default=None, max_length=120)
    filters: dict[str, str] = Field(default_factory=dict)

    def to_crawl_scope(self) -> CrawlScope:
        return CrawlScope(
            city=self.city,
            district=self.district,
            submarket=self.submarket,
            query=self.query,
            filters=self.filters,
        )


class FeedCoverage(BaseModel):
    model_config = ConfigDict(extra="allow", frozen=True)

    page_count: int | None = Field(default=None, ge=0)
    pages_fetched: int | None = Field(default=None, ge=0)
    reported_total: int | None = Field(default=None, ge=0)
    items_returned: int | None = Field(default=None, ge=0)


class ProviderCapabilities(BaseModel):
    model_config = ConfigDict(frozen=True)

    supports_listing_history: bool = False
    supports_reported_total: bool = False
    supports_pagination: bool = False
    supports_coordinates: bool = False
    supports_property_attributes: bool = False
    supports_transaction_data: bool = False


class CanonicalListingItem(BaseModel):
    """Version 1 canonical listing fields; additive provider fields are tolerated."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="allow")

    listing_id: str = Field(min_length=1, max_length=255)
    url: HttpUrl
    district: str = Field(min_length=1, max_length=80)
    submarket: str = Field(min_length=1, max_length=120)
    community: str = Field(min_length=1, max_length=160)
    longitude: Decimal | None = Field(default=None, ge=-180, le=180)
    latitude: Decimal | None = Field(default=None, ge=-90, le=90)
    price_wan: Decimal = Field(gt=0)
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
    observed_at: datetime | None = None

    @field_validator("url")
    @classmethod
    def reject_credentials_in_url(cls, value: HttpUrl) -> HttpUrl:
        parsed = urlsplit(str(value))
        if parsed.username or parsed.password:
            raise ValueError("listing URL must not contain credentials")
        if any(_contains_sensitive_key(key) for key, _ in parse_qsl(parsed.query)):
            raise ValueError("listing URL must not contain credential query parameters")
        return value

    @model_validator(mode="after")
    def require_coordinate_pair(self) -> CanonicalListingItem:
        if (self.longitude is None) != (self.latitude is None):
            raise ValueError("longitude and latitude must be supplied together")
        return self

    @field_validator("observed_at")
    @classmethod
    def require_observed_timezone(cls, value: datetime | None) -> datetime | None:
        if value is not None and value.tzinfo is None:
            raise ValueError("observed_at must be timezone-aware")
        return value


class CanonicalFeedEnvelope(BaseModel):
    """Top-level versioned contract; item validation stays per-record for partial ingestion."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="allow")

    schema_version: str
    source_id: str = Field(min_length=1, max_length=50)
    scope: CanonicalFeedScope
    completeness: CrawlCompleteness
    coverage: FeedCoverage | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    items: list[dict[str, Any]]

    @model_validator(mode="before")
    @classmethod
    def reject_embedded_credentials(cls, value: Any) -> Any:
        reject_sensitive_fields(value)
        return value

    @field_validator("schema_version")
    @classmethod
    def require_supported_major_version(cls, value: str) -> str:
        match = SCHEMA_VERSION_PATTERN.fullmatch(value)
        if match is None:
            raise ValueError("schema_version must use major.minor form")
        if int(match.group("major")) != CANONICAL_SCHEMA_MAJOR:
            raise ValueError(f"unsupported canonical feed major version: {value}")
        return value

    @field_validator("completeness")
    @classmethod
    def reject_unknown_success(cls, value: CrawlCompleteness) -> CrawlCompleteness:
        if value is CrawlCompleteness.UNKNOWN:
            raise ValueError("canonical feed cannot declare unknown completeness")
        return value


class CoverageAssessment(BaseModel):
    model_config = ConfigDict(frozen=True)

    completeness: CrawlCompleteness
    coverage_complete: bool
    reasons: tuple[str, ...] = ()


def assess_coverage(
    envelope: CanonicalFeedEnvelope,
    *,
    capabilities: ProviderCapabilities,
    allow_complete_without_coverage: bool,
) -> CoverageAssessment:
    reasons: list[str] = []
    coverage = envelope.coverage
    if envelope.completeness is CrawlCompleteness.PARTIAL:
        reasons.append("provider_declared_partial")

    if coverage is None:
        if not allow_complete_without_coverage:
            reasons.append("coverage_missing")
    else:
        actual_items = len(envelope.items)
        if coverage.items_returned is None:
            reasons.append("items_returned_missing")
        elif coverage.items_returned != actual_items:
            reasons.append("items_returned_mismatch")

        strong_evidence = False
        if coverage.reported_total is not None:
            strong_evidence = True
            if coverage.items_returned is None:
                reasons.append("reported_total_without_items_returned")
            elif coverage.reported_total != coverage.items_returned:
                reasons.append("reported_total_mismatch")
        elif capabilities.supports_reported_total:
            reasons.append("reported_total_missing")

        has_page_count = coverage.page_count is not None
        has_pages_fetched = coverage.pages_fetched is not None
        if has_page_count or has_pages_fetched:
            strong_evidence = True
            if not (has_page_count and has_pages_fetched):
                reasons.append("pagination_coverage_incomplete")
            elif coverage.page_count != coverage.pages_fetched:
                reasons.append("pagination_incomplete")
            elif coverage.page_count == 0 and actual_items > 0:
                reasons.append("pagination_zero_with_items")
        elif capabilities.supports_pagination:
            reasons.append("pagination_coverage_missing")

        if not strong_evidence and not allow_complete_without_coverage:
            reasons.append("coverage_not_proven")

    complete = not reasons and envelope.completeness is CrawlCompleteness.COMPLETE
    return CoverageAssessment(
        completeness=(CrawlCompleteness.COMPLETE if complete else CrawlCompleteness.PARTIAL),
        coverage_complete=complete,
        reasons=tuple(dict.fromkeys(reasons)),
    )

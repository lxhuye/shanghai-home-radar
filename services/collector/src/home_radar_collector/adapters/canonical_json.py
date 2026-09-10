from __future__ import annotations

from asyncio import to_thread
from collections.abc import Mapping
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import httpx
from home_radar_models.enums import ListingStatus
from pydantic import ValidationError
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential_jitter

from home_radar_collector.auth import ProviderAuth, validate_provider_endpoint
from home_radar_collector.contracts import CrawlScope, FetchResult, ListingObservation
from home_radar_collector.errors import (
    AuthenticationRequiredError,
    ListingNormalizationError,
    RetryableSourceError,
    SourceIdentityMismatchError,
    SourcePayloadValidationError,
    SourceScopeMismatchError,
)
from home_radar_collector.feed import (
    CanonicalFeedEnvelope,
    CanonicalListingItem,
    ProviderCapabilities,
    assess_coverage,
)
from home_radar_collector.normalizers import CanonicalJsonFeedNormalizer, FeedNormalizer


class CanonicalJsonFeedAdapter:
    """Generic adapter for an authorized, versioned canonical JSON property feed."""

    def __init__(
        self,
        *,
        source_id: str,
        endpoint: str,
        scope: CrawlScope,
        capabilities: ProviderCapabilities | None = None,
        auth: ProviderAuth | None = None,
        environment: str = "development",
        allow_complete_without_coverage: bool = False,
        timeout_seconds: float = 20,
        max_attempts: int = 4,
        client: httpx.AsyncClient | None = None,
        normalizer: FeedNormalizer | None = None,
    ) -> None:
        self.source_id = source_id
        self.endpoint = endpoint
        self.scope = scope
        self.scope_key = scope.stable_key(source_id)
        self.capabilities = capabilities or ProviderCapabilities()
        self.auth = auth or ProviderAuth()
        self.allow_complete_without_coverage = allow_complete_without_coverage
        self.timeout_seconds = timeout_seconds
        self.max_attempts = max_attempts
        self._client = client
        self._normalizer = normalizer or CanonicalJsonFeedNormalizer()
        validate_provider_endpoint(endpoint, auth=self.auth, environment=environment)

    @property
    def name(self) -> str:
        return self.source_id

    async def fetch(self) -> FetchResult:
        return self.parse_payload(await self._load_payload_text())

    def parse_payload(self, raw_payload: str | bytes) -> FetchResult:
        """Validate already-loaded bytes through the same canonical boundary."""

        payload = self._normalizer.normalize(raw_payload)
        try:
            self.auth.reject_reflected_credentials(payload)
        except ValueError:
            raise SourcePayloadValidationError(
                "provider response contains authentication material"
            ) from None
        try:
            envelope = CanonicalFeedEnvelope.model_validate(payload)
        except ValidationError:
            raise SourcePayloadValidationError("canonical feed validation failed") from None

        if envelope.source_id != self.source_id:
            raise SourceIdentityMismatchError("payload source does not match configured source")
        if envelope.scope.to_crawl_scope().stable_key(self.source_id) != self.scope_key:
            raise SourceScopeMismatchError("payload scope does not match configured scope")

        coverage = assess_coverage(
            envelope,
            capabilities=self.capabilities,
            allow_complete_without_coverage=self.allow_complete_without_coverage,
        )
        coverage_without_override = assess_coverage(
            envelope,
            capabilities=self.capabilities,
            allow_complete_without_coverage=False,
        )
        return FetchResult(
            source_id=self.source_id,
            scope_key=self.scope_key,
            raw_items=envelope.items,
            completeness=coverage.completeness,
            metadata={
                "transport_success": True,
                "schema_valid": True,
                "coverage_complete": coverage.coverage_complete,
                "coverage_override_applied": (
                    self.allow_complete_without_coverage
                    and coverage.coverage_complete
                    and not coverage_without_override.coverage_complete
                ),
                "coverage_reasons": list(coverage.reasons),
                "schema_version": envelope.schema_version,
                "scope": envelope.scope.model_dump(mode="json"),
                "coverage": (
                    envelope.coverage.model_dump(mode="json")
                    if envelope.coverage is not None
                    else None
                ),
                "provider_capabilities": self.capabilities.model_dump(mode="json"),
                "provider_metadata": envelope.metadata,
                "feed_extensions": envelope.model_extra or {},
            },
        )

    def normalize(self, raw_item: Mapping[str, Any]) -> ListingObservation:
        try:
            item = CanonicalListingItem.model_validate(raw_item)
            total_price = (item.price_wan * Decimal(10_000)).quantize(Decimal("0.01"))
            return ListingObservation(
                source=self.source_id,
                source_listing_id=item.listing_id,
                source_url=item.url,
                district=item.district,
                submarket=item.submarket,
                community=item.community,
                longitude=item.longitude,
                latitude=item.latitude,
                total_price=total_price,
                area_sqm=item.area_sqm,
                bedrooms=item.bedrooms,
                living_rooms=item.living_rooms,
                floor=item.floor,
                total_floors=item.total_floors,
                orientation=item.orientation,
                year_built=item.year_built,
                elevator=item.elevator,
                building_type=item.building_type,
                status=ListingStatus(item.status),
                observed_at=item.observed_at or datetime.now(UTC),
            )
        except (ValidationError, ValueError, ArithmeticError):
            raise ListingNormalizationError("canonical_listing_validation_failed") from None

    def source_record_id(self, raw_item: Mapping[str, Any]) -> str | None:
        value = raw_item.get("listing_id")
        return str(value) if value is not None else None

    async def _load_payload_text(self) -> str | bytes:
        if self.endpoint.startswith(("https://", "http://")):
            return await self._fetch_http()
        try:
            return await to_thread(Path(self.endpoint).read_bytes)
        except OSError:
            raise SourcePayloadValidationError("canonical feed file could not be read") from None

    async def _fetch_http(self) -> bytes:
        retryer = retry(
            retry=retry_if_exception_type(
                (httpx.TimeoutException, httpx.TransportError, RetryableSourceError)
            ),
            wait=wait_exponential_jitter(initial=0.5, max=8),
            stop=stop_after_attempt(self.max_attempts),
            reraise=True,
        )

        @retryer
        async def request() -> bytes:
            request_kwargs: dict[str, Any] = {
                "timeout": self.timeout_seconds,
                "headers": self.auth.request_headers(),
                "auth": self.auth.request_auth(),
                "follow_redirects": False,
            }
            if self._client is not None:
                response = await self._client.get(self.endpoint, **request_kwargs)
            else:
                async with httpx.AsyncClient(follow_redirects=False) as client:
                    response = await client.get(self.endpoint, **request_kwargs)
            if response.status_code in {401, 403}:
                raise AuthenticationRequiredError(
                    f"source authentication required: {response.status_code}"
                )
            if response.status_code == 429 or response.status_code >= 500:
                raise RetryableSourceError(f"temporary source response: {response.status_code}")
            if response.status_code >= 300:
                raise SourcePayloadValidationError(
                    f"source response status: {response.status_code}"
                )
            return response.content

        return await request()

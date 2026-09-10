from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import httpx
from home_radar_models.enums import CrawlCompleteness, ListingStatus
from pydantic import ValidationError
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential_jitter

from home_radar_collector.auth import ProviderAuth, validate_provider_endpoint
from home_radar_collector.contracts import CrawlScope, FetchResult, ListingObservation
from home_radar_collector.errors import (
    AuthenticationRequiredError,
    ListingNormalizationError,
    RetryableSourceError,
    SourcePayloadValidationError,
)
from home_radar_collector.feed import (
    CanonicalListingItem,
    ProviderCapabilities,
)


class AuthorizedAPIAdapter:
    """Adapter for paginated authorized API endpoints with daily full pulls."""

    def __init__(
        self,
        *,
        source_id: str,
        api_endpoint: str,
        scope: CrawlScope,
        capabilities: ProviderCapabilities | None = None,
        auth: ProviderAuth | None = None,
        page_size: int = 100,
        max_pages: int | None = None,
        timeout_seconds: float = 30,
        max_attempts: int = 4,
        environment: str = "development",
        require_observed_at: bool = False,
        max_observation_age_hours: float | None = None,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.source_id = source_id
        self.api_endpoint = api_endpoint
        self.scope = scope
        self.scope_key = scope.stable_key(source_id)
        self.capabilities = capabilities or ProviderCapabilities()
        self.auth = auth or ProviderAuth()
        self.page_size = page_size
        self.max_pages = max_pages
        self.timeout_seconds = timeout_seconds
        self.max_attempts = max_attempts
        self.require_observed_at = require_observed_at
        if max_observation_age_hours is not None and max_observation_age_hours <= 0:
            raise ValueError("maximum observation age must be positive")
        self.max_observation_age_hours = max_observation_age_hours
        self._client = client
        validate_provider_endpoint(
            api_endpoint,
            auth=self.auth,
            environment=environment,
        )

    @property
    def name(self) -> str:
        return self.source_id

    async def fetch(self) -> FetchResult:
        """Fetch all listings from paginated API endpoint."""
        all_items: list[dict[str, Any]] = []
        page = 1
        pages_fetched = 0
        total_pages: int | None = None
        reported_total: int | None = None
        ended_with_empty_page = False
        hit_max_pages = False

        while True:
            if self.max_pages is not None and page > self.max_pages:
                hit_max_pages = True
                break

            response_data = await self._fetch_page(page)
            pages_fetched += 1

            page_total_pages = _optional_nonnegative_int(
                response_data.get("total_pages"), "total_pages"
            )
            page_reported_total = _optional_nonnegative_int(response_data.get("total"), "total")
            total_pages = _consistent_pagination_value(total_pages, page_total_pages, "total_pages")
            reported_total = _consistent_pagination_value(
                reported_total, page_reported_total, "total"
            )

            # Extract items from response
            items = response_data.get("items", [])
            if not isinstance(items, list) or any(not isinstance(item, dict) for item in items):
                raise SourcePayloadValidationError("API items must be a list of objects")
            if not items:
                ended_with_empty_page = True
                # Empty page - if we've seen at least one page with data,
                # consider this complete; otherwise it's suspicious
                if page == 1:
                    # First page empty - likely an error or no data
                    break
                # Subsequent page empty - likely reached end
                break

            all_items.extend(items)
            if total_pages == 0:
                raise SourcePayloadValidationError(
                    "API total_pages cannot be zero when items are returned"
                )

            # Check if we've fetched all pages
            if total_pages is not None and page >= total_pages:
                break

            page += 1

        # Validate completeness
        completeness_reasons: list[str] = []

        # Incomplete if we hit max_pages limit
        if hit_max_pages:
            completeness_reasons.append("max_pages_limit")

        # Incomplete if reported_total doesn't match items_returned
        if reported_total is not None and len(all_items) != reported_total:
            completeness_reasons.append("reported_total_mismatch")

        nonempty_pages = pages_fetched - int(ended_with_empty_page)
        if total_pages is not None and nonempty_pages < total_pages:
            completeness_reasons.append("early_empty_page")

        freshness = _assess_observation_freshness(
            all_items,
            now=datetime.now(UTC),
            require_observed_at=self.require_observed_at,
            max_age_hours=self.max_observation_age_hours,
        )
        completeness_reasons.extend(freshness["reasons"])

        # If no items at all, mark as unknown (could be error or truly empty)
        if len(all_items) == 0:
            completeness = CrawlCompleteness.UNKNOWN
            completeness_reasons.append("empty_first_page")
        elif completeness_reasons:
            completeness = CrawlCompleteness.PARTIAL
        else:
            completeness = CrawlCompleteness.COMPLETE

        return FetchResult(
            source_id=self.source_id,
            scope_key=self.scope_key,
            raw_items=all_items,
            completeness=completeness,
            metadata={
                "transport_success": True,
                "api_endpoint": self.api_endpoint,
                "pages_fetched": pages_fetched,
                "total_pages": total_pages,
                "reported_total": reported_total,
                "items_returned": len(all_items),
                "completeness_reasons": completeness_reasons,
                "observation_freshness": freshness,
                "scope": self.scope.model_dump(mode="json"),
                "provider_capabilities": self.capabilities.model_dump(mode="json"),
            },
        )

    def normalize(self, raw_item: Mapping[str, Any]) -> ListingObservation:
        """Normalize API response item to ListingObservation."""
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
            raise ListingNormalizationError("api_listing_validation_failed") from None

    def source_record_id(self, raw_item: Mapping[str, Any]) -> str | None:
        value = raw_item.get("listing_id")
        return str(value) if value is not None else None

    async def _fetch_page(self, page: int) -> dict[str, Any]:
        """Fetch a single page from the API with retry logic."""
        retryer = retry(
            retry=retry_if_exception_type(
                (httpx.TimeoutException, httpx.TransportError, RetryableSourceError)
            ),
            wait=wait_exponential_jitter(initial=0.5, max=8),
            stop=stop_after_attempt(self.max_attempts),
            reraise=True,
        )

        @retryer
        async def request() -> dict[str, Any]:
            params = self._build_request_params(page)
            request_kwargs: dict[str, Any] = {
                "timeout": self.timeout_seconds,
                "headers": self.auth.request_headers(),
                "auth": self.auth.request_auth(),
                "params": params,
                "follow_redirects": False,
            }

            if self._client is not None:
                response = await self._client.get(self.api_endpoint, **request_kwargs)
            else:
                async with httpx.AsyncClient(follow_redirects=False) as client:
                    response = await client.get(self.api_endpoint, **request_kwargs)

            if response.status_code in {401, 403}:
                raise AuthenticationRequiredError(
                    f"API authentication required: {response.status_code}"
                )
            if response.status_code == 429 or response.status_code >= 500:
                raise RetryableSourceError(f"temporary API response: {response.status_code}")
            if response.status_code >= 300:
                raise SourcePayloadValidationError(f"API response status: {response.status_code}")

            try:
                data = response.json()
            except Exception:
                raise SourcePayloadValidationError("API response is not valid JSON") from None

            # Validate response has required structure
            if not isinstance(data, dict):
                raise SourcePayloadValidationError("API response must be a dictionary")
            try:
                self.auth.reject_reflected_credentials(data)
            except ValueError:
                raise SourcePayloadValidationError(
                    "provider response contains authentication material"
                ) from None

            return data

        return await request()

    def _build_request_params(self, page: int) -> dict[str, Any]:
        """Build request parameters for a page fetch."""
        params: dict[str, Any] = {
            "page": page,
            "page_size": self.page_size,
            "status": "active",
        }

        # Add scope filters
        if self.scope.city:
            params["city"] = self.scope.city
        if self.scope.district:
            params["district"] = self.scope.district
        if self.scope.submarket:
            params["submarket"] = self.scope.submarket
        if self.scope.query:
            params["query"] = self.scope.query

        return params


def _optional_nonnegative_int(value: object, field: str) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise SourcePayloadValidationError(f"API {field} must be a non-negative integer")
    return value


def _consistent_pagination_value(
    current: int | None,
    candidate: int | None,
    field: str,
) -> int | None:
    if candidate is None:
        return current
    if current is not None and current != candidate:
        raise SourcePayloadValidationError(f"API {field} changed between pages")
    return candidate


def _assess_observation_freshness(
    items: list[dict[str, Any]],
    *,
    now: datetime,
    require_observed_at: bool,
    max_age_hours: float | None,
) -> dict[str, Any]:
    reasons: list[str] = []
    timestamps: list[datetime] = []
    missing = 0
    invalid = 0
    future = 0
    stale = 0
    oldest_allowed = now - timedelta(hours=max_age_hours) if max_age_hours is not None else None
    future_limit = now + timedelta(minutes=5)

    for item in items:
        raw = item.get("observed_at")
        if raw is None or (isinstance(raw, str) and not raw.strip()):
            missing += 1
            continue
        parsed = _parse_observed_at(raw)
        if parsed is None:
            invalid += 1
            continue
        normalized = parsed.astimezone(UTC)
        timestamps.append(normalized)
        if normalized > future_limit:
            future += 1
        if oldest_allowed is not None and normalized < oldest_allowed:
            stale += 1

    if require_observed_at and missing:
        reasons.append("observed_at_missing")
    if require_observed_at and invalid:
        reasons.append("observed_at_invalid")
    if require_observed_at and future:
        reasons.append("observed_at_in_future")
    if require_observed_at and stale:
        reasons.append("observed_at_stale")
    return {
        "required": require_observed_at,
        "max_age_hours": max_age_hours,
        "timestamped_items": len(timestamps),
        "missing_items": missing,
        "invalid_items": invalid,
        "future_items": future,
        "stale_items": stale,
        "oldest_observed_at": min(timestamps).isoformat() if timestamps else None,
        "newest_observed_at": max(timestamps).isoformat() if timestamps else None,
        "reasons": reasons,
    }


def _parse_observed_at(value: object) -> datetime | None:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    else:
        return None
    return parsed if parsed.tzinfo is not None else None

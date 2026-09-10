from __future__ import annotations

from collections.abc import Callable
from urllib.parse import urlparse

from home_radar_models.enums import DataMode
from home_radar_shared.config import Settings

from home_radar_collector.adapters.authorized_api import AuthorizedAPIAdapter
from home_radar_collector.adapters.canonical_json import CanonicalJsonFeedAdapter
from home_radar_collector.adapters.partner_csv import PartnerCsvFeedAdapter
from home_radar_collector.auth import build_provider_auth, provider_origins_match
from home_radar_collector.contracts import CrawlScope, SourceAdapter
from home_radar_collector.errors import SourceIdentityMismatchError
from home_radar_collector.feed import ProviderCapabilities

AdapterFactory = Callable[[Settings, str | None], SourceAdapter]


PROVIDER_CAPABILITIES: dict[str, ProviderCapabilities] = {
    "sample_json": ProviderCapabilities(
        supports_reported_total=True,
        supports_pagination=True,
        supports_coordinates=True,
        supports_property_attributes=True,
    ),
    "partner_feed": ProviderCapabilities(
        supports_listing_history=True,
        supports_reported_total=True,
        supports_pagination=True,
        supports_coordinates=True,
        supports_property_attributes=True,
        supports_transaction_data=True,
    ),
    "authorized_api": ProviderCapabilities(
        supports_listing_history=False,
        supports_reported_total=True,
        supports_pagination=True,
        supports_coordinates=True,
        supports_property_attributes=True,
        supports_transaction_data=False,
    ),
    "partner_csv": ProviderCapabilities(
        supports_listing_history=False,
        supports_reported_total=True,
        supports_pagination=False,
        supports_coordinates=True,
        supports_property_attributes=True,
        supports_transaction_data=False,
    ),
}


def _build_json_feed(settings: Settings, endpoint: str | None) -> SourceAdapter:
    scope = CrawlScope(
        city=settings.collector_city,
        district=settings.collector_district,
        submarket=settings.collector_submarket,
        query=settings.collector_query,
    )
    auth = build_provider_auth(settings)
    resolved_endpoint = endpoint or settings.collector_endpoint
    if (
        endpoint is not None
        and auth.enabled
        and not provider_origins_match(settings.collector_endpoint, endpoint)
    ):
        raise ValueError("authenticated endpoint override must keep the configured origin")
    return CanonicalJsonFeedAdapter(
        source_id=settings.collector_source,
        endpoint=resolved_endpoint,
        scope=scope,
        capabilities=PROVIDER_CAPABILITIES[settings.collector_source],
        auth=auth,
        environment=settings.environment,
        allow_complete_without_coverage=settings.collector_allow_complete_without_coverage,
        timeout_seconds=settings.collector_request_timeout_seconds,
        max_attempts=settings.collector_max_attempts,
    )


def _build_authorized_api(settings: Settings, endpoint: str | None) -> SourceAdapter:
    scope = CrawlScope(
        city=settings.collector_city,
        district=settings.collector_district,
        submarket=settings.collector_submarket,
        query=settings.collector_query,
    )
    auth = build_provider_auth(settings)
    resolved_endpoint = endpoint or settings.collector_endpoint
    if (
        endpoint is not None
        and auth.enabled
        and not provider_origins_match(settings.collector_endpoint, endpoint)
    ):
        raise ValueError("authenticated endpoint override must keep the configured origin")
    return AuthorizedAPIAdapter(
        source_id=settings.collector_source,
        api_endpoint=resolved_endpoint,
        scope=scope,
        capabilities=PROVIDER_CAPABILITIES[settings.collector_source],
        auth=auth,
        timeout_seconds=settings.collector_request_timeout_seconds,
        max_attempts=settings.collector_max_attempts,
        environment=settings.environment,
        require_observed_at=settings.market_data_mode == DataMode.LIVE.value,
        max_observation_age_hours=settings.collector_max_observation_age_hours,
    )


def _build_partner_csv(settings: Settings, endpoint: str | None) -> SourceAdapter:
    auth = build_provider_auth(settings)
    if auth.enabled:
        raise ValueError("partner CSV file source does not accept transport credentials")
    scope = CrawlScope(
        city=settings.collector_city,
        district=settings.collector_district,
        submarket=settings.collector_submarket,
        query=settings.collector_query,
    )
    return PartnerCsvFeedAdapter(
        source_id=settings.collector_source,
        endpoint=endpoint or settings.collector_endpoint,
        scope=scope,
        capabilities=PROVIDER_CAPABILITIES[settings.collector_source],
        environment=settings.environment,
        max_export_age_hours=settings.collector_partner_csv_max_age_hours,
    )


ADAPTER_REGISTRY: dict[str, AdapterFactory] = {
    "sample_json": _build_json_feed,
    "partner_feed": _build_json_feed,
    "authorized_api": _build_authorized_api,
    "partner_csv": _build_partner_csv,
}


def build_configured_adapter(settings: Settings, *, endpoint: str | None = None) -> SourceAdapter:
    resolved_endpoint = endpoint or settings.collector_endpoint
    if DataMode(settings.market_data_mode) is DataMode.LIVE:
        is_partner_csv = settings.collector_source == "partner_csv"
        is_http_feed = urlparse(resolved_endpoint).scheme in {"http", "https"}
        if settings.collector_source == "sample_json" or not (is_http_feed or is_partner_csv):
            raise ValueError("LIVE mode requires an authorized HTTP feed or partner CSV export")
    factory = ADAPTER_REGISTRY.get(settings.collector_source)
    if factory is None:
        raise ValueError(f"unsupported collector source: {settings.collector_source}")
    adapter = factory(settings, endpoint)
    if adapter.source_id != settings.collector_source:
        raise SourceIdentityMismatchError(
            f"adapter source {adapter.source_id} does not match {settings.collector_source}"
        )
    return adapter

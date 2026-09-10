from __future__ import annotations

import pytest
from home_radar_collector.adapters import (
    AuthorizedAPIAdapter,
    CanonicalJsonFeedAdapter,
    ExampleJsonFeedAdapter,
    PartnerCsvFeedAdapter,
)
from home_radar_collector.contracts import CrawlScope, SourceAdapter
from home_radar_collector.errors import SourceIdentityMismatchError
from home_radar_collector.registry import (
    ADAPTER_REGISTRY,
    PROVIDER_CAPABILITIES,
    build_configured_adapter,
)
from home_radar_shared.config import Settings


def test_registry_rejects_adapter_source_identity_divergence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def mismatched_factory(settings: Settings, endpoint: str | None) -> SourceAdapter:
        return ExampleJsonFeedAdapter(
            source_id="other_source",
            endpoint=endpoint or settings.collector_endpoint,
            scope=CrawlScope(city="shanghai"),
        )

    monkeypatch.setitem(ADAPTER_REGISTRY, "sample_json", mismatched_factory)

    with pytest.raises(SourceIdentityMismatchError):
        build_configured_adapter(Settings(collector_source="sample_json"))


def test_registry_rejects_unknown_configured_source() -> None:
    with pytest.raises(ValueError, match="unsupported collector source"):
        build_configured_adapter(Settings(collector_source="not_registered"))


@pytest.mark.parametrize("source_id", ["sample_json", "partner_feed"])
def test_registry_uses_one_generic_adapter_for_registered_json_feeds(source_id: str) -> None:
    source = build_configured_adapter(Settings(collector_source=source_id))

    assert isinstance(source, CanonicalJsonFeedAdapter)
    assert source.capabilities == PROVIDER_CAPABILITIES[source_id]  # type: ignore[attr-defined]


def test_authenticated_endpoint_override_must_keep_origin() -> None:
    settings = Settings(
        collector_source="partner_feed",
        collector_endpoint="https://partner.example.invalid/feed",
        collector_auth_mode="BEARER_TOKEN",
        collector_bearer_token="secret",
    )

    with pytest.raises(ValueError, match="configured origin"):
        build_configured_adapter(
            settings,
            endpoint="https://attacker.example.invalid/feed",
        )


def test_registry_allows_authorized_partner_csv_in_live_mode() -> None:
    source = build_configured_adapter(
        Settings(
            collector_source="partner_csv",
            collector_endpoint="data/inbox/listings.csv",
            market_data_mode="live",
        )
    )

    assert isinstance(source, PartnerCsvFeedAdapter)
    assert source.capabilities == PROVIDER_CAPABILITIES["partner_csv"]


def test_registry_requires_fresh_observation_times_for_live_api() -> None:
    source = build_configured_adapter(
        Settings(
            collector_source="authorized_api",
            collector_endpoint="https://partner.example.invalid/listings",
            market_data_mode="live",
            collector_max_observation_age_hours=24,
        )
    )

    assert isinstance(source, AuthorizedAPIAdapter)
    assert source.require_observed_at is True
    assert source.max_observation_age_hours == 24


def test_partner_csv_rejects_transport_credentials() -> None:
    settings = Settings(
        collector_source="partner_csv",
        collector_endpoint="data/inbox/listings.csv",
        market_data_mode="live",
        collector_auth_mode="BEARER_TOKEN",
        collector_bearer_token="secret",
    )

    with pytest.raises(ValueError, match="does not accept transport credentials"):
        build_configured_adapter(settings)


def test_live_mode_still_rejects_unregistered_local_feed() -> None:
    with pytest.raises(ValueError, match="authorized HTTP feed or partner CSV"):
        build_configured_adapter(
            Settings(
                collector_source="partner_feed",
                collector_endpoint="data/inbox/listings.json",
                market_data_mode="live",
            )
        )

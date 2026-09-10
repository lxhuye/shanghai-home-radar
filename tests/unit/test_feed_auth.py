from __future__ import annotations

import base64

import httpx
import pytest
from home_radar_collector.adapters import CanonicalJsonFeedAdapter
from home_radar_collector.auth import (
    ProviderAuth,
    ProviderAuthMode,
    build_provider_auth,
    provider_origins_match,
    validate_provider_endpoint,
)
from home_radar_collector.contracts import CrawlScope
from home_radar_collector.errors import SourcePayloadValidationError
from home_radar_shared.config import Settings
from pydantic import SecretStr, ValidationError


def secret(value: str) -> SecretStr:
    return SecretStr(value)


@pytest.mark.parametrize(
    "auth",
    [
        ProviderAuth(mode=ProviderAuthMode.BEARER_TOKEN, bearer_token=secret("secret")),
        ProviderAuth(
            mode=ProviderAuthMode.BASIC_AUTH,
            basic_username=secret("user"),
            basic_password=secret("secret"),
        ),
        ProviderAuth(
            mode=ProviderAuthMode.CUSTOM_HEADER,
            custom_header_name="X-Partner-Key",
            custom_header_value=secret("secret"),
        ),
    ],
)
def test_provider_auth_repr_and_dump_do_not_reveal_secret(auth: ProviderAuth) -> None:
    assert "secret" not in repr(auth)
    assert "secret" not in str(auth.model_dump())


def test_none_mode_accepts_blank_environment_values() -> None:
    auth = build_provider_auth(
        Settings(
            collector_auth_mode="NONE",
            collector_bearer_token="",
            collector_basic_username="",
            collector_basic_password="",
            collector_custom_header_name="",
            collector_custom_header_value="",
        )
    )

    assert auth == ProviderAuth()


@pytest.mark.parametrize(
    "kwargs",
    [
        {"mode": ProviderAuthMode.BEARER_TOKEN},
        {"mode": ProviderAuthMode.BEARER_TOKEN, "bearer_token": secret(" ")},
        {
            "mode": ProviderAuthMode.NONE,
            "bearer_token": secret("unexpected"),
        },
        {
            "mode": ProviderAuthMode.BASIC_AUTH,
            "basic_username": secret("bad:user"),
            "basic_password": secret("secret"),
        },
        {
            "mode": ProviderAuthMode.CUSTOM_HEADER,
            "custom_header_name": "Authorization",
            "custom_header_value": secret("secret"),
        },
        {
            "mode": ProviderAuthMode.CUSTOM_HEADER,
            "custom_header_name": "X-Bad\r\nInjected",
            "custom_header_value": secret("secret"),
        },
    ],
)
def test_invalid_or_conflicting_auth_configuration_fails_closed(kwargs: object) -> None:
    with pytest.raises(ValidationError):
        ProviderAuth.model_validate(kwargs)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("auth", "expected_name", "expected_value"),
    [
        (
            ProviderAuth(
                mode=ProviderAuthMode.BEARER_TOKEN,
                bearer_token=secret("bearer-secret"),
            ),
            "authorization",
            "Bearer bearer-secret",
        ),
        (
            ProviderAuth(
                mode=ProviderAuthMode.BASIC_AUTH,
                basic_username=secret("user"),
                basic_password=secret("basic-secret"),
            ),
            "authorization",
            "Basic " + base64.b64encode(b"user:basic-secret").decode(),
        ),
        (
            ProviderAuth(
                mode=ProviderAuthMode.CUSTOM_HEADER,
                custom_header_name="X-Partner-Key",
                custom_header_value=secret("header-secret"),
            ),
            "x-partner-key",
            "header-secret",
        ),
    ],
)
async def test_auth_mode_is_applied_only_to_the_request(
    auth: ProviderAuth, expected_name: str, expected_value: str
) -> None:
    seen_header = ""

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal seen_header
        seen_header = request.headers[expected_name]
        return httpx.Response(
            200,
            json={
                "schema_version": "1.0",
                "source_id": "partner_feed",
                "scope": {"city": "shanghai", "filters": {}},
                "completeness": "complete",
                "coverage": {"reported_total": 0, "items_returned": 0},
                "metadata": {},
                "items": [],
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        source = CanonicalJsonFeedAdapter(
            source_id="partner_feed",
            endpoint="https://partner.example.invalid/feed",
            scope=CrawlScope(city="shanghai"),
            auth=auth,
            client=client,
        )
        await source.fetch()

    assert seen_header == expected_value


@pytest.mark.asyncio
async def test_provider_reflected_secret_is_rejected_before_persistence() -> None:
    auth = ProviderAuth(
        mode=ProviderAuthMode.BEARER_TOKEN,
        bearer_token=secret("never-persist-this"),
    )

    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "schema_version": "1.0",
                "source_id": "partner_feed",
                "scope": {"city": "shanghai", "filters": {}},
                "completeness": "complete",
                "coverage": {"reported_total": 0, "items_returned": 0},
                "metadata": {"echo": "Bearer never-persist-this"},
                "items": [],
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        source = CanonicalJsonFeedAdapter(
            source_id="partner_feed",
            endpoint="https://partner.example.invalid/feed",
            scope=CrawlScope(city="shanghai"),
            auth=auth,
            client=client,
        )
        with pytest.raises(SourcePayloadValidationError) as error:
            await source.fetch()

    assert "never-persist-this" not in str(error.value)


def test_endpoint_policy_blocks_embedded_credentials_and_production_http() -> None:
    with pytest.raises(ValueError):
        validate_provider_endpoint(
            "https://user:password@example.invalid/feed",
            auth=ProviderAuth(),
            environment="development",
        )
    with pytest.raises(ValueError):
        validate_provider_endpoint(
            "http://example.invalid/feed",
            auth=ProviderAuth(),
            environment="production",
        )


def test_authenticated_override_origin_comparison_is_exact() -> None:
    assert provider_origins_match(
        "https://example.invalid/feed",
        "https://example.invalid/other",
    )
    assert not provider_origins_match(
        "https://example.invalid/feed",
        "https://other.invalid/feed",
    )

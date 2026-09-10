from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

import httpx
import pytest
from home_radar_collector.adapters import CanonicalJsonFeedAdapter
from home_radar_collector.contracts import CrawlScope
from home_radar_collector.errors import (
    AuthenticationRequiredError,
    ListingNormalizationError,
    SourceIdentityMismatchError,
    SourcePayloadValidationError,
    SourceScopeMismatchError,
)
from home_radar_collector.feed import ProviderCapabilities
from home_radar_models.enums import CrawlCompleteness


def adapter(endpoint: str, **kwargs: object) -> CanonicalJsonFeedAdapter:
    return CanonicalJsonFeedAdapter(
        source_id="sample_json",
        endpoint=endpoint,
        scope=CrawlScope(city="shanghai", district="xuhui"),
        capabilities=ProviderCapabilities(
            supports_reported_total=True,
            supports_pagination=True,
        ),
        **kwargs,  # type: ignore[arg-type]
    )


def valid_item(listing_id: str = "a-1") -> dict[str, object]:
    return {
        "listing_id": listing_id,
        "url": f"https://example.invalid/{listing_id}",
        "district": "Xuhui",
        "submarket": "Huajing",
        "community": "Test Garden",
        "price_wan": 300,
        "area_sqm": 60,
    }


def feed(items: list[dict[str, object]] | None = None, **overrides: object) -> dict[str, object]:
    values = items if items is not None else [valid_item()]
    result: dict[str, object] = {
        "schema_version": "1.0",
        "source_id": "sample_json",
        "scope": {"city": "shanghai", "district": "Xuhui", "filters": {}},
        "completeness": "complete",
        "coverage": {
            "page_count": 1,
            "pages_fetched": 1,
            "reported_total": len(values),
            "items_returned": len(values),
        },
        "metadata": {"batch": "fixture"},
        "items": values,
    }
    result.update(overrides)
    return result


def write_feed(path: Path, payload: object) -> None:
    path.write_text(json.dumps(payload), encoding="utf-8")


@pytest.mark.asyncio
async def test_canonical_feed_validates_fetch_then_normalizes(tmp_path: Path) -> None:
    fixture = tmp_path / "feed.json"
    write_feed(fixture, feed(schema_version="1.1", additive_field={"accepted": True}))

    source = adapter(str(fixture))
    result = await source.fetch()
    observation = source.normalize(result.raw_items[0])

    assert result.completeness is CrawlCompleteness.COMPLETE
    assert result.metadata["transport_success"] is True
    assert result.metadata["schema_valid"] is True
    assert result.metadata["coverage_complete"] is True
    assert result.metadata["feed_extensions"] == {"additive_field": {"accepted": True}}
    assert observation.total_price == Decimal("3000000.00")
    assert observation.unit_price == Decimal("50000.00")


@pytest.mark.asyncio
async def test_explicit_zero_coverage_is_a_complete_empty_result(tmp_path: Path) -> None:
    fixture = tmp_path / "feed.json"
    write_feed(fixture, feed([]))

    result = await adapter(str(fixture)).fetch()

    assert result.raw_items == []
    assert result.completeness is CrawlCompleteness.COMPLETE


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("coverage", "reason"),
    [
        (
            {"page_count": 2, "pages_fetched": 1, "reported_total": 1, "items_returned": 1},
            "pagination_incomplete",
        ),
        (
            {"page_count": 1, "pages_fetched": 1, "reported_total": 2, "items_returned": 1},
            "reported_total_mismatch",
        ),
        (
            {"page_count": 0, "pages_fetched": 0, "reported_total": 1, "items_returned": 1},
            "pagination_zero_with_items",
        ),
        (None, "coverage_missing"),
    ],
)
async def test_incomplete_coverage_is_partial(
    tmp_path: Path, coverage: object, reason: str
) -> None:
    fixture = tmp_path / "feed.json"
    write_feed(fixture, feed(coverage=coverage))

    result = await adapter(str(fixture)).fetch()

    assert result.completeness is CrawlCompleteness.PARTIAL
    assert reason in result.metadata["coverage_reasons"]


@pytest.mark.asyncio
async def test_explicit_no_coverage_override_is_recorded(tmp_path: Path) -> None:
    fixture = tmp_path / "feed.json"
    write_feed(fixture, feed(coverage=None))

    result = await adapter(
        str(fixture),
        allow_complete_without_coverage=True,
    ).fetch()

    assert result.completeness is CrawlCompleteness.COMPLETE
    assert result.metadata["coverage_override_applied"] is True


@pytest.mark.asyncio
@pytest.mark.parametrize("payload", [{}, {"error": "blocked"}, [], "not-json"])
async def test_malformed_top_level_payload_is_rejected(tmp_path: Path, payload: object) -> None:
    fixture = tmp_path / "feed.json"
    if payload == "not-json":
        fixture.write_text("not-json", encoding="utf-8")
    else:
        write_feed(fixture, payload)

    with pytest.raises(SourcePayloadValidationError):
        await adapter(str(fixture)).fetch()


@pytest.mark.asyncio
async def test_unsupported_major_version_is_rejected(tmp_path: Path) -> None:
    fixture = tmp_path / "feed.json"
    write_feed(fixture, feed(schema_version="2.0"))

    with pytest.raises(SourcePayloadValidationError):
        await adapter(str(fixture)).fetch()


@pytest.mark.asyncio
async def test_payload_source_must_match_configured_source(tmp_path: Path) -> None:
    fixture = tmp_path / "feed.json"
    write_feed(fixture, feed(source_id="other"))

    with pytest.raises(SourceIdentityMismatchError):
        await adapter(str(fixture)).fetch()


@pytest.mark.asyncio
async def test_payload_scope_must_match_configured_scope(tmp_path: Path) -> None:
    fixture = tmp_path / "feed.json"
    write_feed(fixture, feed(scope={"city": "shanghai", "district": "putuo"}))

    with pytest.raises(SourceScopeMismatchError):
        await adapter(str(fixture)).fetch()


def test_invalid_individual_item_has_safe_normalization_error() -> None:
    with pytest.raises(ListingNormalizationError, match="canonical_listing_validation_failed"):
        adapter("unused.json").normalize({"listing_id": "broken"})


@pytest.mark.asyncio
async def test_http_adapter_retries_temporary_source_failure() -> None:
    attempts = 0

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            return httpx.Response(503)
        return httpx.Response(200, json=feed([valid_item("retry-1")]))

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await adapter(
            "https://feed.example.invalid/listings",
            max_attempts=2,
            client=client,
        ).fetch()

    assert attempts == 2
    assert len(result.raw_items) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [401, 403])
async def test_authentication_failure_is_not_retried(status: int) -> None:
    attempts = 0

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        return httpx.Response(status)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(AuthenticationRequiredError):
            await adapter(
                "https://feed.example.invalid/listings",
                max_attempts=4,
                client=client,
            ).fetch()

    assert attempts == 1


def test_scope_key_is_stable_and_scope_sensitive() -> None:
    first = CrawlScope(city="shanghai", district="xuhui", filters={"budget": "230-330"})
    same = CrawlScope(filters={" BUDGET ": " 230-330 "}, district="Xuhui", city="Shanghai")
    other = CrawlScope(city="shanghai", district="putuo", filters={"budget": "230-330"})

    assert first.stable_key("sample_json") == same.stable_key("sample_json")
    assert first.stable_key("sample_json") != other.stable_key("sample_json")

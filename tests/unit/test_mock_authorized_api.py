"""Application-level HTTP checks of AuthorizedAPIAdapter against the mock partner API.

The adapter talks to the FastAPI app through httpx's ASGI transport, so request building,
authentication headers, pagination, completeness accounting, retries, and error mapping all run
through HTTP semantics. Socket, TLS, DNS, and network timeout behavior require a deployed smoke
test and are intentionally outside this unit suite.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
from home_radar_collector.adapters.authorized_api import AuthorizedAPIAdapter
from home_radar_collector.auth import ProviderAuth, ProviderAuthMode
from home_radar_collector.contracts import CrawlScope
from home_radar_collector.errors import AuthenticationRequiredError, RetryableSourceError
from home_radar_collector.feed import CrawlCompleteness
from pydantic import SecretStr

from scripts import mock_authorized_api as mock

ENDPOINT = "http://mock-partner/v1/listings"
TOKEN = "unit-test-token"


def _items(count: int) -> list[dict[str, Any]]:
    districts = ["徐汇", "普陀", "杨浦", "浦东", "闵行"]
    return [
        {
            "listing_id": f"L{i:03d}",
            "url": f"https://example.com/listing/L{i:03d}",
            "district": districts[i % 5],
            "submarket": "康健" if i % 5 == 0 else "其他",
            "community": f"小区{i}",
            "price_wan": 280 + i,
            "area_sqm": 60 + i,
            "bedrooms": 2,
            "living_rooms": 1,
            "status": "active",
        }
        for i in range(count)
    ]


@pytest.fixture
async def client_factory() -> AsyncIterator[Any]:
    clients: list[httpx.AsyncClient] = []

    def factory(state: mock.MockState) -> httpx.AsyncClient:
        client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=mock.create_app(state)),
            base_url="http://mock-partner",
        )
        clients.append(client)
        return client

    yield factory
    for client in clients:
        await client.aclose()


def _adapter(
    client: httpx.AsyncClient,
    *,
    token: str | None = TOKEN,
    scope: CrawlScope | None = None,
    page_size: int = 4,
    max_attempts: int = 3,
) -> AuthorizedAPIAdapter:
    auth = (
        ProviderAuth(mode=ProviderAuthMode.BEARER_TOKEN, bearer_token=SecretStr(token))
        if token is not None
        else ProviderAuth(mode=ProviderAuthMode.NONE)
    )
    return AuthorizedAPIAdapter(
        source_id="mock_partner",
        api_endpoint=ENDPOINT,
        scope=scope or CrawlScope(city="shanghai"),
        auth=auth,
        page_size=page_size,
        max_attempts=max_attempts,
        client=client,
    )


async def test_paginates_to_complete_and_sends_bearer_header(client_factory: Any) -> None:
    state = mock.build_state(_items(10), token=TOKEN)
    adapter = _adapter(client_factory(state))

    result = await adapter.fetch()

    assert result.completeness is CrawlCompleteness.COMPLETE
    assert len(result.raw_items) == 10
    assert result.metadata["pages_fetched"] == 3
    assert result.metadata["reported_total"] == 10
    assert [entry["page"] for entry in state.request_log] == [1, 2, 3]
    observation = adapter.normalize(result.raw_items[0])
    assert observation.source == "mock_partner"
    assert observation.total_price == 2_800_000


async def test_wrong_bearer_token_is_not_retried_and_raises_auth_error(
    client_factory: Any,
) -> None:
    state = mock.build_state(_items(3), token=TOKEN)
    adapter = _adapter(client_factory(state), token="wrong")

    with pytest.raises(AuthenticationRequiredError):
        await adapter.fetch()

    assert [entry["status_code"] for entry in state.request_log] == [401]


async def test_scope_filters_are_forwarded_as_query_params(client_factory: Any) -> None:
    state = mock.build_state(_items(10), token=TOKEN)
    adapter = _adapter(
        client_factory(state), scope=CrawlScope(city="shanghai", district="徐汇", submarket="康健")
    )

    result = await adapter.fetch()

    assert {item["district"] for item in result.raw_items} == {"徐汇"}
    assert len(result.raw_items) == 2
    assert state.request_log[0]["district"] == "徐汇"
    assert state.request_log[0]["submarket"] == "康健"
    assert result.completeness is CrawlCompleteness.COMPLETE


async def test_city_and_text_query_are_real_filters(client_factory: Any) -> None:
    items = _items(3)
    items[0]["title"] = "地铁两房"
    items[1]["city"] = "beijing"
    state = mock.build_state(items, token=TOKEN)
    adapter = _adapter(
        client_factory(state),
        scope=CrawlScope(city="shanghai", query="地铁"),
    )

    result = await adapter.fetch()

    assert [item["listing_id"] for item in result.raw_items] == ["L000"]
    assert state.request_log[0]["city"] == "shanghai"
    assert state.request_log[0]["query"] == "地铁"


async def test_transient_429_is_retried_until_the_page_recovers(client_factory: Any) -> None:
    state = mock.build_state(_items(8), token=TOKEN, faults=[mock.Fault(2, "429", 1)])
    adapter = _adapter(client_factory(state))

    result = await adapter.fetch()

    assert result.completeness is CrawlCompleteness.COMPLETE
    assert len(result.raw_items) == 8
    assert [entry["page"] for entry in state.request_log] == [1, 2, 2]


async def test_persistent_500_exhausts_retries_and_surfaces_retryable_error(
    client_factory: Any,
) -> None:
    state = mock.build_state(_items(8), token=TOKEN, faults=[mock.Fault(1, "500", 10)])
    adapter = _adapter(client_factory(state), max_attempts=2)

    with pytest.raises(RetryableSourceError):
        await adapter.fetch()

    assert [entry["page"] for entry in state.request_log] == [1, 1]


async def test_short_page_is_reported_partial_not_complete(client_factory: Any) -> None:
    state = mock.build_state(_items(8), token=TOKEN, faults=[mock.Fault(2, "short", 1)])
    adapter = _adapter(client_factory(state))

    result = await adapter.fetch()

    assert result.completeness is CrawlCompleteness.PARTIAL
    assert len(result.raw_items) < 8
    assert result.metadata["reported_total"] == 8


async def test_empty_first_page_is_unknown(client_factory: Any) -> None:
    state = mock.build_state(_items(8), token=TOKEN, faults=[mock.Fault(1, "empty", 1)])
    adapter = _adapter(client_factory(state))

    result = await adapter.fetch()

    assert result.completeness is CrawlCompleteness.UNKNOWN
    assert result.raw_items == []


def test_fault_spec_parsing() -> None:
    fault = mock.Fault.parse("3:503:2")
    assert (fault.page, fault.kind, fault.remaining) == (3, "503", 2)
    with pytest.raises(Exception, match="bad fault spec"):
        mock.Fault.parse("nonsense")

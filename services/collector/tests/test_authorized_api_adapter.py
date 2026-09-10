"""Tests for AuthorizedAPIAdapter."""

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from home_radar_collector.adapters.authorized_api import AuthorizedAPIAdapter
from home_radar_collector.auth import ProviderAuth, ProviderAuthMode
from home_radar_collector.contracts import CrawlScope
from home_radar_collector.errors import SourcePayloadValidationError
from home_radar_models.enums import CrawlCompleteness


@pytest.fixture
def mock_scope():
    return CrawlScope(city="shanghai", district="xuhui")


@pytest.fixture
def mock_auth():
    return ProviderAuth(
        mode=ProviderAuthMode.BEARER_TOKEN,
        bearer_token="test_token_123",
    )


@pytest.fixture
def adapter(mock_scope, mock_auth):
    return AuthorizedAPIAdapter(
        source_id="test_api",
        api_endpoint="https://api.example.com/v1/listings",
        scope=mock_scope,
        auth=mock_auth,
        page_size=10,
    )


class TestAuthorizedAPIAdapter:
    """Test suite for AuthorizedAPIAdapter."""

    @pytest.mark.asyncio
    async def test_fetch_single_page_complete(self, adapter):
        """Test fetching a single page returns COMPLETE."""
        mock_response = {
            "items": [{"listing_id": "001"}, {"listing_id": "002"}],
            "total_pages": 1,
            "total": 2,
        }

        with patch.object(adapter, "_fetch_page", new_callable=AsyncMock) as mock_fetch:
            mock_fetch.return_value = mock_response

            result = await adapter.fetch()

            assert result.completeness == CrawlCompleteness.COMPLETE
            assert len(result.raw_items) == 2
            assert result.metadata["pages_fetched"] == 1
            assert result.metadata["items_returned"] == 2
            assert result.metadata["reported_total"] == 2

    @pytest.mark.asyncio
    async def test_fetch_multiple_pages_complete(self, adapter):
        """Test fetching multiple pages returns COMPLETE."""
        page1_response = {
            "items": [{"listing_id": f"{i:03d}"} for i in range(10)],
            "total_pages": 3,
            "total": 25,
        }
        page2_response = {
            "items": [{"listing_id": f"{i:03d}"} for i in range(10, 20)],
            "total_pages": 3,
            "total": 25,
        }
        page3_response = {
            "items": [{"listing_id": f"{i:03d}"} for i in range(20, 25)],
            "total_pages": 3,
            "total": 25,
        }

        with patch.object(adapter, "_fetch_page", new_callable=AsyncMock) as mock_fetch:
            mock_fetch.side_effect = [page1_response, page2_response, page3_response]

            result = await adapter.fetch()

            assert result.completeness == CrawlCompleteness.COMPLETE
            assert len(result.raw_items) == 25
            assert result.metadata["pages_fetched"] == 3
            assert result.metadata["reported_total"] == 25

    @pytest.mark.asyncio
    async def test_fetch_empty_first_page_unknown(self, adapter):
        """Test empty first page returns UNKNOWN."""
        mock_response = {
            "items": [],
            "total_pages": 0,
            "total": 0,
        }

        with patch.object(adapter, "_fetch_page", new_callable=AsyncMock) as mock_fetch:
            mock_fetch.return_value = mock_response

            result = await adapter.fetch()

            assert result.completeness == CrawlCompleteness.UNKNOWN
            assert len(result.raw_items) == 0

    @pytest.mark.asyncio
    async def test_fetch_with_max_pages_partial(self, adapter):
        """Test max_pages limit returns PARTIAL."""
        adapter.max_pages = 2

        page_response = {
            "items": [{"listing_id": f"{i:03d}"} for i in range(10)],
            "total_pages": 5,
            "total": 50,
        }

        with patch.object(adapter, "_fetch_page", new_callable=AsyncMock) as mock_fetch:
            mock_fetch.return_value = page_response

            result = await adapter.fetch()

            assert result.completeness == CrawlCompleteness.PARTIAL
            assert len(result.raw_items) == 20  # 2 pages × 10 items
            assert result.metadata["pages_fetched"] == 2

    @pytest.mark.asyncio
    async def test_fetch_reported_total_mismatch_partial(self, adapter):
        """Test reported_total mismatch returns PARTIAL."""
        page1_response = {
            "items": [{"listing_id": "001"}],
            "total_pages": 1,
            "total": 10,  # Claims 10 but only returns 1
        }

        with patch.object(adapter, "_fetch_page", new_callable=AsyncMock) as mock_fetch:
            mock_fetch.return_value = page1_response

            result = await adapter.fetch()

            assert result.completeness == CrawlCompleteness.PARTIAL
            assert len(result.raw_items) == 1
            assert result.metadata["reported_total"] == 10

    @pytest.mark.asyncio
    async def test_fetch_early_empty_page_stops(self, adapter):
        """Test that an empty page after data pages stops fetching."""
        page1_response = {
            "items": [{"listing_id": f"{i:03d}"} for i in range(10)],
            "total_pages": 3,
            "total": 25,
        }
        page2_response = {
            "items": [],  # Empty page
        }

        with patch.object(adapter, "_fetch_page", new_callable=AsyncMock) as mock_fetch:
            mock_fetch.side_effect = [page1_response, page2_response]

            result = await adapter.fetch()

            # Should stop after page 2 (empty)
            assert mock_fetch.call_count == 2
            assert len(result.raw_items) == 10
            assert result.metadata["pages_fetched"] == 2
            assert result.completeness == CrawlCompleteness.PARTIAL
            assert "early_empty_page" in result.metadata["completeness_reasons"]

    @pytest.mark.asyncio
    async def test_reported_total_smaller_than_items_is_partial(self, adapter):
        with patch.object(adapter, "_fetch_page", new_callable=AsyncMock) as mock_fetch:
            mock_fetch.return_value = {
                "items": [{"listing_id": "001"}, {"listing_id": "002"}],
                "total_pages": 1,
                "total": 1,
            }

            result = await adapter.fetch()

        assert result.completeness == CrawlCompleteness.PARTIAL
        assert "reported_total_mismatch" in result.metadata["completeness_reasons"]

    @pytest.mark.asyncio
    async def test_pagination_metadata_cannot_change_between_pages(self, adapter):
        page1 = {
            "items": [{"listing_id": "001"}],
            "total_pages": 2,
            "total": 2,
        }
        page2 = {
            "items": [{"listing_id": "002"}],
            "total_pages": 3,
            "total": 2,
        }
        with patch.object(adapter, "_fetch_page", new_callable=AsyncMock) as mock_fetch:
            mock_fetch.side_effect = [page1, page2]

            with pytest.raises(SourcePayloadValidationError, match="changed between pages"):
                await adapter.fetch()

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("observed_at", "reason"),
        [
            (None, "observed_at_missing"),
            ("not-a-time", "observed_at_invalid"),
            (
                (datetime.now(UTC) - timedelta(hours=48)).isoformat(),
                "observed_at_stale",
            ),
            (
                (datetime.now(UTC) + timedelta(hours=1)).isoformat(),
                "observed_at_in_future",
            ),
        ],
    )
    async def test_live_freshness_gate_downgrades_untrusted_snapshot(
        self, mock_scope, observed_at, reason
    ):
        item = {"listing_id": "001"}
        if observed_at is not None:
            item["observed_at"] = observed_at
        source = AuthorizedAPIAdapter(
            source_id="test_api",
            api_endpoint="https://api.example.com/v1/listings",
            scope=mock_scope,
            require_observed_at=True,
            max_observation_age_hours=36,
        )
        with patch.object(source, "_fetch_page", new_callable=AsyncMock) as mock_fetch:
            mock_fetch.return_value = {"items": [item], "total_pages": 1, "total": 1}

            result = await source.fetch()

        assert result.completeness is CrawlCompleteness.PARTIAL
        assert reason in result.metadata["completeness_reasons"]

    @pytest.mark.asyncio
    async def test_live_freshness_gate_accepts_current_timestamp(self, mock_scope):
        source = AuthorizedAPIAdapter(
            source_id="test_api",
            api_endpoint="https://api.example.com/v1/listings",
            scope=mock_scope,
            require_observed_at=True,
            max_observation_age_hours=36,
        )
        with patch.object(source, "_fetch_page", new_callable=AsyncMock) as mock_fetch:
            mock_fetch.return_value = {
                "items": [
                    {
                        "listing_id": "001",
                        "observed_at": (datetime.now(UTC) - timedelta(minutes=5)).isoformat(),
                    }
                ],
                "total_pages": 1,
                "total": 1,
            }

            result = await source.fetch()

        assert result.completeness is CrawlCompleteness.COMPLETE
        assert result.metadata["observation_freshness"]["timestamped_items"] == 1
        assert result.metadata["observation_freshness"]["reasons"] == []

    def test_normalize_valid_item(self, adapter):
        """Test normalizing a valid canonical listing item."""
        raw_item = {
            "listing_id": "test_001",
            "url": "https://example.com/listing/test_001",
            "district": "xuhui",
            "submarket": "kangjian",
            "community": "Test Community",
            "longitude": 121.4,
            "latitude": 31.2,
            "price_wan": 300,
            "area_sqm": 80,
            "bedrooms": 2,
            "living_rooms": 1,
            "status": "active",
        }

        observation = adapter.normalize(raw_item)

        assert observation.source == "test_api"
        assert observation.source_listing_id == "test_001"
        assert observation.district == "xuhui"
        assert observation.total_price == 3000000

    def test_source_record_id(self, adapter):
        """Test extracting source record ID from raw item."""
        raw_item = {"listing_id": "test_001"}

        record_id = adapter.source_record_id(raw_item)

        assert record_id == "test_001"

    def test_source_record_id_missing(self, adapter):
        """Test source_record_id returns None for missing listing_id."""
        raw_item = {"other_field": "value"}

        record_id = adapter.source_record_id(raw_item)

        assert record_id is None

    def test_build_request_params(self, adapter):
        """Test building request parameters with scope filters."""
        params = adapter._build_request_params(page=2)

        assert params["page"] == 2
        assert params["page_size"] == 10
        assert params["status"] == "active"
        assert params["city"] == "shanghai"
        assert params["district"] == "xuhui"

    def test_build_request_params_minimal_scope(self):
        """Test building request params with minimal scope."""
        scope = CrawlScope(city="shanghai")
        adapter = AuthorizedAPIAdapter(
            source_id="test_api",
            api_endpoint="https://api.example.com/v1/listings",
            scope=scope,
        )

        params = adapter._build_request_params(page=1)

        assert params["city"] == "shanghai"
        assert "district" not in params
        assert "submarket" not in params

    def test_build_request_params_includes_query(self):
        source = AuthorizedAPIAdapter(
            source_id="test_api",
            api_endpoint="https://api.example.com/v1/listings",
            scope=CrawlScope(city="shanghai", query="two-bedroom"),
        )

        assert source._build_request_params(page=1)["query"] == "two-bedroom"

    def test_production_endpoint_requires_https(self, mock_scope):
        with pytest.raises(ValueError, match="HTTPS"):
            AuthorizedAPIAdapter(
                source_id="test_api",
                api_endpoint="http://api.example.com/v1/listings",
                scope=mock_scope,
                environment="production",
            )

    @pytest.mark.asyncio
    async def test_fetch_rejects_malformed_items(self, adapter):
        with patch.object(adapter, "_fetch_page", new_callable=AsyncMock) as mock_fetch:
            mock_fetch.return_value = {"items": "not-a-list"}

            with pytest.raises(SourcePayloadValidationError, match="list of objects"):
                await adapter.fetch()

    @pytest.mark.asyncio
    async def test_provider_reflected_secret_is_rejected(self, mock_scope, mock_auth):
        def handler(_: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json={
                    "items": [],
                    "total_pages": 0,
                    "total": 0,
                    "echo": "Bearer test_token_123",
                },
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            source = AuthorizedAPIAdapter(
                source_id="test_api",
                api_endpoint="https://api.example.com/v1/listings",
                scope=mock_scope,
                auth=mock_auth,
                client=client,
            )
            with pytest.raises(SourcePayloadValidationError) as error:
                await source.fetch()

        assert "test_token_123" not in str(error.value)

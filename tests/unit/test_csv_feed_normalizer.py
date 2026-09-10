from __future__ import annotations

from pathlib import Path

import pytest
from home_radar_collector.adapters import CanonicalJsonFeedAdapter
from home_radar_collector.contracts import CrawlScope
from home_radar_collector.errors import ListingNormalizationError, SourcePayloadValidationError
from home_radar_collector.feed import FeedCoverage
from home_radar_collector.normalizers import CsvFeedContext, CsvFeedNormalizer
from home_radar_models.enums import CrawlCompleteness

CSV_HEADER = "listing_id,url,district,submarket,community,price_wan,area_sqm,elevator\n"
CSV_ROW = "csv-1,https://example.invalid/csv-1,Xuhui,Huajing,Garden,300,60,false\n"


def test_csv_normalizer_defaults_to_partial_without_provider_manifest() -> None:
    normalizer = CsvFeedNormalizer(
        CsvFeedContext(source_id="partner_feed", scope=CrawlScope(city="shanghai"))
    )

    payload = normalizer.normalize(CSV_HEADER + CSV_ROW)

    assert payload["completeness"] == "partial"
    assert payload["coverage"] == {"items_returned": 1}


@pytest.mark.asyncio
async def test_csv_with_explicit_manifest_flows_through_canonical_adapter(
    tmp_path: Path,
) -> None:
    fixture = tmp_path / "feed.csv"
    fixture.write_text(CSV_HEADER + CSV_ROW, encoding="utf-8")
    scope = CrawlScope(city="shanghai")
    normalizer = CsvFeedNormalizer(
        CsvFeedContext(
            source_id="partner_feed",
            scope=scope,
            completeness=CrawlCompleteness.COMPLETE,
            coverage=FeedCoverage(
                page_count=1,
                pages_fetched=1,
                reported_total=1,
            ),
        )
    )
    source = CanonicalJsonFeedAdapter(
        source_id="partner_feed",
        endpoint=str(fixture),
        scope=scope,
        normalizer=normalizer,
    )

    result = await source.fetch()
    observation = source.normalize(result.raw_items[0])

    assert result.completeness is CrawlCompleteness.COMPLETE
    assert observation.source_listing_id == "csv-1"
    assert observation.elevator is False


def test_csv_bad_row_remains_a_per_item_normalization_failure(tmp_path: Path) -> None:
    fixture = tmp_path / "feed.csv"
    fixture.write_text(CSV_HEADER + CSV_ROW.replace(",300,", ",bad-price,"), encoding="utf-8")
    scope = CrawlScope(city="shanghai")
    normalizer = CsvFeedNormalizer(CsvFeedContext(source_id="partner_feed", scope=scope))
    payload = normalizer.normalize(fixture.read_bytes())
    source = CanonicalJsonFeedAdapter(
        source_id="partner_feed",
        endpoint=str(fixture),
        scope=scope,
        normalizer=normalizer,
    )

    with pytest.raises(ListingNormalizationError):
        source.normalize(payload["items"][0])


def test_csv_missing_required_columns_is_rejected() -> None:
    normalizer = CsvFeedNormalizer(
        CsvFeedContext(source_id="partner_feed", scope=CrawlScope(city="shanghai"))
    )

    with pytest.raises(SourcePayloadValidationError):
        normalizer.normalize("listing_id,url\na-1,https://example.invalid/a-1\n")

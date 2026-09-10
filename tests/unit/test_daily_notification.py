from __future__ import annotations

import json
from decimal import Decimal

import httpx
import pytest
from home_radar_notification import build_daily_digest, deliver_daily_digest


def result_payload() -> dict[str, object]:
    return {
        "run_id": "run-1",
        "end_time": "2026-09-04T01:00:00+00:00",
        "daily_report": {
            "date": "2026-09-04",
            "new_listings": 12,
            "price_changes": 3,
            "new_inactive": 1,
        },
        "opportunity_snapshot": {
            "data_mode": "sample",
            "evaluated_count": 499,
            "eligible_count": 21,
            "fresh_eligible_count": 1,
            "workflow_counts": {"PASS": 478, "WATCH": 20, "VIEW": 1},
            "items": [
                {
                    "listing_id": "listing-1",
                    "rank": 1,
                    "activity_signal": "PRICE_CUT",
                    "price_change_pct": Decimal("-5.00"),
                    "community": "测试小区",
                    "area_sqm": Decimal("60.50"),
                    "asking_price_wan": Decimal("298.00"),
                    "fair_value_wan": Decimal("318.00"),
                    "fair_value_low_wan": Decimal("305.00"),
                    "fair_value_high_wan": Decimal("329.00"),
                    "value_score": Decimal("87"),
                    "future_score": Decimal("84"),
                    "liquidity_score": Decimal("91"),
                    "obsolescence_risk": Decimal("26"),
                    "opportunity_classification": "QUALITY_AT_DISCOUNT",
                    "workflow_state": "VIEW",
                    "positive_reasons": [{"code": "DISCOUNT", "text": "价格低于公允价值"}],
                    "negative_reasons": [{"code": "AGING", "text": "楼龄偏高"}],
                    "source_url": "https://example.invalid/listing/1",
                }
            ],
            "fresh_items": [
                {
                    "listing_id": "listing-1",
                    "rank": 1,
                    "activity_signal": "PRICE_CUT",
                    "price_change_pct": Decimal("-5.00"),
                    "community": "测试小区",
                    "area_sqm": Decimal("60.50"),
                    "asking_price_wan": Decimal("298.00"),
                    "fair_value_wan": Decimal("318.00"),
                    "fair_value_low_wan": Decimal("305.00"),
                    "fair_value_high_wan": Decimal("329.00"),
                    "value_score": Decimal("87"),
                    "future_score": Decimal("84"),
                    "liquidity_score": Decimal("91"),
                    "obsolescence_risk": Decimal("26"),
                    "opportunity_classification": "QUALITY_AT_DISCOUNT",
                    "workflow_state": "VIEW",
                    "positive_reasons": ["价格低于公允价值"],
                    "negative_reasons": ["楼龄偏高"],
                    "source_url": "https://example.invalid/listing/1",
                }
            ],
        },
    }


def test_digest_is_explainable_and_explicitly_not_advice() -> None:
    digest = build_daily_digest(result_payload(), advisory_status="RESEARCH_ONLY")

    assert digest["investment_advice"] is False
    assert digest["advisory_status"] == "RESEARCH_ONLY"
    assert digest["summary"]["eligible_count"] == 21
    assert digest["summary"]["delivered_count"] == 1
    assert digest["summary"]["fresh_eligible_count"] == 1
    assert digest["summary"]["fresh_delivered_count"] == 1
    assert digest["opportunities"][0]["asking_price_wan"] == "298.00"
    assert digest["fresh_opportunities"][0]["activity_signal"] == "PRICE_CUT"
    assert "今日变化" in digest["text"]
    assert "[降价]" in digest["text"]
    assert "调价 -5.00%" in digest["text"]
    assert digest["text"].count("测试小区") == 1
    assert "价格低于公允价值" in digest["text"]
    assert "楼龄偏高" in digest["text"]
    assert "VIEW 1 | CONTACT 0 | WATCH 20" in digest["text"]
    assert "QUALITY_AT_DISCOUNT / VIEW" in digest["text"]
    assert "Fair 318.00万 (305.00-329.00)" in digest["text"]
    assert "Liquidity 91 | Risk 26" in digest["text"]
    assert "{'code'" not in digest["text"]
    assert "不构成买入建议" in digest["text"]


def test_digest_keeps_legacy_string_reasons_readable() -> None:
    payload = result_payload()
    snapshot = payload["opportunity_snapshot"]
    assert isinstance(snapshot, dict)
    items = snapshot["items"]
    assert isinstance(items, list)
    item = items[0]
    assert isinstance(item, dict)
    item["positive_reasons"] = ["字符串理由"]
    fresh_items = snapshot["fresh_items"]
    assert isinstance(fresh_items, list)
    fresh_item = fresh_items[0]
    assert isinstance(fresh_item, dict)
    fresh_item["positive_reasons"] = ["字符串理由"]

    digest = build_daily_digest(payload, advisory_status="RESEARCH_ONLY")

    assert "  + 字符串理由" in digest["text"]


@pytest.mark.asyncio
async def test_delivery_sets_idempotency_and_auth_headers() -> None:
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["headers"] = request.headers
        seen["payload"] = json.loads(request.content)
        return httpx.Response(202)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await deliver_daily_digest(
            build_daily_digest(result_payload(), advisory_status="RESEARCH_ONLY"),
            webhook_url="https://notify.example.invalid/daily",
            bearer_token="secret",
            run_id="run-1",
            client=client,
        )

    headers = seen["headers"]
    assert isinstance(headers, httpx.Headers)
    assert headers["Idempotency-Key"] == "home-radar-daily-run-1"
    assert headers["Authorization"] == "Bearer secret"
    assert seen["payload"]["event"] == "daily_opportunity_digest"  # type: ignore[index]
    assert result["status"] == "delivered"
    assert result["http_status"] == 202


@pytest.mark.asyncio
async def test_delivery_failure_is_visible_to_caller() -> None:
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _request: httpx.Response(503))
    ) as client:
        with pytest.raises(httpx.HTTPError):
            await deliver_daily_digest(
                {},
                webhook_url="https://notify.example.invalid/daily",
                bearer_token=None,
                run_id="run-1",
                max_attempts=1,
                client=client,
            )


@pytest.mark.parametrize(
    "url",
    ["file:///tmp/result", "https://user:pass@example.invalid/hook"],
)
@pytest.mark.asyncio
async def test_delivery_rejects_unsafe_webhook_url(url: str) -> None:
    with pytest.raises(ValueError):
        await deliver_daily_digest({}, webhook_url=url, bearer_token=None, run_id="run-1")


@pytest.mark.asyncio
async def test_authenticated_delivery_rejects_plain_http() -> None:
    with pytest.raises(ValueError, match="HTTPS"):
        await deliver_daily_digest(
            {},
            webhook_url="http://notify.example.invalid/daily",
            bearer_token="secret",
            run_id="run-1",
        )

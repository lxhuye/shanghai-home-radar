from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any, cast
from urllib.parse import urlsplit

import httpx
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential_jitter


class RetryableDeliveryError(httpx.HTTPError):
    """A temporary webhook response that can be retried safely by run id."""


def build_daily_digest(results: Mapping[str, Any], *, advisory_status: str) -> dict[str, Any]:
    """Build one channel-neutral, explicitly non-advisory daily opportunity digest."""

    report_value = results.get("daily_report")
    snapshot_value = results.get("opportunity_snapshot")
    report = report_value if isinstance(report_value, dict) else {}
    snapshot = snapshot_value if isinstance(snapshot_value, dict) else {}
    items_value = snapshot.get("items")
    items = items_value if isinstance(items_value, list) else []
    fresh_value = snapshot.get("fresh_items")
    fresh_items = fresh_value if isinstance(fresh_value, list) else []
    digest = {
        "event": "daily_opportunity_digest",
        "run_id": results.get("run_id"),
        "generated_at": results.get("end_time") or datetime.now(UTC).isoformat(),
        "data_mode": snapshot.get("data_mode"),
        "advisory_status": advisory_status,
        "investment_advice": False,
        "summary": {
            "date": report.get("date"),
            "new_listings": report.get("new_listings"),
            "price_changes": report.get("price_changes"),
            "new_inactive": report.get("new_inactive"),
            "evaluated_count": snapshot.get("evaluated_count"),
            "eligible_count": snapshot.get("eligible_count"),
            "fresh_eligible_count": snapshot.get("fresh_eligible_count"),
            "delivered_count": len(items),
            "fresh_delivered_count": len(fresh_items),
            "workflow_counts": snapshot.get("workflow_counts") or {},
        },
        "opportunities": items,
        "fresh_opportunities": fresh_items,
    }
    digest["text"] = _format_digest_text(digest)
    return cast(dict[str, Any], _json_safe(digest))


async def deliver_daily_digest(
    digest: Mapping[str, Any],
    *,
    webhook_url: str,
    bearer_token: str | None,
    run_id: str,
    timeout_seconds: float = 10,
    max_attempts: int = 3,
    client: httpx.AsyncClient | None = None,
) -> dict[str, Any]:
    """Deliver once with an idempotency key; callers decide whether failure is fatal."""

    _validate_webhook_url(webhook_url, bearer_token=bearer_token)
    if max_attempts < 1:
        raise ValueError("daily result webhook max_attempts must be positive")
    headers = {
        "Content-Type": "application/json",
        "Idempotency-Key": f"home-radar-daily-{run_id}",
        "X-Home-Radar-Run-ID": run_id,
    }
    if bearer_token:
        headers["Authorization"] = f"Bearer {bearer_token}"

    retryer = retry(
        retry=retry_if_exception_type(
            (httpx.TimeoutException, httpx.TransportError, RetryableDeliveryError)
        ),
        wait=wait_exponential_jitter(initial=0.5, max=5),
        stop=stop_after_attempt(max_attempts),
        reraise=True,
    )

    @retryer
    async def send(active_client: httpx.AsyncClient) -> httpx.Response:
        response = await active_client.post(
            webhook_url,
            json=dict(digest),
            headers=headers,
            timeout=timeout_seconds,
            follow_redirects=False,
        )
        if response.status_code == 429 or response.status_code >= 500:
            raise RetryableDeliveryError(
                f"temporary daily result webhook response: {response.status_code}"
            )
        response.raise_for_status()
        return response

    if client is not None:
        response = await send(client)
    else:
        async with httpx.AsyncClient(follow_redirects=False) as active_client:
            response = await send(active_client)
    return {
        "status": "delivered",
        "http_status": response.status_code,
        "delivered_at": datetime.now(UTC).isoformat(),
    }


def _format_digest_text(digest: Mapping[str, Any]) -> str:
    summary_value = digest.get("summary")
    summary = summary_value if isinstance(summary_value, dict) else {}
    lines = [
        f"Shanghai Home Radar {summary.get('date') or ''}".rstrip(),
        (
            f"模式 {digest.get('data_mode') or 'unknown'} | "
            f"状态 {digest.get('advisory_status') or 'RESEARCH_ONLY'}"
        ),
        (
            f"新增 {summary.get('new_listings') or 0} | "
            f"调价 {summary.get('price_changes') or 0} | "
            f"候选 {summary.get('eligible_count') or 0} | "
            f"今日变化候选 {summary.get('fresh_eligible_count') or 0}"
        ),
    ]
    workflow_counts = summary.get("workflow_counts")
    if isinstance(workflow_counts, dict):
        lines.append(
            f"VIEW {workflow_counts.get('VIEW') or 0} | "
            f"CONTACT {workflow_counts.get('CONTACT') or 0} | "
            f"WATCH {workflow_counts.get('WATCH') or 0}"
        )
    seen_listing_ids: set[str] = set()
    fresh = digest.get("fresh_opportunities")
    if isinstance(fresh, list) and fresh:
        lines.append("今日变化")
        _append_opportunities(lines, fresh, seen_listing_ids=seen_listing_ids)
    opportunities = digest.get("opportunities")
    if isinstance(opportunities, list) and opportunities:
        lines.append("综合排名")
        _append_opportunities(lines, opportunities, seen_listing_ids=seen_listing_ids)
    lines.append("工作流候选，不构成买入建议。")
    return "\n".join(lines)


def _append_opportunities(
    lines: list[str],
    opportunities: list[object],
    *,
    seen_listing_ids: set[str],
) -> None:
    for item in opportunities:
        if not isinstance(item, dict):
            continue
        listing_id = str(item.get("listing_id") or "")
        if listing_id and listing_id in seen_listing_ids:
            continue
        if listing_id:
            seen_listing_ids.add(listing_id)
        reasons = item.get("positive_reasons")
        reason = _first_reason_text(reasons)
        risks = item.get("negative_reasons")
        risk = _first_reason_text(risks)
        signal = _activity_label(item.get("activity_signal"))
        lines.append(
            f"#{item.get('rank')} [{signal}] {item.get('community') or '未知小区'} "
            f"{_display(item.get('area_sqm'))}㎡ "
            f"{_display(item.get('asking_price_wan'))}万 | "
            f"Value {_display(item.get('value_score'))} "
            f"Future {_display(item.get('future_score'))} | "
            f"{item.get('opportunity_classification') or '未分类'} / "
            f"{item.get('workflow_state') or '未分流'}"
        )
        lines.append(
            f"  Fair {_fair_value_text(item)} | "
            f"Liquidity {_display(item.get('liquidity_score'))} | "
            f"Risk {_display(item.get('obsolescence_risk'))}"
        )
        if item.get("price_change_pct") is not None:
            lines.append(f"  调价 {item['price_change_pct']}%")
        if reason:
            lines.append(f"  + {reason}")
        if risk:
            lines.append(f"  - {risk}")
        if item.get("source_url"):
            lines.append(f"  {item['source_url']}")


def _first_reason_text(value: object) -> str:
    if not isinstance(value, list) or not value:
        return ""
    reason = value[0]
    if isinstance(reason, str):
        return reason.strip()
    if isinstance(reason, Mapping):
        text = reason.get("text")
        if isinstance(text, str):
            return text.strip()
        code = reason.get("code")
        return code.strip() if isinstance(code, str) else ""
    return ""


def _fair_value_text(item: Mapping[str, Any]) -> str:
    fair_value = _display(item.get("fair_value_wan"))
    low = item.get("fair_value_low_wan")
    high = item.get("fair_value_high_wan")
    if low is not None and high is not None:
        return f"{fair_value}万 ({_display(low)}-{_display(high)})"
    return f"{fair_value}万"


def _display(value: object) -> str:
    return "?" if value is None or value == "" else str(value)


def _activity_label(value: object) -> str:
    return {
        "NEW": "新增",
        "PRICE_CUT": "降价",
        "PRICE_INCREASE": "涨价",
        "RELISTED": "重新挂牌",
        "UNCHANGED": "存量",
    }.get(str(value), "存量")


def _json_safe(value: object) -> object:
    return json.loads(json.dumps(value, ensure_ascii=False, default=str))


def _validate_webhook_url(value: str, *, bearer_token: str | None) -> None:
    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("daily result webhook must be an HTTP(S) URL")
    if parsed.username or parsed.password:
        raise ValueError("daily result webhook URL must not contain credentials")
    if bearer_token and parsed.scheme != "https":
        raise ValueError("authenticated daily result webhook must use HTTPS")

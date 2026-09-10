from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any, cast

from fastapi import APIRouter, HTTPException
from home_radar_shared.config import get_settings

router = APIRouter(prefix="/api/v1/operations", tags=["operations"])


@router.get("/daily/latest", response_model=dict[str, Any])
def get_latest_daily_run() -> dict[str, Any]:
    """Return the latest immutable daily pipeline artifact."""
    return _load_latest_daily_run()


@router.get("/daily/digest", response_model=dict[str, Any])
def get_latest_daily_digest() -> dict[str, Any]:
    """Return the latest channel-neutral, human-readable opportunity digest."""
    value = _load_latest_daily_run()
    digest = value.get("digest")
    if isinstance(digest, dict):
        return cast(dict[str, Any], digest)
    if (
        value.get("status") == "success"
        and isinstance(value.get("daily_report"), dict)
        and isinstance(value.get("opportunity_snapshot"), dict)
    ):
        from home_radar_notification import build_daily_digest

        return build_daily_digest(
            value,
            advisory_status=get_settings().daily_advisory_status,
        )
    raise HTTPException(status_code=503, detail="daily digest is unavailable")


@router.get("/daily/health", response_model=dict[str, Any])
def get_daily_health() -> dict[str, Any]:
    settings = get_settings()
    try:
        value = _load_latest_daily_run()
    except HTTPException as exc:
        raise _unhealthy("missing_or_unreadable_artifact") from exc
    if value.get("status") != "success":
        raise _unhealthy("latest_run_failed", run_id=value.get("run_id"))
    completed_at = _parse_completed_at(value.get("end_time"))
    if completed_at is None:
        raise _unhealthy("invalid_completion_timestamp", run_id=value.get("run_id"))
    age_hours = (datetime.now(UTC) - completed_at).total_seconds() / 3600
    if age_hours < 0 or age_hours > settings.daily_health_max_age_hours:
        raise _unhealthy(
            "latest_run_stale",
            run_id=value.get("run_id"),
            age_hours=round(age_hours, 3),
        )
    snapshot = value.get("opportunity_snapshot")
    data_mode = snapshot.get("data_mode") if isinstance(snapshot, dict) else None
    if data_mode != settings.market_data_mode:
        raise _unhealthy("data_mode_mismatch", run_id=value.get("run_id"))
    opportunity_health = value.get("opportunity_health")
    opportunity_health_status = (
        opportunity_health.get("status") if isinstance(opportunity_health, Mapping) else None
    )
    if data_mode == "live" and opportunity_health_status != "healthy":
        raise _unhealthy(
            "opportunity_output_unhealthy",
            run_id=value.get("run_id"),
            opportunity_health_status=opportunity_health_status or "missing",
        )
    delivery = value.get("delivery")
    delivery_status = delivery.get("status") if isinstance(delivery, dict) else None
    if getattr(settings, "daily_result_webhook_url", None) and delivery_status != "delivered":
        raise _unhealthy(
            "daily_result_not_delivered",
            run_id=value.get("run_id"),
            delivery_status=delivery_status,
        )
    return {
        "status": "healthy",
        "run_id": value.get("run_id"),
        "data_mode": data_mode,
        "last_completed_at": completed_at.isoformat(),
        "age_hours": round(age_hours, 3),
        "delivery_status": delivery_status or "disabled",
        "opportunity_health_status": opportunity_health_status or "unknown",
    }


def _load_latest_daily_run() -> dict[str, Any]:
    path = get_settings().daily_report_directory / "latest.json"
    if not path.is_file():
        raise HTTPException(status_code=404, detail="no daily pipeline artifact exists")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise HTTPException(
            status_code=503, detail="daily pipeline artifact is unreadable"
        ) from exc
    if not isinstance(value, dict):
        raise HTTPException(status_code=503, detail="daily pipeline artifact is invalid")
    return cast(dict[str, Any], value)


def _parse_completed_at(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.astimezone(UTC) if parsed.tzinfo is not None else None


def _unhealthy(reason: str, **details: object) -> HTTPException:
    return HTTPException(
        status_code=503,
        detail={"status": "unhealthy", "reason": reason, **details},
    )

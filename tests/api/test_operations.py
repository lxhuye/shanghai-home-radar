from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import home_radar_api.routes.operations as operations
import httpx
import pytest
from home_radar_api.main import app


@pytest.mark.asyncio
async def test_latest_daily_run_returns_persisted_artifact(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    payload = {
        "run_id": "run-1",
        "status": "success",
        "opportunity_snapshot": {"eligible_count": 2, "items": []},
    }
    (tmp_path / "latest.json").write_text(json.dumps(payload), encoding="utf-8")
    monkeypatch.setattr(
        operations,
        "get_settings",
        lambda: SimpleNamespace(daily_report_directory=tmp_path),
    )

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/api/v1/operations/daily/latest")

    assert response.status_code == 200
    assert response.json() == payload


@pytest.mark.asyncio
async def test_latest_daily_run_returns_404_before_first_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        operations,
        "get_settings",
        lambda: SimpleNamespace(daily_report_directory=tmp_path),
    )

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/api/v1/operations/daily/latest")

    assert response.status_code == 404
    assert response.json() == {"detail": "no daily pipeline artifact exists"}


@pytest.mark.asyncio
async def test_daily_digest_returns_frozen_digest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    digest = {"event": "daily_opportunity_digest", "text": "可读榜单"}
    (tmp_path / "latest.json").write_text(
        json.dumps({"run_id": "run-1", "status": "success", "digest": digest}),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        operations,
        "get_settings",
        lambda: SimpleNamespace(daily_report_directory=tmp_path),
    )

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/api/v1/operations/daily/digest")

    assert response.status_code == 200
    assert response.json() == digest


@pytest.mark.asyncio
async def test_daily_digest_builds_legacy_success_artifact(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    payload = {
        "run_id": "run-legacy",
        "status": "success",
        "end_time": "2026-09-04T01:00:00+00:00",
        "daily_report": {"date": "2026-09-04"},
        "opportunity_snapshot": {
            "data_mode": "sample",
            "eligible_count": 0,
            "items": [],
            "fresh_items": [],
        },
    }
    (tmp_path / "latest.json").write_text(json.dumps(payload), encoding="utf-8")
    monkeypatch.setattr(
        operations,
        "get_settings",
        lambda: SimpleNamespace(
            daily_report_directory=tmp_path,
            daily_advisory_status="RESEARCH_ONLY",
        ),
    )

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/api/v1/operations/daily/digest")

    assert response.status_code == 200
    assert response.json()["event"] == "daily_opportunity_digest"
    assert response.json()["investment_advice"] is False


@pytest.mark.asyncio
async def test_daily_health_accepts_recent_success(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    payload = {
        "run_id": "run-healthy",
        "status": "success",
        "end_time": datetime.now(UTC).isoformat(),
        "opportunity_snapshot": {"data_mode": "sample"},
        "opportunity_health": {"status": "healthy"},
    }
    (tmp_path / "latest.json").write_text(json.dumps(payload), encoding="utf-8")
    monkeypatch.setattr(
        operations,
        "get_settings",
        lambda: SimpleNamespace(
            daily_report_directory=tmp_path,
            daily_health_max_age_hours=30,
            market_data_mode="sample",
        ),
    )

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/api/v1/operations/daily/health")

    assert response.status_code == 200
    assert response.json()["status"] == "healthy"
    assert response.json()["run_id"] == "run-healthy"
    assert response.json()["delivery_status"] == "disabled"
    assert response.json()["opportunity_health_status"] == "healthy"


@pytest.mark.asyncio
async def test_daily_health_requires_delivery_when_webhook_is_configured(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    payload = {
        "run_id": "run-undelivered",
        "status": "success",
        "end_time": datetime.now(UTC).isoformat(),
        "opportunity_snapshot": {"data_mode": "sample"},
        "delivery": {"status": "disabled"},
    }
    (tmp_path / "latest.json").write_text(json.dumps(payload), encoding="utf-8")
    monkeypatch.setattr(
        operations,
        "get_settings",
        lambda: SimpleNamespace(
            daily_report_directory=tmp_path,
            daily_health_max_age_hours=30,
            market_data_mode="sample",
            daily_result_webhook_url="https://notify.example.invalid/daily",
        ),
    )

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/api/v1/operations/daily/health")

    assert response.status_code == 503
    assert response.json()["detail"]["reason"] == "daily_result_not_delivered"


@pytest.mark.asyncio
async def test_daily_health_rejects_stale_result(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    payload = {
        "run_id": "run-stale",
        "status": "success",
        "end_time": (datetime.now(UTC) - timedelta(hours=31)).isoformat(),
        "opportunity_snapshot": {"data_mode": "sample"},
    }
    (tmp_path / "latest.json").write_text(json.dumps(payload), encoding="utf-8")
    monkeypatch.setattr(
        operations,
        "get_settings",
        lambda: SimpleNamespace(
            daily_report_directory=tmp_path,
            daily_health_max_age_hours=30,
            market_data_mode="sample",
        ),
    )

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/api/v1/operations/daily/health")

    assert response.status_code == 503
    assert response.json()["detail"]["reason"] == "latest_run_stale"


@pytest.mark.asyncio
async def test_daily_health_rejects_live_result_without_output_health(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    payload = {
        "run_id": "run-live-unguarded",
        "status": "success",
        "end_time": datetime.now(UTC).isoformat(),
        "opportunity_snapshot": {"data_mode": "live"},
    }
    (tmp_path / "latest.json").write_text(json.dumps(payload), encoding="utf-8")
    monkeypatch.setattr(
        operations,
        "get_settings",
        lambda: SimpleNamespace(
            daily_report_directory=tmp_path,
            daily_health_max_age_hours=30,
            market_data_mode="live",
            daily_result_webhook_url=None,
        ),
    )

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/api/v1/operations/daily/health")

    assert response.status_code == 503
    assert response.json()["detail"] == {
        "status": "unhealthy",
        "reason": "opportunity_output_unhealthy",
        "run_id": "run-live-unguarded",
        "opportunity_health_status": "missing",
    }


@pytest.mark.asyncio
async def test_daily_health_accepts_live_decisive_empty_opportunity_set(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    payload = {
        "run_id": "run-live-no-deals",
        "status": "success",
        "end_time": datetime.now(UTC).isoformat(),
        "opportunity_snapshot": {
            "data_mode": "live",
            "evaluated_count": 20,
            "eligible_count": 0,
        },
        "opportunity_health": {"status": "healthy", "reasons": []},
    }
    (tmp_path / "latest.json").write_text(json.dumps(payload), encoding="utf-8")
    monkeypatch.setattr(
        operations,
        "get_settings",
        lambda: SimpleNamespace(
            daily_report_directory=tmp_path,
            daily_health_max_age_hours=30,
            market_data_mode="live",
            daily_result_webhook_url=None,
        ),
    )

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/api/v1/operations/daily/health")

    assert response.status_code == 200
    assert response.json()["opportunity_health_status"] == "healthy"

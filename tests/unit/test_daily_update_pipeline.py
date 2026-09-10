from __future__ import annotations

import asyncio
import copy
import json
import uuid
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import home_radar_collector.registry as collector_registry
import home_radar_collector.service as collector_service
import home_radar_decision.config as decision_config
import home_radar_decision.ranking as decision_ranking
import home_radar_decision.repository as decision_repository
import home_radar_shared.config as shared_config
import home_radar_shared.database as shared_database
import pytest
import redis
from home_radar_models.enums import DataMode

from scripts import daily_update_pipeline


@pytest.mark.asyncio
async def test_daily_pipeline_persists_delivery_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def successful_step() -> dict[str, object]:
        return {"success": True, "run_id": "crawl-1", "accepted_partial": False}

    persisted: list[dict[str, Any]] = []

    def persist(results: dict[str, Any]) -> Path:
        results["artifact_path"] = "run.json"
        persisted.append(copy.deepcopy(results))
        return Path("run.json")

    monkeypatch.setattr(daily_update_pipeline, "run_collection", successful_step)
    monkeypatch.setattr(daily_update_pipeline, "run_market_baseline", successful_step)
    monkeypatch.setattr(daily_update_pipeline, "run_valuation", successful_step)
    monkeypatch.setattr(daily_update_pipeline, "run_future_assessment", successful_step)
    monkeypatch.setattr(daily_update_pipeline, "run_decision_orchestration", successful_step)
    monkeypatch.setattr(
        daily_update_pipeline,
        "generate_daily_report",
        lambda _crawl_run_id: {"date": "2026-09-04"},
    )
    monkeypatch.setattr(
        daily_update_pipeline,
        "generate_opportunity_snapshot",
        lambda **_kwargs: {
            "evaluated_count": 10,
            "eligible_count": 2,
            "classification_counts": {"GOOD_BUT_EXPENSIVE": 10},
            "items": [],
        },
    )
    monkeypatch.setattr(
        daily_update_pipeline,
        "deliver_daily_result",
        lambda _results: _async_value({"status": "delivered", "http_status": 202}),
    )
    monkeypatch.setattr(
        daily_update_pipeline,
        "persist_run_artifact",
        persist,
    )

    result = await daily_update_pipeline.daily_pipeline()

    assert result["status"] == "success"
    assert result["delivery"] == {"status": "delivered", "http_status": 202}
    assert persisted[0]["delivery"]["status"] == "delivered"


@pytest.mark.asyncio
async def test_daily_pipeline_marks_delivery_failure_as_run_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def successful_step() -> dict[str, object]:
        return {"success": True, "run_id": "crawl-1", "accepted_partial": False}

    persisted: list[dict[str, Any]] = []
    for name in (
        "run_collection",
        "run_market_baseline",
        "run_valuation",
        "run_future_assessment",
        "run_decision_orchestration",
    ):
        monkeypatch.setattr(daily_update_pipeline, name, successful_step)
    monkeypatch.setattr(daily_update_pipeline, "generate_daily_report", lambda _run_id: {})
    monkeypatch.setattr(
        daily_update_pipeline,
        "generate_opportunity_snapshot",
        lambda **_kwargs: {
            "evaluated_count": 10,
            "eligible_count": 2,
            "classification_counts": {"GOOD_BUT_EXPENSIVE": 10},
            "items": [],
        },
    )
    monkeypatch.setattr(
        daily_update_pipeline,
        "deliver_daily_result",
        lambda _results: _async_value({"status": "failed", "error": "HTTPStatusError"}),
    )
    monkeypatch.setattr(
        daily_update_pipeline,
        "persist_run_artifact",
        lambda results: persisted.append(copy.deepcopy(results)) or Path("run.json"),
    )
    monkeypatch.setattr(daily_update_pipeline, "send_alert", lambda _message: None)

    with pytest.raises(RuntimeError, match="投递失败"):
        await daily_update_pipeline.daily_pipeline()

    assert persisted[0]["status"] == "failed"
    assert persisted[0]["delivery"]["status"] == "failed"
    assert persisted[0]["failure"] == {"stage": "delivery", "retryable": False}


@pytest.mark.asyncio
async def test_daily_pipeline_blocks_live_run_with_only_insufficient_decisions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def collection_step() -> dict[str, object]:
        return {"success": True, "run_id": "crawl-1", "accepted_partial": False}

    async def successful_step() -> dict[str, object]:
        return {"success": True, "result": {"evaluated": 499, "failed": 0}}

    async def decision_step() -> dict[str, object]:
        return {
            "success": True,
            "result": {
                "evaluated": 499,
                "failed": 0,
                "skipped_insufficient_data": 0,
            },
        }

    persisted: list[dict[str, Any]] = []
    delivery_called = False

    async def deliver(_results: dict[str, Any]) -> dict[str, object]:
        nonlocal delivery_called
        delivery_called = True
        return {"status": "delivered"}

    monkeypatch.setattr(daily_update_pipeline, "run_collection", collection_step)
    monkeypatch.setattr(daily_update_pipeline, "run_market_baseline", successful_step)
    monkeypatch.setattr(daily_update_pipeline, "run_valuation", successful_step)
    monkeypatch.setattr(daily_update_pipeline, "run_future_assessment", successful_step)
    monkeypatch.setattr(daily_update_pipeline, "run_decision_orchestration", decision_step)
    monkeypatch.setattr(daily_update_pipeline, "generate_daily_report", lambda _run_id: {})
    monkeypatch.setattr(
        daily_update_pipeline,
        "generate_opportunity_snapshot",
        lambda **_kwargs: {
            "data_mode": "live",
            "evaluated_count": 499,
            "eligible_count": 0,
            "classification_counts": {"INSUFFICIENT_DATA": 499},
            "items": [],
        },
    )
    monkeypatch.setattr(
        shared_config,
        "get_settings",
        lambda: SimpleNamespace(
            market_data_mode="live",
            daily_min_decision_coverage_ratio=0.95,
        ),
    )
    monkeypatch.setattr(daily_update_pipeline, "deliver_daily_result", deliver)
    monkeypatch.setattr(
        daily_update_pipeline,
        "persist_run_artifact",
        lambda results: persisted.append(copy.deepcopy(results)) or Path("run.json"),
    )
    monkeypatch.setattr(daily_update_pipeline, "send_alert", lambda _message: None)

    with pytest.raises(RuntimeError, match="all_active_decisions_insufficient_data"):
        await daily_update_pipeline.daily_pipeline()

    assert delivery_called is False
    assert persisted[0]["status"] == "failed"
    assert persisted[0]["opportunity_health"]["status"] == "blocked"
    assert persisted[0]["failure"] == {
        "stage": "opportunity_health",
        "retryable": True,
    }


@pytest.mark.asyncio
async def test_collection_failure_is_marked_retryable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    persisted: list[dict[str, Any]] = []

    async def failed_collection() -> dict[str, object]:
        return {"success": False, "error": "temporary source failure"}

    monkeypatch.setattr(daily_update_pipeline, "run_collection", failed_collection)
    monkeypatch.setattr(
        daily_update_pipeline,
        "persist_run_artifact",
        lambda results: persisted.append(copy.deepcopy(results)) or Path("run.json"),
    )
    monkeypatch.setattr(daily_update_pipeline, "send_alert", lambda _message: None)

    with pytest.raises(daily_update_pipeline.DailyPipelineFailure) as exc_info:
        await daily_update_pipeline.daily_pipeline()

    assert exc_info.value.stage == "collection"
    assert exc_info.value.retryable is True
    assert persisted[0]["failure"] == {"stage": "collection", "retryable": True}


async def _async_value(value: dict[str, object]) -> dict[str, object]:
    return value


@pytest.mark.asyncio
async def test_disabled_delivery_still_builds_readable_digest(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        shared_config,
        "get_settings",
        lambda: SimpleNamespace(
            daily_advisory_status="RESEARCH_ONLY",
            daily_result_webhook_url=None,
        ),
    )
    results: dict[str, Any] = {
        "run_id": "run-readable",
        "end_time": "2026-09-04T01:00:00+00:00",
        "daily_report": {"date": "2026-09-04"},
        "opportunity_snapshot": {
            "data_mode": "sample",
            "eligible_count": 0,
            "items": [],
            "fresh_items": [],
        },
    }

    delivery = await daily_update_pipeline.deliver_daily_result(results)

    assert delivery == {"status": "disabled"}
    assert results["digest"]["investment_advice"] is False
    assert "工作流候选，不构成买入建议" in results["digest"]["text"]


def test_live_output_blocks_when_every_assessment_is_insufficient() -> None:
    result = daily_update_pipeline.assess_opportunity_output(
        {
            "evaluated_count": 499,
            "eligible_count": 0,
            "classification_counts": {"INSUFFICIENT_DATA": 499},
        },
        {
            "result": {
                "evaluated": 499,
                "skipped_insufficient_data": 0,
            }
        },
        data_mode="live",
        minimum_decision_coverage_ratio=0.95,
    )

    assert result["status"] == "blocked"
    assert result["reasons"] == ["all_active_decisions_insufficient_data"]


def test_sample_output_records_same_failure_as_degraded() -> None:
    result = daily_update_pipeline.assess_opportunity_output(
        {
            "evaluated_count": 50,
            "eligible_count": 0,
            "classification_counts": {"INSUFFICIENT_DATA": 50},
        },
        {"result": {"evaluated": 50, "skipped_insufficient_data": 0}},
        data_mode="sample",
        minimum_decision_coverage_ratio=0.95,
    )

    assert result["status"] == "degraded"
    assert result["insufficient_count"] == 50


def test_zero_eligible_is_healthy_when_assessments_are_decisive() -> None:
    result = daily_update_pipeline.assess_opportunity_output(
        {
            "evaluated_count": 12,
            "eligible_count": 0,
            "classification_counts": {"LOW_QUALITY": 12},
        },
        {"result": {"evaluated": 12, "skipped_insufficient_data": 0}},
        data_mode="live",
        minimum_decision_coverage_ratio=0.95,
    )

    assert result["status"] == "healthy"
    assert result["reasons"] == []


def test_live_output_blocks_when_current_decision_batch_all_skips() -> None:
    result = daily_update_pipeline.assess_opportunity_output(
        {
            "evaluated_count": 10,
            "eligible_count": 2,
            "classification_counts": {"GOOD_BUT_EXPENSIVE": 10},
        },
        {"result": {"evaluated": 0, "skipped_insufficient_data": 500}},
        data_mode="live",
        minimum_decision_coverage_ratio=0.95,
    )

    assert result["status"] == "blocked"
    assert result["reasons"] == ["current_decision_batch_all_skipped"]


def test_live_output_blocks_when_current_decision_coverage_is_too_low() -> None:
    result = daily_update_pipeline.assess_opportunity_output(
        {
            "evaluated_count": 500,
            "eligible_count": 20,
            "classification_counts": {"GOOD_BUT_EXPENSIVE": 500},
        },
        {"result": {"evaluated": 50, "skipped_insufficient_data": 450}},
        data_mode="live",
        minimum_decision_coverage_ratio=0.95,
    )

    assert result["status"] == "blocked"
    assert result["reasons"] == ["current_decision_coverage_below_minimum"]
    assert result["current_batch_coverage_ratio"] == 0.1
    assert result["current_batch_total"] == 500


def test_decision_coverage_at_minimum_is_healthy() -> None:
    result = daily_update_pipeline.assess_opportunity_output(
        {
            "evaluated_count": 100,
            "eligible_count": 5,
            "classification_counts": {"GOOD_BUT_EXPENSIVE": 100},
        },
        {"result": {"evaluated": 95, "skipped_insufficient_data": 5}},
        data_mode="live",
        minimum_decision_coverage_ratio=0.95,
    )

    assert result["status"] == "healthy"
    assert result["current_batch_coverage_ratio"] == 0.95


def test_sample_output_records_low_decision_coverage_as_degraded() -> None:
    result = daily_update_pipeline.assess_opportunity_output(
        {
            "evaluated_count": 500,
            "eligible_count": 20,
            "classification_counts": {"GOOD_BUT_EXPENSIVE": 500},
        },
        {"result": {"evaluated": 50, "skipped_insufficient_data": 450}},
        data_mode="sample",
        minimum_decision_coverage_ratio=0.95,
    )

    assert result["status"] == "degraded"
    assert result["reasons"] == ["current_decision_coverage_below_minimum"]


@pytest.mark.asyncio
async def test_collection_rejects_partial_result_and_uses_configured_mode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _FakeSession()
    observed: dict[str, object] = {}
    settings = SimpleNamespace(
        disappearance_final_after_runs=2,
        collector_min_complete_items=1,
        collector_min_complete_count_ratio=0.5,
        market_data_mode="live",
        daily_timezone="Asia/Shanghai",
    )

    class FakeCollectorService:
        def __init__(self, _session: object, **kwargs: object) -> None:
            observed.update(kwargs)

        async def collect(self, _adapter: object) -> object:
            return SimpleNamespace(
                run_id=uuid.uuid4(),
                source="test-source",
                scope_key="test-scope",
                status="succeeded",
                completeness="partial",
                normalized_item_count=12,
                reconciliation_performed=False,
                listings_missing_candidate=0,
                listings_inactivated=0,
                coverage_guard_triggered=True,
                coverage_guard_reason="unexpected_item_count_drop",
                previous_complete_count=100,
                current_count_ratio=0.12,
            )

    monkeypatch.setattr(shared_config, "get_settings", lambda: settings)
    monkeypatch.setattr(collector_registry, "build_configured_adapter", lambda _settings: object())
    monkeypatch.setattr(shared_database, "get_session_factory", lambda: lambda: session)
    monkeypatch.setattr(collector_service, "CollectorService", FakeCollectorService)

    result = await daily_update_pipeline.run_collection()

    assert result["success"] is False
    assert result["completeness"] == "partial"
    assert observed["data_mode"] is DataMode.LIVE


@pytest.mark.asyncio
async def test_collection_accepts_nonempty_partial_sample_for_calibration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _FakeSession()
    settings = SimpleNamespace(
        disappearance_final_after_runs=2,
        collector_min_complete_items=1,
        collector_min_complete_count_ratio=0.5,
        market_data_mode="sample",
        daily_timezone="Asia/Shanghai",
    )

    class FakeCollectorService:
        def __init__(self, _session: object, **_kwargs: object) -> None:
            pass

        async def collect(self, _adapter: object) -> object:
            return SimpleNamespace(
                run_id=uuid.uuid4(),
                source="test-source",
                scope_key="test-scope",
                status="succeeded",
                completeness="partial",
                normalized_item_count=500,
                reconciliation_performed=False,
                listings_missing_candidate=0,
                listings_inactivated=0,
                coverage_guard_triggered=False,
                coverage_guard_reason=None,
                previous_complete_count=None,
                current_count_ratio=None,
            )

    monkeypatch.setattr(shared_config, "get_settings", lambda: settings)
    monkeypatch.setattr(collector_registry, "build_configured_adapter", lambda _settings: object())
    monkeypatch.setattr(shared_database, "get_session_factory", lambda: lambda: session)
    monkeypatch.setattr(collector_service, "CollectorService", FakeCollectorService)

    result = await daily_update_pipeline.run_collection()

    assert result["success"] is True
    assert result["accepted_partial"] is True


def test_daily_report_uses_real_event_types(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _FakeSession()
    monkeypatch.setattr(shared_database, "get_session_factory", lambda: lambda: session)
    monkeypatch.setattr(
        shared_config,
        "get_settings",
        lambda: SimpleNamespace(daily_timezone="Asia/Shanghai"),
    )

    crawl_run_id = uuid.uuid4()
    report = daily_update_pipeline.generate_daily_report(str(crawl_run_id))

    assert "event_type = 'new'" in session.statements[0]
    assert "event_type IN ('price_cut', 'price_increase')" in session.statements[1]
    assert "crawl_run_id" in session.statements[1]
    assert "event_type = 'inactivated'" in session.statements[2]
    assert "event_type = 'relisted'" in session.statements[3]
    assert all(parameters == {"crawl_run_id": crawl_run_id} for parameters in session.parameters)
    assert "top_opportunities" not in report


def test_persist_run_artifact_writes_immutable_run_and_latest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        shared_config,
        "get_settings",
        lambda: SimpleNamespace(daily_report_directory=tmp_path),
    )
    result: dict[str, Any] = {
        "run_id": "run-1",
        "generated_at": datetime(2026, 9, 4, tzinfo=UTC),
        "price": Decimal("298.00"),
        "listing_id": uuid.UUID("00000000-0000-0000-0000-000000000001"),
    }

    run_path = daily_update_pipeline.persist_run_artifact(result)

    assert run_path == tmp_path / "run-1.json"
    assert json.loads(run_path.read_text(encoding="utf-8")) == json.loads(
        (tmp_path / "latest.json").read_text(encoding="utf-8")
    )
    assert result["artifact_path"] == str(run_path)
    with pytest.raises(FileExistsError):
        daily_update_pipeline.persist_run_artifact(result)


def test_opportunity_snapshot_excludes_inactive_and_keeps_existing_rank_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    active_id = uuid.uuid4()
    inactive_id = uuid.uuid4()
    insufficient_id = uuid.uuid4()
    listings = [
        _listing(active_id, status="active"),
        _listing(inactive_id, status="inactive"),
        _listing(insufficient_id, status="active"),
    ]
    assessments = [
        _assessment(active_id, eligibility="ELIGIBLE", score=82),
        _assessment(inactive_id, eligibility="ELIGIBLE", score=99),
        _assessment(insufficient_id, eligibility="INSUFFICIENT_DATA", score=0),
    ]
    settings = SimpleNamespace(
        daily_top_opportunities_limit=10,
        decision_config_path=Path("decision.yaml"),
        market_data_mode="sample",
    )
    monkeypatch.setattr(shared_config, "get_settings", lambda: settings)
    monkeypatch.setattr(
        shared_database,
        "get_session_factory",
        lambda: lambda: _ListingSession(listings),
    )
    monkeypatch.setattr(
        decision_config,
        "load_decision_config",
        lambda _path: SimpleNamespace(
            decision_model_version="decision-v1",
            configuration_version="config-v1",
        ),
    )
    monkeypatch.setattr(
        decision_repository,
        "latest_decision_assessments",
        lambda *_args, **_kwargs: assessments,
    )
    monkeypatch.setattr(
        decision_ranking,
        "decision_sort_key",
        lambda assessment, _config: (-assessment.value_score,),
    )

    snapshot = daily_update_pipeline.generate_opportunity_snapshot()

    assert snapshot["evaluated_count"] == 2
    assert snapshot["eligible_count"] == 1
    assert [item["listing_id"] for item in snapshot["items"]] == [active_id]
    assert snapshot["items"][0]["rank"] == 1
    assert snapshot["items"][0]["activity_signal"] == "UNCHANGED"
    assert snapshot["fresh_eligible_count"] == 0
    assert snapshot["activity_counts"] == {"UNCHANGED": 1}


def test_partial_feed_snapshot_is_limited_to_current_crawl(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    current_id = uuid.uuid4()
    stale_id = uuid.uuid4()
    crawl_run_id = uuid.uuid4()
    current_listing = _listing(current_id, status="active")
    assessments = [
        _assessment(current_id, eligibility="ELIGIBLE", score=82),
        _assessment(stale_id, eligibility="ELIGIBLE", score=99),
    ]
    settings = SimpleNamespace(
        daily_top_opportunities_limit=10,
        decision_config_path=Path("decision.yaml"),
        market_data_mode="sample",
    )
    monkeypatch.setattr(shared_config, "get_settings", lambda: settings)
    monkeypatch.setattr(
        shared_database,
        "get_session_factory",
        lambda: lambda: _CurrentRunSession(current_id, current_listing),
    )
    monkeypatch.setattr(
        decision_config,
        "load_decision_config",
        lambda _path: SimpleNamespace(
            decision_model_version="decision-v1",
            configuration_version="config-v1",
        ),
    )
    monkeypatch.setattr(
        decision_repository,
        "latest_decision_assessments",
        lambda *_args, **_kwargs: assessments,
    )
    monkeypatch.setattr(
        decision_ranking,
        "decision_sort_key",
        lambda assessment, _config: (-assessment.value_score,),
    )

    snapshot = daily_update_pipeline.generate_opportunity_snapshot(
        crawl_run_id=str(crawl_run_id), current_run_only=True
    )

    assert snapshot["universe"] == "current_crawl"
    assert snapshot["evaluated_count"] == 1
    assert [item["listing_id"] for item in snapshot["items"]] == [current_id]


def test_activity_summary_preserves_relisting_and_price_cut() -> None:
    occurred_at = datetime(2026, 9, 4, tzinfo=UTC)
    events = [
        SimpleNamespace(
            event_type="price_cut",
            occurred_at=occurred_at,
            previous_value="3000000",
            current_value="2850000",
        ),
        SimpleNamespace(
            event_type="relisted",
            occurred_at=occurred_at,
            previous_value="inactive",
            current_value="active",
        ),
    ]

    activity = daily_update_pipeline._summarize_activity(events)

    assert activity["activity_signal"] == "RELISTED"
    assert activity["activity_signals"] == ["RELISTED", "PRICE_CUT"]
    assert activity["previous_asking_price_wan"] == Decimal("300.00")
    assert activity["current_asking_price_wan"] == Decimal("285.00")
    assert activity["price_change_pct"] == Decimal("-5.00")
    assert daily_update_pipeline._is_fresh_activity(activity) is True


def test_opportunity_snapshot_has_separate_fresh_list_without_changing_rank(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    price_cut_id = uuid.uuid4()
    unchanged_id = uuid.uuid4()
    listings = [
        _listing(price_cut_id, status="active"),
        _listing(unchanged_id, status="active"),
    ]
    assessments = [
        _assessment(unchanged_id, eligibility="ELIGIBLE", score=90),
        _assessment(price_cut_id, eligibility="ELIGIBLE", score=80),
    ]
    settings = SimpleNamespace(
        daily_top_opportunities_limit=10,
        decision_config_path=Path("decision.yaml"),
        market_data_mode="sample",
    )
    monkeypatch.setattr(shared_config, "get_settings", lambda: settings)
    monkeypatch.setattr(
        shared_database,
        "get_session_factory",
        lambda: lambda: _ListingSession(listings),
    )
    monkeypatch.setattr(
        decision_config,
        "load_decision_config",
        lambda _path: SimpleNamespace(
            decision_model_version="decision-v1",
            configuration_version="config-v1",
        ),
    )
    monkeypatch.setattr(
        decision_repository,
        "latest_decision_assessments",
        lambda *_args, **_kwargs: assessments,
    )
    monkeypatch.setattr(
        decision_ranking,
        "decision_sort_key",
        lambda assessment, _config: (-assessment.value_score,),
    )
    monkeypatch.setattr(
        daily_update_pipeline,
        "_load_run_activity",
        lambda *_args, **_kwargs: {
            price_cut_id: {
                "activity_signal": "PRICE_CUT",
                "activity_signals": ["PRICE_CUT"],
                "activity_occurred_at": datetime(2026, 9, 4, tzinfo=UTC),
            }
        },
    )

    snapshot = daily_update_pipeline.generate_opportunity_snapshot(crawl_run_id=str(uuid.uuid4()))

    assert [item["listing_id"] for item in snapshot["items"]] == [
        unchanged_id,
        price_cut_id,
    ]
    assert snapshot["fresh_eligible_count"] == 1
    assert [item["listing_id"] for item in snapshot["fresh_items"]] == [price_cut_id]
    assert snapshot["fresh_items"][0]["rank"] == 2


def test_locked_pipeline_skips_when_another_run_holds_lock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = SimpleNamespace(
        daily_report_directory=tmp_path,
        daily_run_lock_ttl_seconds=3600,
        market_data_mode="sample",
        redis_url="redis://test",
    )
    lock = _FakeLock(acquired=False)
    client = SimpleNamespace(lock=lambda *_args, **_kwargs: lock)
    monkeypatch.setattr(shared_config, "get_settings", lambda: settings)
    monkeypatch.setattr(redis.Redis, "from_url", lambda _url: client)

    result = asyncio.run(daily_update_pipeline.run_locked_pipeline())

    assert result["status"] == "skipped_already_running"
    assert not (tmp_path / "latest.json").exists()
    assert len(list(tmp_path.glob("*.json"))) == 1
    assert lock.released is False


def test_main_returns_tempfail_for_retryable_pipeline_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def run(coroutine: Any) -> object:
        coroutine.close()
        raise daily_update_pipeline.DailyPipelineFailure(
            "temporary",
            stage="collection",
            retryable=True,
        )

    monkeypatch.setattr(daily_update_pipeline.asyncio, "run", run)

    assert daily_update_pipeline.main() == daily_update_pipeline.RETRYABLE_PIPELINE_EXIT_CODE


def test_main_returns_failure_for_non_retryable_pipeline_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def run(coroutine: Any) -> object:
        coroutine.close()
        raise daily_update_pipeline.DailyPipelineFailure(
            "delivery failed",
            stage="delivery",
            retryable=False,
        )

    monkeypatch.setattr(daily_update_pipeline.asyncio, "run", run)

    assert daily_update_pipeline.main() == 1


@pytest.mark.asyncio
async def test_wait_for_job_rejects_completed_batch_with_item_failures() -> None:
    job = _FakeJob(result={"evaluated": 11, "failed": 1})

    result = await daily_update_pipeline._wait_for_job(
        job,
        label="测试任务",
        timeout_seconds=10,
    )

    assert result["success"] is False
    assert result["error"] == "Job completed with item failures"
    assert result["job_id"] == "job-1"


@pytest.mark.asyncio
async def test_wait_for_job_returns_finished_result() -> None:
    payload = {
        "evaluated": 12,
        "failed": 0,
        "skipped_insufficient_data": 1,
    }
    job = _FakeJob(result=payload)

    result = await daily_update_pipeline._wait_for_job(
        job,
        label="测试任务",
        timeout_seconds=10,
    )

    assert result == {
        "success": True,
        "job_id": "job-1",
        "result": payload,
    }


class _ScalarResult:
    def __init__(self, value: int) -> None:
        self.value = value

    def scalar(self) -> int:
        return self.value


class _FakeSession:
    def __init__(self) -> None:
        self.statements: list[str] = []
        self.parameters: list[dict[str, object] | None] = []
        self.commits = 0

    def __enter__(self) -> _FakeSession:
        return self

    def __exit__(self, *_args: Any) -> None:
        return None

    def execute(
        self, statement: object, parameters: dict[str, object] | None = None
    ) -> _ScalarResult:
        self.statements.append(str(statement))
        self.parameters.append(parameters)
        return _ScalarResult(len(self.statements))

    def commit(self) -> None:
        self.commits += 1


class _FakeJob:
    id = "job-1"
    is_finished = True
    is_failed = False
    exc_info = None

    def __init__(self, *, result: dict[str, int]) -> None:
        self.result = result

    def refresh(self) -> None:
        return None


class _FakeLock:
    def __init__(self, *, acquired: bool) -> None:
        self.acquired = acquired
        self.released = False

    def acquire(self, *, blocking: bool) -> bool:
        assert blocking is False
        return self.acquired

    def release(self) -> None:
        self.released = True


class _ListingSession:
    def __init__(self, listings: list[SimpleNamespace]) -> None:
        self.listings = listings

    def __enter__(self) -> _ListingSession:
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def scalars(self, _statement: object) -> list[SimpleNamespace]:
        return self.listings


class _CurrentRunSession:
    def __init__(self, current_id: uuid.UUID, listing: SimpleNamespace) -> None:
        self.current_id = current_id
        self.listing = listing
        self.calls = 0

    def __enter__(self) -> _CurrentRunSession:
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def scalars(self, _statement: object) -> list[uuid.UUID] | list[SimpleNamespace]:
        self.calls += 1
        if self.calls == 1:
            return [self.current_id]
        if self.calls == 2:
            return [self.listing]
        return _ScalarList()


class _ScalarList(list[SimpleNamespace]):
    def all(self) -> list[SimpleNamespace]:
        return self


def _listing(listing_id: uuid.UUID, *, status: str) -> SimpleNamespace:
    return SimpleNamespace(
        id=listing_id,
        status=status,
        source="public-research",
        source_listing_id=str(listing_id),
        source_url=f"https://example.invalid/{listing_id}",
        district="普陀",
        submarket="真如",
        community="测试小区",
        area_sqm=Decimal("60.00"),
        bedrooms=2,
    )


def _assessment(listing_id: uuid.UUID, *, eligibility: str, score: int) -> SimpleNamespace:
    return SimpleNamespace(
        listing_id=listing_id,
        eligibility_status=eligibility,
        opportunity_classification=(
            "GOOD_BUT_EXPENSIVE" if eligibility == "ELIGIBLE" else "INSUFFICIENT_DATA"
        ),
        workflow_state="WATCH" if eligibility == "ELIGIBLE" else "PASS",
        current_ask=Decimal("2980000"),
        fair_value=Decimal("3100000"),
        fair_value_low=Decimal("3000000"),
        fair_value_high=Decimal("3200000"),
        value_score=Decimal(score),
        future_score=Decimal("80"),
        liquidity_score=Decimal("85"),
        obsolescence_risk=Decimal("25"),
        structural_alpha=Decimal("8"),
        valuation_confidence="medium",
        future_confidence="medium",
        positive_reasons=["流动性较强"],
        negative_reasons=["估值置信度有限"],
        warnings=[],
        decision_version="decision-v1/config-v1",
        data_timestamp=datetime(2026, 9, 4, tzinfo=UTC),
        generated_at=datetime(2026, 9, 4, tzinfo=UTC),
    )

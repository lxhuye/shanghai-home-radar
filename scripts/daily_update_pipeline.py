#!/usr/bin/env python3
"""每日数据更新完整流程

每天凌晨自动执行:
1. 采集新数据（全量拉取）
2. 更新市场基线 (P3)
3. 批量估值 (P4)
4. 未来评估 (P5)
5. 决策编排 (P5.5)
6. 生成每日报告
"""

import asyncio
import json
import logging
import sys
import uuid
from collections import Counter
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import httpx

# 确保可以导入项目模块
sys.path.insert(0, str(Path(__file__).parent.parent))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)
RETRYABLE_PIPELINE_EXIT_CODE = 75


class DailyPipelineFailure(RuntimeError):
    """A persisted pipeline failure with scheduler retry semantics."""

    def __init__(self, message: str, *, stage: str, retryable: bool) -> None:
        super().__init__(message)
        self.stage = stage
        self.retryable = retryable


StepResult = dict[str, Any]

_ACTIVITY_PRECEDENCE = ("relisted", "new", "price_cut", "price_increase")
_FRESH_ACTIVITY_SIGNALS = frozenset({"relisted", "new", "price_cut"})


def _enqueue_job(
    queue_name: str,
    redis_url: str,
    job_function: Callable[..., object],
    *args: object,
    job_timeout: str,
) -> Any:
    from redis import Redis
    from rq import Queue

    queue = Queue(queue_name, connection=Redis.from_url(redis_url))
    return queue.enqueue(job_function, *args, job_timeout=job_timeout)


async def _wait_for_job(
    job: Any,
    *,
    label: str,
    timeout_seconds: int,
    poll_seconds: int = 10,
) -> StepResult:
    elapsed = 0
    while elapsed < timeout_seconds:
        job.refresh()
        if job.is_finished:
            result = job.result
            failed = result.get("failed", 0) if isinstance(result, dict) else 0
            if isinstance(failed, int) and failed > 0:
                logger.error("%s completed with %s item failures", label, failed)
                return {
                    "success": False,
                    "error": "Job completed with item failures",
                    "job_id": job.id,
                    "result": result,
                }
            skipped = result.get("skipped_insufficient_data", 0) if isinstance(result, dict) else 0
            if isinstance(skipped, int) and skipped > 0:
                logger.warning("%s因数据不足跳过 %s 套房源", label, skipped)
            logger.info("%s完成", label)
            return {"success": True, "job_id": job.id, "result": result}
        if job.is_failed:
            logger.error("%s任务失败: %s", label, job.exc_info)
            return {"success": False, "error": "Job failed", "job_id": job.id}
        await asyncio.sleep(poll_seconds)
        elapsed += poll_seconds

    logger.warning("%s任务超时", label)
    return {"success": False, "error": "Job timeout", "job_id": job.id}


async def run_collection() -> StepResult:
    """执行数据采集"""
    logger.info("Step 1/5: 采集房源数据")

    from home_radar_collector.registry import build_configured_adapter
    from home_radar_collector.service import CollectorService
    from home_radar_models.enums import CrawlCompleteness, CrawlRunStatus, DataMode
    from home_radar_shared.config import get_settings
    from home_radar_shared.database import get_session_factory

    try:
        settings = get_settings()
        adapter = build_configured_adapter(settings)
        with get_session_factory()() as session:
            service = CollectorService(
                session,
                final_after_missing_runs=settings.disappearance_final_after_runs,
                data_mode=DataMode(settings.market_data_mode),
                min_complete_items=settings.collector_min_complete_items,
                min_complete_count_ratio=settings.collector_min_complete_count_ratio,
            )
            result = await service.collect(adapter)

        mode = DataMode(settings.market_data_mode)
        accepted_partial = (
            mode is not DataMode.LIVE
            and result.completeness == CrawlCompleteness.PARTIAL.value
            and result.normalized_item_count > 0
        )
        success = result.status == CrawlRunStatus.SUCCEEDED.value and (
            result.completeness == CrawlCompleteness.COMPLETE.value or accepted_partial
        )
        if accepted_partial:
            logger.warning(
                "非 LIVE 数据以 partial 完整性继续，仅用于样例或校准，不执行缺失房源协调"
            )
        logger.info(
            "采集完成: status=%s, completeness=%s, items=%s",
            result.status,
            result.completeness,
            result.normalized_item_count,
        )
        return {
            "success": success,
            "run_id": str(result.run_id),
            "source": result.source,
            "scope_key": result.scope_key,
            "status": result.status,
            "completeness": result.completeness,
            "normalized_count": result.normalized_item_count,
            "accepted_partial": accepted_partial,
            "reconciliation_performed": result.reconciliation_performed,
            "listings_missing_candidate": result.listings_missing_candidate,
            "listings_inactivated": result.listings_inactivated,
            "coverage_guard_triggered": result.coverage_guard_triggered,
            "coverage_guard_reason": result.coverage_guard_reason,
            "previous_complete_count": result.previous_complete_count,
            "current_count_ratio": result.current_count_ratio,
        }
    except Exception as e:
        logger.error(f"采集失败: {e}", exc_info=True)
        return {"success": False, "error": str(e)}


async def run_market_baseline() -> StepResult:
    """更新市场基线 (P3)"""
    logger.info("Step 2/5: 更新市场基线")

    from home_radar_market.jobs import materialize_market_baselines_job
    from home_radar_shared.config import get_settings

    try:
        settings = get_settings()
        job = _enqueue_job(
            settings.market_queue,
            settings.redis_url,
            materialize_market_baselines_job,
            None,
            job_timeout="30m",
        )
        logger.info("Market baseline job enqueued: %s", job.id)
        return await _wait_for_job(
            job,
            label="市场基线更新",
            timeout_seconds=300,
            poll_seconds=5,
        )
    except Exception as e:
        logger.error(f"市场基线更新失败: {e}", exc_info=True)
        return {"success": False, "error": str(e)}


async def run_valuation() -> StepResult:
    """批量估值 (P4)"""
    logger.info("Step 3/5: 批量估值")

    from home_radar_shared.config import get_settings
    from home_radar_valuation.jobs import materialize_valuation_job

    try:
        settings = get_settings()
        as_of = datetime.now(UTC).isoformat()
        job = _enqueue_job(
            settings.valuation_queue,
            settings.redis_url,
            materialize_valuation_job,
            settings.market_data_mode,
            as_of,
            None,
            job_timeout="2h",
        )
        logger.info("Valuation job enqueued: %s", job.id)
        return await _wait_for_job(
            job,
            label="批量估值",
            timeout_seconds=1800,
        )
    except Exception as e:
        logger.error(f"批量估值失败: {e}", exc_info=True)
        return {"success": False, "error": str(e)}


async def run_future_assessment() -> StepResult:
    """未来评估 (P5)"""
    logger.info("Step 4/5: 未来评估")

    from home_radar_forecasting.jobs import materialize_future_job
    from home_radar_shared.config import get_settings

    try:
        settings = get_settings()
        as_of = datetime.now(UTC).isoformat()
        job = _enqueue_job(
            settings.future_queue,
            settings.redis_url,
            materialize_future_job,
            settings.market_data_mode,
            as_of,
            None,
            job_timeout="2h",
        )
        logger.info("Future assessment job enqueued: %s", job.id)
        return await _wait_for_job(
            job,
            label="未来评估",
            timeout_seconds=900,
        )
    except Exception as e:
        logger.error(f"未来评估失败: {e}", exc_info=True)
        return {"success": False, "error": str(e)}


async def run_decision_orchestration() -> StepResult:
    """决策编排 (P5.5)"""
    logger.info("Step 5/5: 决策编排")

    from home_radar_decision.jobs import materialize_decision_job
    from home_radar_shared.config import get_settings

    try:
        settings = get_settings()
        as_of = datetime.now(UTC).isoformat()
        job = _enqueue_job(
            settings.decision_queue,
            settings.redis_url,
            materialize_decision_job,
            settings.market_data_mode,
            as_of,
            None,
            job_timeout="2h",
        )
        logger.info("Decision orchestration job enqueued: %s", job.id)
        return await _wait_for_job(
            job,
            label="决策编排",
            timeout_seconds=600,
        )
    except Exception as e:
        logger.error(f"决策编排失败: {e}", exc_info=True)
        return {"success": False, "error": str(e)}


def generate_daily_report(crawl_run_id: str) -> StepResult:
    """Generate counts for this exact collection run, not a rolling time window."""
    logger.info("生成每日报告")

    from home_radar_shared.config import get_settings
    from home_radar_shared.database import get_session_factory
    from sqlalchemy import text

    SessionLocal = get_session_factory()
    settings = get_settings()
    report = {}
    run_id = uuid.UUID(crawl_run_id)

    try:
        with SessionLocal() as session:
            new_listings = session.execute(
                text(
                    "SELECT COUNT(*) FROM listing_event "
                    "WHERE crawl_run_id = :crawl_run_id AND event_type = 'new'"
                ),
                {"crawl_run_id": run_id},
            ).scalar()
            price_changes = session.execute(
                text(
                    "SELECT COUNT(*) FROM listing_event "
                    "WHERE crawl_run_id = :crawl_run_id "
                    "AND event_type IN ('price_cut', 'price_increase')"
                ),
                {"crawl_run_id": run_id},
            ).scalar()
            new_inactive = session.execute(
                text(
                    "SELECT COUNT(*) FROM listing_event "
                    "WHERE crawl_run_id = :crawl_run_id AND event_type = 'inactivated'"
                ),
                {"crawl_run_id": run_id},
            ).scalar()
            relisted = session.execute(
                text(
                    "SELECT COUNT(*) FROM listing_event "
                    "WHERE crawl_run_id = :crawl_run_id AND event_type = 'relisted'"
                ),
                {"crawl_run_id": run_id},
            ).scalar()

            report = {
                "date": datetime.now(ZoneInfo(settings.daily_timezone)).date().isoformat(),
                "crawl_run_id": crawl_run_id,
                "new_listings": new_listings,
                "price_changes": price_changes,
                "new_inactive": new_inactive,
                "relisted": relisted,
            }
            return report

    except Exception as e:
        logger.error(f"生成报告失败: {e}", exc_info=True)
        return {"error": str(e)}


def generate_opportunity_snapshot(
    *, crawl_run_id: str | None = None, current_run_only: bool = False
) -> StepResult:
    """Freeze the current ranked, active opportunity universe into the daily artifact."""
    from home_radar_decision.config import load_decision_config
    from home_radar_decision.ranking import decision_sort_key
    from home_radar_decision.repository import latest_decision_assessments
    from home_radar_models.collection import RawSourceRecord
    from home_radar_models.enums import ListingStatus, NormalizationStatus
    from home_radar_models.listing import Listing, ListingEvent
    from home_radar_shared.config import get_settings
    from home_radar_shared.database import get_session_factory
    from sqlalchemy import select

    settings = get_settings()
    config = load_decision_config(settings.decision_config_path)
    with get_session_factory()() as session:
        latest = latest_decision_assessments(
            session,
            data_mode=settings.market_data_mode,
            decision_model_version=config.decision_model_version,
            configuration_version=config.configuration_version,
        )
        listing_ids = [assessment.listing_id for assessment in latest]
        if current_run_only:
            if crawl_run_id is None:
                raise ValueError("crawl_run_id is required for current-run-only snapshots")
            observed_ids = set(
                listing_id
                for listing_id in session.scalars(
                    select(RawSourceRecord.listing_id).where(
                        RawSourceRecord.crawl_run_id == uuid.UUID(crawl_run_id),
                        RawSourceRecord.normalization_status == NormalizationStatus.SUCCESS.value,
                        RawSourceRecord.listing_id.is_not(None),
                    )
                )
                if listing_id is not None
            )
            listing_ids = [listing_id for listing_id in listing_ids if listing_id in observed_ids]
        listings = (
            {
                listing.id: listing
                for listing in session.scalars(select(Listing).where(Listing.id.in_(listing_ids)))
            }
            if listing_ids
            else {}
        )
        active = [
            assessment
            for assessment in latest
            if (listing := listings.get(assessment.listing_id)) is not None
            and listing.status == ListingStatus.ACTIVE.value
        ]
        eligible = sorted(
            (assessment for assessment in active if assessment.eligibility_status == "ELIGIBLE"),
            key=lambda assessment: decision_sort_key(assessment, config),
        )
        activity_by_listing = _load_run_activity(
            session,
            event_model=ListingEvent,
            crawl_run_id=crawl_run_id,
            listing_ids=[assessment.listing_id for assessment in eligible],
        )
        selected = eligible[: settings.daily_top_opportunities_limit]
        fresh = [
            assessment
            for assessment in eligible
            if _is_fresh_activity(activity_by_listing.get(assessment.listing_id, {}))
        ]
        fresh_selected = fresh[: settings.daily_top_opportunities_limit]
        overall_rank = {assessment.listing_id: rank for rank, assessment in enumerate(eligible, 1)}
        activity_counts = Counter(
            str(
                activity_by_listing.get(assessment.listing_id, {}).get(
                    "activity_signal", "UNCHANGED"
                )
            )
            for assessment in eligible
        )
        classifications = Counter(item.opportunity_classification for item in active)
        workflows = Counter(item.workflow_state for item in active)
        return {
            "data_mode": settings.market_data_mode,
            "universe": "current_crawl" if current_run_only else "active_inventory",
            "crawl_run_id": crawl_run_id,
            "generated_at": datetime.now(UTC),
            "evaluated_count": len(active),
            "eligible_count": len(eligible),
            "fresh_eligible_count": len(fresh),
            "activity_counts": dict(sorted(activity_counts.items())),
            "classification_counts": dict(sorted(classifications.items())),
            "workflow_counts": dict(sorted(workflows.items())),
            "items": [
                _opportunity_payload(
                    rank,
                    item,
                    listings[item.listing_id],
                    len(eligible),
                    activity_by_listing.get(item.listing_id),
                )
                for rank, item in enumerate(selected, start=1)
            ],
            "fresh_items": [
                _opportunity_payload(
                    overall_rank[item.listing_id],
                    item,
                    listings[item.listing_id],
                    len(eligible),
                    activity_by_listing.get(item.listing_id),
                )
                for item in fresh_selected
            ],
        }


def assess_opportunity_output(
    snapshot: Mapping[str, Any],
    decision_step: Mapping[str, Any],
    *,
    data_mode: str,
    minimum_decision_coverage_ratio: float,
) -> StepResult:
    """Detect data failures without treating a legitimate empty opportunity set as failure."""
    evaluated_count = _nonnegative_int(snapshot.get("evaluated_count"))
    classifications = snapshot.get("classification_counts")
    classification_counts = classifications if isinstance(classifications, Mapping) else {}
    classified_count = sum(_nonnegative_int(value) for value in classification_counts.values())
    insufficient_count = _nonnegative_int(classification_counts.get("INSUFFICIENT_DATA"))

    materialization = decision_step.get("result")
    decision_result = materialization if isinstance(materialization, Mapping) else {}
    current_evaluated = _nonnegative_int(decision_result.get("evaluated"))
    current_skipped = _nonnegative_int(decision_result.get("skipped_insufficient_data"))
    current_total = current_evaluated + current_skipped
    current_coverage_ratio = current_evaluated / current_total if current_total else None

    reasons: list[str] = []
    if evaluated_count == 0:
        reasons.append("no_active_decision_assessments")
    if classified_count != evaluated_count:
        reasons.append("classification_accounting_mismatch")
    if evaluated_count > 0 and insufficient_count == evaluated_count:
        reasons.append("all_active_decisions_insufficient_data")
    if current_evaluated == 0 and current_skipped > 0:
        reasons.append("current_decision_batch_all_skipped")
    elif (
        current_coverage_ratio is not None
        and current_coverage_ratio < minimum_decision_coverage_ratio
    ):
        reasons.append("current_decision_coverage_below_minimum")

    if not reasons:
        status = "healthy"
    elif data_mode == "live":
        status = "blocked"
    else:
        status = "degraded"
    return {
        "status": status,
        "reasons": reasons,
        "data_mode": data_mode,
        "evaluated_count": evaluated_count,
        "classified_count": classified_count,
        "insufficient_count": insufficient_count,
        "current_batch_evaluated": current_evaluated,
        "current_batch_skipped_insufficient_data": current_skipped,
        "current_batch_total": current_total,
        "current_batch_coverage_ratio": (
            round(current_coverage_ratio, 6) if current_coverage_ratio is not None else None
        ),
        "minimum_decision_coverage_ratio": minimum_decision_coverage_ratio,
    }


def _opportunity_payload(
    rank: int,
    assessment: Any,
    listing: Any,
    eligible_count: int,
    activity: Mapping[str, Any] | None = None,
) -> StepResult:
    payload = {
        "rank": rank,
        "eligible_count": eligible_count,
        "listing_id": assessment.listing_id,
        "source": listing.source,
        "source_listing_id": listing.source_listing_id,
        "source_url": listing.source_url,
        "district": listing.district,
        "submarket": listing.submarket,
        "community": listing.community,
        "area_sqm": listing.area_sqm,
        "bedrooms": listing.bedrooms,
        "asking_price_wan": _yuan_to_wan(assessment.current_ask),
        "fair_value_wan": _yuan_to_wan(assessment.fair_value),
        "fair_value_low_wan": _yuan_to_wan(assessment.fair_value_low),
        "fair_value_high_wan": _yuan_to_wan(assessment.fair_value_high),
        "value_score": assessment.value_score,
        "future_score": assessment.future_score,
        "liquidity_score": assessment.liquidity_score,
        "obsolescence_risk": assessment.obsolescence_risk,
        "structural_alpha": assessment.structural_alpha,
        "valuation_confidence": assessment.valuation_confidence,
        "future_confidence": assessment.future_confidence,
        "opportunity_classification": assessment.opportunity_classification,
        "workflow_state": assessment.workflow_state,
        "positive_reasons": assessment.positive_reasons,
        "negative_reasons": assessment.negative_reasons,
        "warnings": assessment.warnings,
        "decision_version": assessment.decision_version,
        "data_timestamp": assessment.data_timestamp,
        "generated_at": assessment.generated_at,
    }
    payload.update(activity or _unchanged_activity())
    return payload


def _load_run_activity(
    session: Any,
    *,
    event_model: Any,
    crawl_run_id: str | None,
    listing_ids: list[uuid.UUID],
) -> dict[uuid.UUID, StepResult]:
    """Load and summarize listing events emitted by this collection run."""
    from sqlalchemy import select

    if crawl_run_id is None or not listing_ids:
        return {}
    events = session.scalars(
        select(event_model).where(
            event_model.crawl_run_id == uuid.UUID(crawl_run_id),
            event_model.listing_id.in_(listing_ids),
        )
    ).all()
    grouped: dict[uuid.UUID, list[Any]] = {}
    for event in events:
        grouped.setdefault(event.listing_id, []).append(event)
    return {listing_id: _summarize_activity(values) for listing_id, values in grouped.items()}


def _summarize_activity(events: list[Any]) -> StepResult:
    """Preserve every current-run signal while selecting one stable display label."""
    by_type = {str(event.event_type): event for event in events}
    signals = [signal for signal in _ACTIVITY_PRECEDENCE if signal in by_type]
    if not signals:
        return _unchanged_activity()

    primary = signals[0]
    occurred_at = max(event.occurred_at for event in events if str(event.event_type) in signals)
    result: StepResult = {
        "activity_signal": primary.upper(),
        "activity_signals": [signal.upper() for signal in signals],
        "activity_occurred_at": occurred_at,
    }
    price_event = by_type.get("price_cut") or by_type.get("price_increase")
    if price_event is not None:
        previous = _optional_decimal(price_event.previous_value)
        current = _optional_decimal(price_event.current_value)
        if previous is not None:
            result["previous_asking_price_wan"] = _yuan_to_wan(previous)
        if current is not None:
            result["current_asking_price_wan"] = _yuan_to_wan(current)
        if previous is not None and current is not None and previous != 0:
            result["price_change_pct"] = (
                (current - previous) / previous * Decimal("100")
            ).quantize(Decimal("0.01"))
    return result


def _unchanged_activity() -> StepResult:
    return {
        "activity_signal": "UNCHANGED",
        "activity_signals": [],
        "activity_occurred_at": None,
    }


def _is_fresh_activity(activity: Mapping[str, Any]) -> bool:
    values = activity.get("activity_signals")
    if not isinstance(values, list):
        return False
    return any(str(value).lower() in _FRESH_ACTIVITY_SIGNALS for value in values)


def _optional_decimal(value: object) -> Decimal | None:
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except Exception:
        return None


def _nonnegative_int(value: object) -> int:
    if isinstance(value, bool):
        return 0
    if isinstance(value, int):
        return max(0, value)
    if not isinstance(value, str):
        return 0
    try:
        parsed = int(value)
    except ValueError:
        return 0
    return max(0, parsed)


def _yuan_to_wan(value: Decimal) -> Decimal:
    return (value / Decimal("10000")).quantize(Decimal("0.01"))


def persist_run_artifact(results: dict[str, Any], *, update_latest: bool = True) -> Path:
    """Atomically persist one immutable run file plus the latest pointer."""
    from home_radar_shared.config import get_settings

    directory = get_settings().daily_report_directory
    directory.mkdir(parents=True, exist_ok=True)
    run_path = directory / f"{results['run_id']}.json"
    if run_path.exists():
        raise FileExistsError(f"daily run artifact already exists: {run_path}")
    results["artifact_path"] = str(run_path)
    payload = json.dumps(results, ensure_ascii=False, indent=2, default=_json_default) + "\n"
    _atomic_write(run_path, payload)
    if update_latest:
        _atomic_write(directory / "latest.json", payload)
    return run_path


def _atomic_write(path: Path, payload: str) -> None:
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    temporary.write_text(payload, encoding="utf-8")
    temporary.replace(path)


def _json_default(value: object) -> str:
    if isinstance(value, (datetime, Decimal, uuid.UUID)):
        return str(value)
    raise TypeError(f"cannot serialize {type(value).__name__}")


def send_alert(message: str) -> None:
    """Send a bounded generic webhook alert when one is explicitly configured."""
    from home_radar_shared.config import get_settings

    settings = get_settings()
    logger.warning("告警: %s", message)
    if not settings.daily_alert_webhook_url:
        return
    headers = {"Content-Type": "application/json"}
    if settings.daily_alert_webhook_bearer_token is not None:
        token = settings.daily_alert_webhook_bearer_token.get_secret_value()
        if token:
            headers["Authorization"] = f"Bearer {token}"
    try:
        response = httpx.post(
            settings.daily_alert_webhook_url,
            json={"event": "daily_pipeline_failed", "message": message},
            headers=headers,
            timeout=10,
        )
        response.raise_for_status()
    except httpx.HTTPError as exc:
        logger.error("告警 webhook 发送失败: %s", exc)


async def deliver_daily_result(results: dict[str, Any]) -> StepResult:
    """Build the readable digest and deliver it when a webhook is configured."""
    from home_radar_notification import build_daily_digest, deliver_daily_digest
    from home_radar_shared.config import get_settings

    settings = get_settings()
    digest = build_daily_digest(results, advisory_status=settings.daily_advisory_status)
    results["digest"] = digest
    if not settings.daily_result_webhook_url:
        return {"status": "disabled"}
    token = (
        settings.daily_result_webhook_bearer_token.get_secret_value()
        if settings.daily_result_webhook_bearer_token is not None
        else None
    )
    try:
        return await deliver_daily_digest(
            digest,
            webhook_url=settings.daily_result_webhook_url,
            bearer_token=token,
            run_id=str(results["run_id"]),
            timeout_seconds=settings.daily_result_webhook_timeout_seconds,
            max_attempts=settings.daily_result_webhook_max_attempts,
        )
    except (ValueError, httpx.HTTPError) as exc:
        logger.error("每日榜单 webhook 投递失败: %s", exc)
        return {"status": "failed", "error": type(exc).__name__}


async def daily_pipeline() -> dict[str, Any]:
    """每日更新主流程"""
    start_time = datetime.now(UTC)
    logger.info(f"开始每日更新流程: {start_time}")

    step_results: dict[str, StepResult] = {}
    results: dict[str, Any] = {
        "run_id": str(uuid.uuid4()),
        "start_time": start_time.isoformat(),
        "steps": step_results,
    }
    current_stage = "collection"

    try:
        # Step 1: 采集新数据
        step_results["collection"] = await run_collection()
        if not step_results["collection"]["success"]:
            raise RuntimeError("数据采集失败")

        # Step 2: 更新市场基线
        current_stage = "market_baseline"
        step_results["market_baseline"] = await run_market_baseline()
        if not step_results["market_baseline"]["success"]:
            raise RuntimeError("市场基线更新失败")

        # Step 3: 批量估值
        current_stage = "valuation"
        step_results["valuation"] = await run_valuation()
        if not step_results["valuation"]["success"]:
            raise RuntimeError("批量估值失败")

        # Step 4: 未来评估
        current_stage = "future_assessment"
        step_results["future_assessment"] = await run_future_assessment()
        if not step_results["future_assessment"]["success"]:
            raise RuntimeError("未来评估失败")

        # Step 5: 决策编排
        current_stage = "decision"
        step_results["decision"] = await run_decision_orchestration()
        if not step_results["decision"]["success"]:
            raise RuntimeError("决策编排失败")

        # Step 6: 生成每日报告
        current_stage = "daily_report"
        collection = step_results["collection"]
        crawl_run_id = str(collection["run_id"])
        results["daily_report"] = generate_daily_report(crawl_run_id)
        if "error" in results["daily_report"]:
            raise RuntimeError("每日报告生成失败")
        results["opportunity_snapshot"] = generate_opportunity_snapshot(
            crawl_run_id=crawl_run_id,
            current_run_only=bool(collection.get("accepted_partial", False)),
        )
        from home_radar_shared.config import get_settings

        settings = get_settings()
        current_stage = "opportunity_health"
        results["opportunity_health"] = assess_opportunity_output(
            results["opportunity_snapshot"],
            step_results["decision"],
            data_mode=settings.market_data_mode,
            minimum_decision_coverage_ratio=settings.daily_min_decision_coverage_ratio,
        )
        if results["opportunity_health"]["status"] == "blocked":
            reasons = ",".join(results["opportunity_health"]["reasons"])
            raise RuntimeError(f"机会输出健康检查失败: {reasons}")
        results["daily_report"]["top_opportunities"] = results["opportunity_snapshot"][
            "eligible_count"
        ]
        logger.info("每日报告: %s", results["daily_report"])

        end_time = datetime.now(UTC)
        duration = (end_time - start_time).total_seconds()
        results["end_time"] = end_time.isoformat()
        results["duration_seconds"] = duration
        results["status"] = "success"
        current_stage = "delivery"
        results["delivery"] = await deliver_daily_result(results)
        if results["delivery"]["status"] == "failed":
            raise RuntimeError("每日榜单投递失败")
        current_stage = "artifact_persistence"
        persist_run_artifact(results)

        logger.info(f"每日更新完成，耗时 {duration:.0f} 秒")
        logger.info(
            "每日结果: run_id=%s evaluated=%s eligible=%s artifact=%s",
            results["run_id"],
            results["opportunity_snapshot"]["evaluated_count"],
            results["opportunity_snapshot"]["eligible_count"],
            results["artifact_path"],
        )
        return results

    except Exception as e:
        end_time = datetime.now(UTC)
        duration = (end_time - start_time).total_seconds()
        results["end_time"] = end_time.isoformat()
        results["duration_seconds"] = duration
        results["status"] = "failed"
        results["error"] = str(e)
        retryable = current_stage not in {"delivery", "artifact_persistence"}
        results["failure"] = {
            "stage": current_stage,
            "retryable": retryable,
        }
        try:
            persist_run_artifact(results)
        except Exception as artifact_error:
            results["artifact_error"] = str(artifact_error)
            logger.error("失败运行记录无法持久化: %s", artifact_error, exc_info=True)

        logger.error(f"每日更新失败: {e}", exc_info=True)
        send_alert(f"每日更新失败: {e}")
        raise DailyPipelineFailure(
            str(e),
            stage=current_stage,
            retryable=retryable,
        ) from e


async def run_locked_pipeline() -> dict[str, Any]:
    """Run at most one pipeline per data mode across cron, scheduler, and manual calls."""
    from home_radar_shared.config import get_settings
    from redis import Redis
    from redis.exceptions import LockError

    settings = get_settings()
    client = Redis.from_url(settings.redis_url)
    lock = client.lock(
        f"home-radar:daily-pipeline:{settings.market_data_mode}",
        timeout=settings.daily_run_lock_ttl_seconds,
        blocking_timeout=0,
    )
    if not lock.acquire(blocking=False):
        now = datetime.now(UTC)
        result: dict[str, Any] = {
            "run_id": str(uuid.uuid4()),
            "start_time": now.isoformat(),
            "end_time": now.isoformat(),
            "duration_seconds": 0,
            "status": "skipped_already_running",
            "steps": {},
        }
        persist_run_artifact(result, update_latest=False)
        logger.warning("每日流水线已有实例运行，本次跳过")
        return result
    try:
        return await daily_pipeline()
    finally:
        try:
            lock.release()
        except LockError:
            logger.error("每日流水线锁已过期或所有权丢失")


def main() -> int:
    try:
        result = asyncio.run(run_locked_pipeline())
    except DailyPipelineFailure as exc:
        return RETRYABLE_PIPELINE_EXIT_CODE if exc.retryable else 1
    except Exception:
        return RETRYABLE_PIPELINE_EXIT_CODE
    return 0 if result["status"] in {"success", "skipped_already_running"} else 1


if __name__ == "__main__":
    raise SystemExit(main())

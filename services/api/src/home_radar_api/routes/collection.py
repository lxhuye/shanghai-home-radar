from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from home_radar_collector.jobs import collect_configured_source
from home_radar_collector.registry import build_configured_adapter
from home_radar_models.collection import CrawlRun
from home_radar_models.enums import CrawlCompleteness, CrawlRunStatus, DataMode
from home_radar_shared.config import get_settings
from redis import Redis
from rq import Queue
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from home_radar_api.dependencies import get_db, require_api_key
from home_radar_api.schemas import CrawlJobRead, CrawlRunPage, CrawlRunRead

router = APIRouter(prefix="/api/v1/collector/runs", tags=["collector"])


@router.post("", response_model=CrawlJobRead, status_code=status.HTTP_202_ACCEPTED)
def enqueue_collection(
    db: Annotated[Session, Depends(get_db)],
    _: Annotated[None, Depends(require_api_key)],
) -> CrawlJobRead:
    settings = get_settings()
    adapter = build_configured_adapter(settings)
    run_id = uuid.uuid4()
    run = CrawlRun(
        id=run_id,
        source=adapter.source_id,
        data_mode=DataMode(settings.market_data_mode).value,
        scope_key=adapter.scope_key,
        status=CrawlRunStatus.RUNNING.value,
        completeness=CrawlCompleteness.UNKNOWN.value,
        started_at=datetime.now(UTC),
        run_metadata={"queued": True},
    )
    db.add(run)
    db.commit()

    queue = Queue(settings.collector_queue, connection=Redis.from_url(settings.redis_url))
    try:
        job = queue.enqueue(
            collect_configured_source,
            str(run_id),
            job_id=f"crawl-{run_id}",
            job_timeout="30m",
            result_ttl=86_400,
        )
    except Exception as exc:
        run.status = CrawlRunStatus.FAILED.value
        run.finished_at = datetime.now(UTC)
        run.error_type = exc.__class__.__name__
        run.error_message = "failed to enqueue crawl"
        db.commit()
        raise HTTPException(status_code=503, detail="collector queue unavailable") from exc
    return CrawlJobRead(
        run_id=run_id,
        job_id=job.id,
        queue=queue.name,
        source=adapter.source_id,
        scope_key=adapter.scope_key,
    )


@router.get("", response_model=CrawlRunPage)
def list_crawl_runs(
    db: Annotated[Session, Depends(get_db)],
    source: Annotated[str | None, Query(max_length=50)] = None,
    scope_key: Annotated[str | None, Query(max_length=255)] = None,
    run_status: Annotated[str | None, Query(alias="status", max_length=24)] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> CrawlRunPage:
    filters = []
    if source:
        filters.append(CrawlRun.source == source)
    if scope_key:
        filters.append(CrawlRun.scope_key == scope_key)
    if run_status:
        filters.append(CrawlRun.status == run_status)
    total = db.scalar(select(func.count()).select_from(CrawlRun).where(*filters)) or 0
    runs = db.scalars(
        select(CrawlRun)
        .where(*filters)
        .order_by(CrawlRun.started_at.desc(), CrawlRun.id)
        .offset(offset)
        .limit(limit)
    ).all()
    return CrawlRunPage(
        items=[CrawlRunRead.model_validate(run) for run in runs],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get("/{run_id}", response_model=CrawlRunRead)
def get_crawl_run(run_id: uuid.UUID, db: Annotated[Session, Depends(get_db)]) -> CrawlRun:
    run = db.get(CrawlRun, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="crawl run not found")
    return run

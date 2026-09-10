from __future__ import annotations

from datetime import UTC, datetime, time, timedelta

from home_radar_shared.config import get_settings
from redis import Redis
from rq import Queue

from home_radar_market.windows import SHANGHAI


def schedule_next_materialization(now: datetime | None = None) -> str:
    """Ensure exactly one next daily 01:15 Asia/Shanghai materialization is registered."""
    settings = get_settings()
    current = (now or datetime.now(UTC)).astimezone(SHANGHAI)
    next_local = datetime.combine(current.date(), time(hour=1, minute=15), SHANGHAI)
    if next_local <= current:
        next_local += timedelta(days=1)
    as_of_date = next_local.date() - timedelta(days=1)
    job_id = f"market-daily-{settings.market_data_mode}-{as_of_date.isoformat()}"
    queue = Queue(settings.market_queue, connection=Redis.from_url(settings.redis_url))
    if queue.fetch_job(job_id) is None:
        from home_radar_market.jobs import materialize_market_baselines_job

        queue.enqueue_at(
            next_local,
            materialize_market_baselines_job,
            as_of_date.isoformat(),
            job_id=job_id,
            job_timeout="30m",
            result_ttl=604_800,
            failure_ttl=604_800,
        )
    return job_id

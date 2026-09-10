from __future__ import annotations

import argparse
from datetime import date

from home_radar_shared.config import get_settings
from redis import Redis
from rq import Queue

from home_radar_market.jobs import materialize_market_baselines_job
from home_radar_market.scheduler import schedule_next_materialization


def enqueue_main() -> None:
    parser = argparse.ArgumentParser(description="Enqueue a P3 market baseline materialization")
    parser.add_argument("--as-of-date", type=date.fromisoformat)
    arguments = parser.parse_args()
    settings = get_settings()
    queue = Queue(settings.market_queue, connection=Redis.from_url(settings.redis_url))
    job = queue.enqueue(
        materialize_market_baselines_job,
        arguments.as_of_date.isoformat() if arguments.as_of_date else None,
        job_timeout="30m",
        result_ttl=604_800,
        failure_ttl=604_800,
    )
    print(job.id)


def schedule_main() -> None:
    print(schedule_next_materialization())

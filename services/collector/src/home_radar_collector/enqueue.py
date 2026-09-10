from __future__ import annotations

import uuid

from home_radar_shared.config import get_settings
from redis import Redis
from rq import Queue

from home_radar_collector.jobs import collect_configured_source


def main() -> None:
    settings = get_settings()
    queue = Queue(settings.collector_queue, connection=Redis.from_url(settings.redis_url))
    run_id = uuid.uuid4()
    job = queue.enqueue(
        collect_configured_source,
        str(run_id),
        job_id=f"crawl-{run_id}",
        job_timeout="30m",
        result_ttl=86_400,
    )
    print(f"{run_id} {job.id}")


if __name__ == "__main__":
    main()

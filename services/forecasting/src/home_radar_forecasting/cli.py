from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime

from home_radar_shared.config import get_settings
from redis import Redis
from rq import Queue

from home_radar_forecasting.jobs import materialize_future_job


def enqueue_main() -> None:
    parser = argparse.ArgumentParser(description="Enqueue P5 future assessments")
    parser.add_argument("--data-mode", choices=("demo", "sample", "live"))
    parser.add_argument("--as-of")
    parser.add_argument("--limit", type=int)
    args = parser.parse_args()
    settings = get_settings()
    as_of = args.as_of or datetime.now(UTC).isoformat()
    queue = Queue(settings.future_queue, connection=Redis.from_url(settings.redis_url))
    job = queue.enqueue(
        materialize_future_job,
        args.data_mode,
        as_of,
        args.limit,
        job_timeout="2h",
    )
    print(json.dumps({"job_id": job.id, "queue": queue.name, "as_of": as_of}))

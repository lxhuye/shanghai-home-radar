#!/usr/bin/env python3
"""Run Home Radar daily, with bounded retries for recoverable pipeline failures.

The scheduler contains no business logic. It launches ``daily_update_pipeline.py`` as a child
process, so manual, cron, and Compose executions share the same Redis singleton lock, reporting,
and exit semantics.
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
from collections.abc import Callable
from datetime import datetime, timedelta
from datetime import time as wall_time
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from home_radar_shared.config import get_settings

RETRYABLE_PIPELINE_EXIT_CODE = 75


def parse_schedule_time(value: str) -> wall_time:
    try:
        parsed = datetime.strptime(value, "%H:%M").time()
    except ValueError as exc:
        raise ValueError("SHR_DAILY_SCHEDULE_TIME must use 24-hour HH:MM") from exc
    return parsed


def next_run_at(now: datetime, scheduled: wall_time) -> datetime:
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    candidate = now.replace(
        hour=scheduled.hour,
        minute=scheduled.minute,
        second=0,
        microsecond=0,
    )
    return candidate if candidate > now else candidate + timedelta(days=1)


def startup_run_reason(
    *,
    force: bool,
    catch_up: bool,
    now: datetime,
    scheduled: wall_time,
    latest_attempt_at: datetime | None,
) -> str | None:
    """Return why startup should run once, without retrying an attempt from today."""
    if force:
        return "forced"
    if not catch_up:
        return None
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    scheduled_today = now.replace(
        hour=scheduled.hour,
        minute=scheduled.minute,
        second=0,
        microsecond=0,
    )
    if now < scheduled_today:
        return None
    if latest_attempt_at is None:
        return "missed_schedule"
    if latest_attempt_at.tzinfo is None:
        return "missed_schedule"
    return "missed_schedule" if latest_attempt_at.astimezone(now.tzinfo) < scheduled_today else None


def read_latest_attempt(path: Path) -> datetime | None:
    """Read the latest persisted attempt time; corrupt or absent state triggers catch-up."""
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None
    if not isinstance(value, dict):
        return None
    raw = value.get("end_time") or value.get("start_time")
    if not isinstance(raw, str):
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else None


def run_pipeline() -> int:
    script = Path(__file__).with_name("daily_update_pipeline.py")
    completed = subprocess.run((sys.executable, str(script)), check=False)
    return completed.returncode


def run_live_readiness() -> int:
    script = Path(__file__).with_name("live_readiness.py")
    completed = subprocess.run(
        (sys.executable, str(script), "--probe-source"),
        check=False,
    )
    return completed.returncode


def run_pipeline_with_retries(
    *,
    reason: str,
    max_attempts: int,
    initial_delay_seconds: float,
    max_delay_seconds: float,
    sleep: Callable[[float], None] = time.sleep,
) -> int:
    if max_attempts < 1:
        raise ValueError("SHR_DAILY_MAX_ATTEMPTS_PER_DAY must be positive")
    if initial_delay_seconds <= 0 or max_delay_seconds <= 0:
        raise ValueError("daily retry delays must be positive")
    if max_delay_seconds < initial_delay_seconds:
        raise ValueError("daily retry maximum must not be below the initial delay")

    for attempt in range(1, max_attempts + 1):
        print(
            json.dumps(
                {
                    "event": "daily_pipeline_start",
                    "reason": reason,
                    "attempt": attempt,
                    "max_attempts": max_attempts,
                },
                ensure_ascii=False,
            ),
            flush=True,
        )
        return_code = run_pipeline()
        print(
            json.dumps(
                {
                    "event": "daily_pipeline_exit",
                    "reason": reason,
                    "attempt": attempt,
                    "return_code": return_code,
                },
                ensure_ascii=False,
            ),
            flush=True,
        )
        if return_code != RETRYABLE_PIPELINE_EXIT_CODE or attempt == max_attempts:
            return return_code
        delay = min(initial_delay_seconds * (2 ** (attempt - 1)), max_delay_seconds)
        print(
            json.dumps(
                {
                    "event": "daily_pipeline_retry_scheduled",
                    "reason": reason,
                    "next_attempt": attempt + 1,
                    "delay_seconds": delay,
                },
                ensure_ascii=False,
            ),
            flush=True,
        )
        sleep(delay)
    raise AssertionError("unreachable")


def scheduler_loop() -> int:
    settings = get_settings()
    try:
        timezone = ZoneInfo(settings.daily_timezone)
    except ZoneInfoNotFoundError as exc:
        raise ValueError(f"unknown SHR_DAILY_TIMEZONE: {settings.daily_timezone}") from exc
    scheduled = parse_schedule_time(settings.daily_schedule_time)

    if settings.market_data_mode == "live":
        readiness_return_code = run_live_readiness()
        if readiness_return_code != 0:
            print(
                json.dumps(
                    {
                        "event": "daily_scheduler_live_readiness_failed",
                        "return_code": readiness_return_code,
                    },
                    ensure_ascii=False,
                ),
                flush=True,
            )
            return 2

    now = datetime.now(timezone)
    reason = startup_run_reason(
        force=settings.daily_run_on_start,
        catch_up=settings.daily_catch_up_on_start,
        now=now,
        scheduled=scheduled,
        latest_attempt_at=read_latest_attempt(settings.daily_report_directory / "latest.json"),
    )
    if reason is not None:
        run_pipeline_with_retries(
            reason=reason,
            max_attempts=settings.daily_max_attempts_per_day,
            initial_delay_seconds=settings.daily_retry_initial_delay_seconds,
            max_delay_seconds=settings.daily_retry_max_delay_seconds,
        )

    while True:
        now = datetime.now(timezone)
        next_run = next_run_at(now, scheduled)
        delay = max(0.0, (next_run - now).total_seconds())
        print(
            json.dumps(
                {
                    "event": "daily_pipeline_scheduled",
                    "now": now.isoformat(),
                    "next_run": next_run.isoformat(),
                    "sleep_seconds": round(delay, 3),
                },
                ensure_ascii=False,
            ),
            flush=True,
        )
        time.sleep(delay)
        run_pipeline_with_retries(
            reason="scheduled",
            max_attempts=settings.daily_max_attempts_per_day,
            initial_delay_seconds=settings.daily_retry_initial_delay_seconds,
            max_delay_seconds=settings.daily_retry_max_delay_seconds,
        )


def main() -> int:
    try:
        return scheduler_loop()
    except (ValueError, OSError) as exc:
        print(json.dumps({"event": "daily_scheduler_failed", "error": str(exc)}), flush=True)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

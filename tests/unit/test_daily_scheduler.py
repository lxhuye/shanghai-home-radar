from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from subprocess import CompletedProcess
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from scripts import daily_scheduler


def test_parse_schedule_time_requires_strict_hour_and_minute() -> None:
    assert daily_scheduler.parse_schedule_time("02:30").isoformat() == "02:30:00"
    with pytest.raises(ValueError, match="HH:MM"):
        daily_scheduler.parse_schedule_time("2am")


def test_next_run_uses_same_day_before_schedule() -> None:
    zone = ZoneInfo("Asia/Shanghai")
    now = datetime(2026, 9, 4, 1, 0, tzinfo=zone)

    result = daily_scheduler.next_run_at(now, daily_scheduler.parse_schedule_time("02:00"))

    assert result == datetime(2026, 9, 4, 2, 0, tzinfo=zone)


def test_next_run_rolls_to_tomorrow_at_or_after_schedule() -> None:
    zone = ZoneInfo("Asia/Shanghai")
    now = datetime(2026, 9, 4, 2, 0, tzinfo=zone)

    result = daily_scheduler.next_run_at(now, daily_scheduler.parse_schedule_time("02:00"))

    assert result == datetime(2026, 9, 5, 2, 0, tzinfo=zone)


def test_startup_catches_up_once_after_missed_schedule() -> None:
    zone = ZoneInfo("Asia/Shanghai")
    now = datetime(2026, 9, 4, 9, 0, tzinfo=zone)
    scheduled = daily_scheduler.parse_schedule_time("02:00")

    assert (
        daily_scheduler.startup_run_reason(
            force=False,
            catch_up=True,
            now=now,
            scheduled=scheduled,
            latest_attempt_at=datetime(2026, 9, 3, 2, 30, tzinfo=zone),
        )
        == "missed_schedule"
    )


def test_startup_does_not_repeat_attempt_after_today_schedule() -> None:
    zone = ZoneInfo("Asia/Shanghai")
    now = datetime(2026, 9, 4, 9, 0, tzinfo=zone)
    scheduled = daily_scheduler.parse_schedule_time("02:00")

    assert (
        daily_scheduler.startup_run_reason(
            force=False,
            catch_up=True,
            now=now,
            scheduled=scheduled,
            latest_attempt_at=datetime(2026, 9, 4, 2, 5, tzinfo=zone),
        )
        is None
    )


def test_startup_waits_when_schedule_is_still_ahead() -> None:
    zone = ZoneInfo("Asia/Shanghai")

    assert (
        daily_scheduler.startup_run_reason(
            force=False,
            catch_up=True,
            now=datetime(2026, 9, 4, 1, 0, tzinfo=zone),
            scheduled=daily_scheduler.parse_schedule_time("02:00"),
            latest_attempt_at=None,
        )
        is None
    )


def test_forced_start_takes_precedence_over_catch_up_setting() -> None:
    zone = ZoneInfo("Asia/Shanghai")

    assert (
        daily_scheduler.startup_run_reason(
            force=True,
            catch_up=False,
            now=datetime(2026, 9, 4, 1, 0, tzinfo=zone),
            scheduled=daily_scheduler.parse_schedule_time("02:00"),
            latest_attempt_at=None,
        )
        == "forced"
    )


def test_latest_attempt_reader_accepts_aware_timestamp(tmp_path: Path) -> None:
    latest = tmp_path / "latest.json"
    latest.write_text(
        json.dumps({"end_time": "2026-09-04T02:05:00+08:00", "status": "failed"}),
        encoding="utf-8",
    )

    assert daily_scheduler.read_latest_attempt(latest) == datetime.fromisoformat(
        "2026-09-04T02:05:00+08:00"
    )


@pytest.mark.parametrize("payload", ["not-json", "{}", '{"end_time":"2026-09-04"}'])
def test_latest_attempt_reader_treats_invalid_state_as_missing(
    tmp_path: Path, payload: str
) -> None:
    latest = tmp_path / "latest.json"
    latest.write_text(payload, encoding="utf-8")

    assert daily_scheduler.read_latest_attempt(latest) is None


def test_live_readiness_runs_read_only_source_probe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[tuple[str, ...]] = []

    def run(command: tuple[str, ...], *, check: bool) -> CompletedProcess[str]:
        captured.append(command)
        assert check is False
        return CompletedProcess(command, 0)

    monkeypatch.setattr(daily_scheduler.subprocess, "run", run)

    assert daily_scheduler.run_live_readiness() == 0
    assert captured == [
        (
            daily_scheduler.sys.executable,
            str(Path(daily_scheduler.__file__).with_name("live_readiness.py")),
            "--probe-source",
        )
    ]


def test_retryable_pipeline_failure_retries_with_bounded_backoff(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    return_codes = iter([daily_scheduler.RETRYABLE_PIPELINE_EXIT_CODE, 75, 0])
    sleeps: list[float] = []
    monkeypatch.setattr(daily_scheduler, "run_pipeline", lambda: next(return_codes))

    result = daily_scheduler.run_pipeline_with_retries(
        reason="scheduled",
        max_attempts=4,
        initial_delay_seconds=10,
        max_delay_seconds=30,
        sleep=sleeps.append,
    )

    assert result == 0
    assert sleeps == [10, 20]
    events = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert [event["event"] for event in events].count("daily_pipeline_start") == 3
    assert [event["delay_seconds"] for event in events if "delay_seconds" in event] == [
        10,
        20,
    ]


def test_non_retryable_pipeline_failure_does_not_repeat(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = 0
    sleeps: list[float] = []

    def run_pipeline() -> int:
        nonlocal calls
        calls += 1
        return 1

    monkeypatch.setattr(daily_scheduler, "run_pipeline", run_pipeline)

    result = daily_scheduler.run_pipeline_with_retries(
        reason="scheduled",
        max_attempts=4,
        initial_delay_seconds=10,
        max_delay_seconds=30,
        sleep=sleeps.append,
    )

    assert result == 1
    assert calls == 1
    assert sleeps == []


def test_retryable_pipeline_failure_stops_after_configured_attempts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = 0
    sleeps: list[float] = []

    def run_pipeline() -> int:
        nonlocal calls
        calls += 1
        return daily_scheduler.RETRYABLE_PIPELINE_EXIT_CODE

    monkeypatch.setattr(daily_scheduler, "run_pipeline", run_pipeline)

    result = daily_scheduler.run_pipeline_with_retries(
        reason="scheduled",
        max_attempts=4,
        initial_delay_seconds=10,
        max_delay_seconds=30,
        sleep=sleeps.append,
    )

    assert result == daily_scheduler.RETRYABLE_PIPELINE_EXIT_CODE
    assert calls == 4
    assert sleeps == [10, 20, 30]


def test_live_scheduler_exits_before_pipeline_when_readiness_fails(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(
        daily_scheduler,
        "get_settings",
        lambda: SimpleNamespace(
            daily_timezone="Asia/Shanghai",
            daily_schedule_time="02:00",
            market_data_mode="live",
        ),
    )
    monkeypatch.setattr(daily_scheduler, "run_live_readiness", lambda: 2)
    pipeline_called = False

    def run_pipeline() -> int:
        nonlocal pipeline_called
        pipeline_called = True
        return 0

    monkeypatch.setattr(daily_scheduler, "run_pipeline", run_pipeline)

    assert daily_scheduler.scheduler_loop() == 2
    assert pipeline_called is False
    output = json.loads(capsys.readouterr().out)
    assert output == {
        "event": "daily_scheduler_live_readiness_failed",
        "return_code": 2,
    }

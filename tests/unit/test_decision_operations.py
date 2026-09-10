from __future__ import annotations

import sys
import uuid
from datetime import UTC, datetime
from types import SimpleNamespace

import home_radar_decision.cli as decision_cli
import home_radar_decision.jobs as decision_jobs
import home_radar_decision.materializer as decision_materializer
import pytest
from home_radar_decision.materializer import BatchDecisionResult
from home_radar_models.enums import DataMode
from home_radar_valuation.aggregation import InsufficientValuationEvidenceError

AS_OF = datetime(2026, 9, 2, tzinfo=UTC)


def test_batch_materializer_isolates_failures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _FakeSession()
    listing_ids = [uuid.uuid4(), uuid.uuid4(), uuid.uuid4()]
    monkeypatch.setattr(
        decision_materializer,
        "_active_listing_ids",
        lambda *_args, **_kwargs: listing_ids,
    )
    calls = 0

    def evaluate(*_args: object, **_kwargs: object) -> tuple[object, bool]:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError("isolated failure")
        return object(), calls == 1

    monkeypatch.setattr(decision_materializer, "evaluate_cached_decision", evaluate)
    result = decision_materializer.materialize_decisions(
        session,
        DataMode.SAMPLE,
        AS_OF,
        object(),
        object(),
        object(),
        object(),
    )
    assert result.evaluated == 2
    assert result.cache_hits == 1
    assert result.failed == 1
    assert "RuntimeError" in result.failures[0]
    assert session.commits == 2
    assert session.rollbacks == 1


def test_batch_materializer_skips_insufficient_valuation_data(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _FakeSession()
    listing_id = uuid.uuid4()
    monkeypatch.setattr(
        decision_materializer,
        "_active_listing_ids",
        lambda *_args, **_kwargs: [listing_id],
    )

    def evaluate(*_args: object, **_kwargs: object) -> tuple[object, bool]:
        raise InsufficientValuationEvidenceError("no comparable evidence")

    monkeypatch.setattr(decision_materializer, "evaluate_cached_decision", evaluate)
    result = decision_materializer.materialize_decisions(
        session,
        DataMode.SAMPLE,
        AS_OF,
        object(),
        object(),
        object(),
        object(),
    )

    assert result.evaluated == 0
    assert result.skipped_insufficient_data == 1
    assert result.failed == 0
    assert "InsufficientValuationEvidenceError" in result.skips[0]
    assert session.rollbacks == 1


def test_decision_job_returns_batch_summary(monkeypatch: pytest.MonkeyPatch) -> None:
    session = _FakeSession()
    settings = SimpleNamespace(
        market_data_mode="sample",
        market_baseline_config_path="market.yaml",
        valuation_config_path="valuation.yaml",
        forecasting_config_path="future.yaml",
        decision_config_path="decision.yaml",
    )
    monkeypatch.setattr(decision_jobs, "get_settings", lambda: settings)
    monkeypatch.setattr(decision_jobs, "get_session_factory", lambda: lambda: session)
    monkeypatch.setattr(decision_jobs, "load_market_config", lambda _path: object())
    monkeypatch.setattr(decision_jobs, "load_valuation_config", lambda _path: object())
    monkeypatch.setattr(decision_jobs, "load_future_config", lambda _path: object())
    monkeypatch.setattr(decision_jobs, "load_decision_config", lambda _path: object())
    monkeypatch.setattr(
        decision_jobs,
        "materialize_decisions",
        lambda *_args, **_kwargs: BatchDecisionResult(
            3,
            1,
            1,
            ("one failure",),
            2,
            ("two skips",),
        ),
    )
    result = decision_jobs.materialize_decision_job("sample", AS_OF.isoformat(), 10)
    assert result["evaluated"] == 3
    assert result["cache_hits"] == 1
    assert result["failures"] == ["one failure"]
    assert result["skipped_insufficient_data"] == 2
    assert result["skips"] == ["two skips"]


def test_decision_cli_enqueues_job(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    settings = SimpleNamespace(decision_queue="decision", redis_url="redis://test")
    queue = _FakeQueue("decision", object())
    monkeypatch.setattr(decision_cli, "get_settings", lambda: settings)
    monkeypatch.setattr(decision_cli, "Redis", _FakeRedis)
    monkeypatch.setattr(decision_cli, "Queue", lambda *_args, **_kwargs: queue)
    monkeypatch.setattr(sys, "argv", ["decision", "--data-mode", "sample", "--limit", "2"])
    decision_cli.enqueue_main()
    output = capsys.readouterr().out
    assert '"job_id": "job-1"' in output
    assert queue.enqueued[1] == "sample"
    assert queue.enqueued[3] == 2


class _FakeSession:
    def __init__(self) -> None:
        self.commits = 0
        self.rollbacks = 0

    def __enter__(self) -> _FakeSession:
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def commit(self) -> None:
        self.commits += 1

    def rollback(self) -> None:
        self.rollbacks += 1


class _FakeRedis:
    @staticmethod
    def from_url(url: str) -> str:
        return url


class _FakeJob:
    id = "job-1"


class _FakeQueue:
    def __init__(self, name: str, connection: object) -> None:
        self.name = name
        self.connection = connection
        self.enqueued: tuple[object, ...] = ()

    def enqueue(self, *args: object, **_kwargs: object) -> _FakeJob:
        self.enqueued = args
        return _FakeJob()

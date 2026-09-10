from __future__ import annotations

import sys
import uuid
from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace
from typing import Any

import home_radar_forecasting.cli as future_cli
import home_radar_forecasting.jobs as future_jobs
import home_radar_forecasting.materializer as future_materializer
import pytest
from home_radar_forecasting.backtesting import (
    FutureBacktestCase,
    calculate_future_backtest_metrics,
)
from home_radar_forecasting.config import load_future_config
from home_radar_forecasting.contracts import ScenarioContract
from home_radar_forecasting.data_contracts import (
    BuyerPoolDataSource,
    EmploymentDataSource,
    FutureInputRepository,
    MacroFinancingDataSource,
    MarketCycleDataSource,
    PublicServicesDataSource,
    RentalBaselineSource,
    SupplyDataSource,
    TransportDataSource,
    UrbanRenewalDataSource,
)
from home_radar_forecasting.domain import (
    CalibrationEvidence,
    FactorEvidence,
    FutureInputs,
    FutureProperty,
)
from home_radar_forecasting.engine import DeterministicFutureEngine
from home_radar_forecasting.materializer import BatchFutureResult
from home_radar_models.enums import DataMode
from home_radar_valuation.aggregation import InsufficientValuationEvidenceError

AS_OF = datetime(2026, 9, 2, tzinfo=UTC)


def test_live_calibration_requires_verified_gates() -> None:
    factors = tuple(
        FactorEvidence(
            factor=name,
            current_score=Decimal("78"),
            future_score=Decimal("82"),
            confidence=Decimal("0.9"),
            source="verified-live",
            source_timestamp=AS_OF,
            data_mode="live",
            explanation="Verified live test evidence.",
        )
        for name in (
            "employment_accessibility",
            "transport_accessibility",
            "supply_scarcity",
            "buyer_pool_depth",
            "community_competitiveness",
            "urban_renewal",
            "rental_demand",
            "public_services",
            "planning_realization",
            "market_cycle",
            "building_aging",
            "product_obsolescence",
        )
    )
    inputs = FutureInputs(
        property=FutureProperty(
            listing_id=uuid.uuid4(),
            district="测试区",
            submarket="测试板块",
            community="测试社区",
            area_sqm=Decimal("80"),
            fair_value=Decimal("4000000"),
            value_score=Decimal("80"),
            valuation_version="p4-live",
            baseline_version="p3-live",
            baseline_confidence="high",
            valuation_confidence="high",
            transaction_support="strong",
            data_mode="live",
            year_built=2018,
            elevator=True,
            bedrooms=2,
            parking_quality="good",
            property_management_quality="good",
            maintenance_quality="good",
            layout_mainstreamness="good",
        ),
        factors=factors,
        hierarchical_components={
            "district_alpha": Decimal("10"),
            "submarket_alpha": Decimal("12"),
            "community_alpha": Decimal("8"),
            "property_alpha": Decimal("6"),
        },
        calibration=CalibrationEvidence(
            verified_case_count=250,
            transaction_calibration_available=True,
            employment_coverage_sufficient=True,
            supply_coverage_sufficient=True,
            live_isolation_verified=True,
            historical_continuity_score=Decimal("0.9"),
        ),
        data_version="verified-live-v1",
        data_timestamp=AS_OF,
    )
    result = DeterministicFutureEngine(load_future_config()).evaluate(inputs, AS_OF)
    assert result.calibration_state == "calibrated"
    assert result.confidence in {"medium", "high"}
    assert "UNCALIBRATED_MODEL" not in result.warnings
    assert result.investment_conclusion is None


def test_verified_future_backtest_metrics() -> None:
    cases = [
        _backtest_case(0, Decimal("0.8"), Decimal("0.9"), Decimal("3100000")),
        _backtest_case(1, Decimal("0.3"), Decimal("0.2"), Decimal("2850000")),
    ]
    result = calculate_future_backtest_metrics(cases)
    assert result.sample_count == 2
    assert result.scenario_coverage == Decimal("1")
    assert result.downside_calibration_error == Decimal("0")
    assert result.rank_correlation == Decimal("1")
    assert result.relative_outperformance_accuracy == Decimal("1")
    assert set(result.confidence_calibration) == {"high", "medium"}


def test_batch_materializer_isolates_listing_failures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _FakeSession()
    listing_ids = [uuid.uuid4(), uuid.uuid4(), uuid.uuid4()]
    monkeypatch.setattr(
        future_materializer,
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

    monkeypatch.setattr(future_materializer, "evaluate_cached_future", evaluate)
    result = future_materializer.materialize_future_assessments(
        session,
        DataMode.SAMPLE,
        AS_OF,
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
        future_materializer,
        "_active_listing_ids",
        lambda *_args, **_kwargs: [listing_id],
    )

    def evaluate(*_args: object, **_kwargs: object) -> tuple[object, bool]:
        raise InsufficientValuationEvidenceError("no comparable evidence")

    monkeypatch.setattr(future_materializer, "evaluate_cached_future", evaluate)
    result = future_materializer.materialize_future_assessments(
        session,
        DataMode.SAMPLE,
        AS_OF,
        object(),
        object(),
        object(),
    )

    assert result.evaluated == 0
    assert result.skipped_insufficient_data == 1
    assert result.failed == 0
    assert "InsufficientValuationEvidenceError" in result.skips[0]
    assert session.rollbacks == 1


def test_future_job_returns_batch_summary(monkeypatch: pytest.MonkeyPatch) -> None:
    session = _FakeSession()
    settings = SimpleNamespace(
        market_data_mode="sample",
        market_baseline_config_path="market.yaml",
        valuation_config_path="valuation.yaml",
        forecasting_config_path="future.yaml",
    )
    monkeypatch.setattr(future_jobs, "get_settings", lambda: settings)
    monkeypatch.setattr(future_jobs, "get_session_factory", lambda: lambda: session)
    monkeypatch.setattr(future_jobs, "load_market_config", lambda _path: object())
    monkeypatch.setattr(future_jobs, "load_valuation_config", lambda _path: object())
    monkeypatch.setattr(future_jobs, "load_future_config", lambda _path: object())
    monkeypatch.setattr(
        future_jobs,
        "materialize_future_assessments",
        lambda *_args, **_kwargs: BatchFutureResult(
            3,
            1,
            1,
            ("one failure",),
            2,
            ("two skips",),
        ),
    )
    result = future_jobs.materialize_future_job("sample", AS_OF.isoformat(), 10)
    assert result["evaluated"] == 3
    assert result["cache_hits"] == 1
    assert result["failures"] == ["one failure"]
    assert result["skipped_insufficient_data"] == 2
    assert result["skips"] == ["two skips"]


def test_future_cli_enqueues_job(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    settings = SimpleNamespace(future_queue="future", redis_url="redis://test")
    queue = _FakeQueue("future", object())
    monkeypatch.setattr(future_cli, "get_settings", lambda: settings)
    monkeypatch.setattr(future_cli, "Redis", _FakeRedis)
    monkeypatch.setattr(future_cli, "Queue", lambda *_args, **_kwargs: queue)
    monkeypatch.setattr(sys, "argv", ["future", "--data-mode", "sample", "--limit", "2"])
    future_cli.enqueue_main()
    output = capsys.readouterr().out
    assert '"job_id": "job-1"' in output
    assert queue.enqueued[1] == "sample"


def test_contracts_validate_scenario_order_and_protocols_are_available() -> None:
    with pytest.raises(ValueError, match="price range"):
        ScenarioContract(
            horizon_years=3,
            scenario="base",
            price_range_low=Decimal("4"),
            price_range_high=Decimal("3"),
            nominal_return_low=Decimal("0"),
            nominal_return_high=Decimal("0.1"),
            cagr_low=Decimal("0"),
            cagr_high=Decimal("0.03"),
            probability=Decimal("0.55"),
            confidence="low",
        )
    protocols = (
        EmploymentDataSource,
        TransportDataSource,
        SupplyDataSource,
        UrbanRenewalDataSource,
        RentalBaselineSource,
        PublicServicesDataSource,
        BuyerPoolDataSource,
        MarketCycleDataSource,
        MacroFinancingDataSource,
        FutureInputRepository,
    )
    assert all(protocol.__name__ for protocol in protocols)


def _backtest_case(
    index: int, predicted_rank: Decimal, observed_rank: Decimal, observed: Decimal
) -> FutureBacktestCase:
    return FutureBacktestCase(
        listing_id=uuid.uuid4(),
        assessment_version=f"p5-{index}",
        forecast_at=AS_OF,
        outcome_at=datetime(2027, 9, 2, tzinfo=UTC),
        horizon_years=1,
        fair_value_anchor=Decimal("3000000"),
        observed_value=observed,
        bear_probability=Decimal("0.2"),
        base_probability=Decimal("0.55"),
        bull_probability=Decimal("0.25"),
        forecast_low=Decimal("2700000"),
        forecast_high=Decimal("3300000"),
        predicted_rank=predicted_rank,
        observed_rank=observed_rank,
        predicted_downside=index == 1,
        observed_downside=index == 1,
        predicted_outperformance=index == 0,
        observed_outperformance=index == 0,
        confidence="high" if index == 0 else "medium",
        data_mode="live",
        verified_outcome=True,
    )


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
    def from_url(_url: str) -> object:
        return object()


class _FakeQueue:
    def __init__(self, name: str, connection: object) -> None:
        self.name = name
        self.connection = connection
        self.enqueued: tuple[Any, ...] = ()

    def enqueue(self, *args: Any, **_kwargs: Any) -> object:
        self.enqueued = args
        return SimpleNamespace(id="job-1")

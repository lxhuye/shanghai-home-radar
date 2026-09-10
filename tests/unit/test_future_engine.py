from __future__ import annotations

import json
import uuid
from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from home_radar_forecasting.accessibility import (
    employment_accessibility,
    transport_accessibility,
)
from home_radar_forecasting.backtesting import (
    FutureBacktestCase,
    calculate_future_backtest_metrics,
)
from home_radar_forecasting.config import load_future_config
from home_radar_forecasting.domain import (
    EmploymentCenterInput,
    FactorEvidence,
    FutureInputs,
    FutureProjectInput,
    FutureProperty,
)
from home_radar_forecasting.engine import DeterministicFutureEngine

AS_OF = datetime(2026, 9, 2, tzinfo=UTC)
CASES_PATH = Path("data/sample/p5_future_cases.json")


def test_employment_accessibility_separates_current_and_future_weights() -> None:
    centers = (
        EmploymentCenterInput(
            name="near-current",
            category="current",
            longitude=Decimal("121.44"),
            latitude=Decimal("31.19"),
            current_employment_weight=Decimal("1"),
            future_employment_weight=Decimal("0.2"),
            source="test",
            source_timestamp=AS_OF,
            confidence=Decimal("0.9"),
        ),
        EmploymentCenterInput(
            name="far-future",
            category="future",
            longitude=Decimal("121.60"),
            latitude=Decimal("31.19"),
            current_employment_weight=Decimal("0.1"),
            future_employment_weight=Decimal("2"),
            source="test",
            source_timestamp=AS_OF,
            confidence=Decimal("0.8"),
        ),
    )
    result = employment_accessibility(
        Decimal("121.44"),
        Decimal("31.19"),
        centers,
        load_future_config().employment_accessibility,
        "demo",
    )
    assert result.current_score is not None and result.future_score is not None
    assert result.current_score > result.future_score
    assert result.metadata["routing_method"] == "haversine_v0"


def test_transport_planning_is_probability_weighted_and_capped() -> None:
    projects = (
        _project("current", Decimal("121.50")),
        _project("planned", Decimal("121.4405")),
    )
    config = load_future_config()
    result = transport_accessibility(
        Decimal("121.44"),
        Decimal("31.19"),
        projects,
        config.transport_accessibility,
        config.planning_realization_weights,
        "demo",
    )
    assert result.current_score is not None and result.future_score is not None
    assert result.future_score - result.current_score <= Decimal("25")
    assert result.metadata["planning_share"] != "1"


def test_synthetic_future_cases_preserve_value_quality_distinction() -> None:
    results = {
        case["id"]: DeterministicFutureEngine(load_future_config()).evaluate(_inputs(case), AS_OF)
        for case in _cases()
    }
    assert results["A"].future_score is not None and results["A"].future_score >= 75
    assert results["A"].obsolescence_risk is not None
    assert results["A"].obsolescence_risk < 50
    assert results["A"].quality_value_quadrant == "QUALITY_AT_DISCOUNT"

    assert results["B"].future_score is not None and results["B"].future_score < 45
    assert results["B"].obsolescence_risk is not None
    assert results["B"].obsolescence_risk >= 65
    assert results["B"].quality_value_quadrant == "VALUE_TRAP"

    assert results["C"].future_score is not None and results["C"].future_score >= 75
    assert results["C"].quality_value_quadrant == "GOOD_BUT_EXPENSIVE"

    assert "PLANNING_DEPENDENCY" in results["D"].warnings
    assert results["D"].confidence in {"low", "insufficient"}

    assert results["E"].future_score is not None and results["E"].future_score >= 70
    assert results["E"].obsolescence_risk is not None
    assert results["E"].obsolescence_risk >= 45
    assert results["E"].obsolescence_risk != Decimal("100") - results["E"].future_score


def test_scenarios_use_fair_value_and_probabilities_sum_to_one() -> None:
    inputs = _inputs(_cases()[0])
    engine = DeterministicFutureEngine(load_future_config())
    result = engine.evaluate(inputs, AS_OF)
    changed_value_score = engine.evaluate(
        replace(inputs, property=replace(inputs.property, value_score=Decimal("10"))),
        AS_OF,
    )
    assert len(result.scenarios) == 9
    for horizon in (1, 3, 5):
        scoped = [item for item in result.scenarios if item.horizon_years == horizon]
        assert sum((item.probability for item in scoped), Decimal()) == Decimal("1")
        assert all(item.price_range_low <= item.price_range_high for item in scoped)
    assert [item.price_range_low for item in result.scenarios] == [
        item.price_range_low for item in changed_value_score.scenarios
    ]
    assert result.provenance["fair_value_anchor_source"] == "p4_valuation"
    assert result.provenance["asking_price_used_as_forecast_anchor"] is False


def test_demo_output_is_uncalibrated_and_never_emits_investment_conclusion() -> None:
    result = DeterministicFutureEngine(load_future_config()).evaluate(_inputs(_cases()[0]), AS_OF)
    assert result.calibration_state == "uncalibrated"
    assert result.investment_conclusion is None
    assert result.recommendation_status == "research_uncalibrated"
    assert result.confidence in {"low", "insufficient"}
    assert {"UNCALIBRATED_MODEL", "NON_LIVE_DATA"}.issubset(result.warnings)


def test_future_api_surface_is_registered() -> None:
    from home_radar_api.main import app

    paths = app.openapi()["paths"]
    listing_path = "/api/v1/future/listings/{listing_id}"
    assert listing_path in paths
    assert f"{listing_path}/factors" in paths
    assert f"{listing_path}/scenarios" in paths
    assert f"{listing_path}/risks" in paths


def test_missing_rental_data_is_explicitly_insufficient() -> None:
    inputs = _inputs(_cases()[0])
    filtered = replace(
        inputs,
        factors=tuple(factor for factor in inputs.factors if factor.factor != "rental_demand"),
    )
    result = DeterministicFutureEngine(load_future_config()).evaluate(filtered, AS_OF)
    rental = next(factor for factor in result.factors if factor.factor == "rental_demand")
    assert rental.current_score is None
    assert rental.future_score is None
    assert rental.source == "insufficient"
    assert "LOW_RENTAL_SUPPORT" in result.warnings


def test_cross_mode_factor_is_rejected() -> None:
    inputs = _inputs(_cases()[0])
    mixed = replace(inputs.factors[0], data_mode="live")
    with pytest.raises(ValueError, match="data modes"):
        DeterministicFutureEngine(load_future_config()).evaluate(
            replace(inputs, factors=(mixed, *inputs.factors[1:])), AS_OF
        )


def test_backtesting_refuses_synthetic_or_sample_metrics() -> None:
    case = FutureBacktestCase(
        listing_id=uuid.uuid4(),
        assessment_version="p5-test",
        forecast_at=AS_OF,
        outcome_at=datetime(2027, 9, 2, tzinfo=UTC),
        horizon_years=1,
        fair_value_anchor=Decimal("3000000"),
        observed_value=Decimal("3100000"),
        bear_probability=Decimal("0.2"),
        base_probability=Decimal("0.55"),
        bull_probability=Decimal("0.25"),
        forecast_low=Decimal("2800000"),
        forecast_high=Decimal("3300000"),
        predicted_rank=Decimal("0.7"),
        observed_rank=Decimal("0.8"),
        predicted_downside=False,
        observed_downside=False,
        predicted_outperformance=True,
        observed_outperformance=True,
        confidence="low",
        data_mode="sample",
        verified_outcome=False,
    )
    with pytest.raises(ValueError, match="cannot publish"):
        calculate_future_backtest_metrics([case])


def _project(status: str, longitude: Decimal) -> FutureProjectInput:
    return FutureProjectInput(
        name=status,
        project_type="transport",
        status=status,
        longitude=longitude,
        latitude=Decimal("31.19"),
        expected_completion=None,
        source="test",
        source_date=AS_OF,
        confidence=Decimal("0.9"),
    )


def _cases() -> list[dict[str, Any]]:
    payload = json.loads(CASES_PATH.read_text(encoding="utf-8"))
    assert isinstance(payload, dict)
    cases = payload["cases"]
    assert isinstance(cases, list)
    return cases


def _inputs(case: dict[str, Any]) -> FutureInputs:
    property_data = case["property"]
    case_id = str(case["id"])
    property_ = FutureProperty(
        listing_id=uuid.uuid5(uuid.NAMESPACE_URL, f"p5-case-{case_id}"),
        district="上海测试区",
        submarket="测试板块",
        community=f"测试社区{case_id}",
        valuation_version=f"p4-case-{case_id}",
        baseline_version=f"p3-case-{case_id}",
        baseline_confidence="medium",
        valuation_confidence="medium",
        transaction_support="none",
        data_mode="demo",
        layout="2BR",
        **property_data,
    )
    factors = tuple(
        FactorEvidence(
            factor=name,
            current_score=Decimal(str(scores[0])),
            future_score=Decimal(str(scores[1])),
            confidence=Decimal("0.8"),
            source="synthetic_validation_fixture",
            source_timestamp=AS_OF,
            data_mode="demo",
            explanation=f"Synthetic P5 validation evidence for {name}.",
        )
        for name, scores in case["factors"].items()
    )
    return FutureInputs(
        property=property_,
        factors=factors,
        hierarchical_components={
            key: Decimal(str(value)) for key, value in case["hierarchy"].items()
        },
        data_version=f"synthetic-case-{case_id}",
        data_timestamp=AS_OF,
    )

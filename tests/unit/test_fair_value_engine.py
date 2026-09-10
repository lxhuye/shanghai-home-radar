from __future__ import annotations

import uuid
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from home_radar_models.enums import DataMode
from home_radar_valuation.adjustments import (
    adjust_comparable,
    target_missing_attributes,
)
from home_radar_valuation.aggregation import (
    InsufficientValuationEvidenceError,
    aggregate_fair_value,
    weighted_quantile,
)
from home_radar_valuation.backtesting import (
    VerifiedBacktestCase,
    calculate_backtest_metrics,
)
from home_radar_valuation.comparables import select_comparables
from home_radar_valuation.config import load_valuation_config
from home_radar_valuation.domain import (
    BaselineEvidence,
    ComparableRecord,
    PriceHistory,
    TargetProperty,
)
from home_radar_valuation.engine import DeterministicValuationEngine
from home_radar_valuation.repository import EvaluationInputs
from home_radar_valuation.scoring import calculate_value_score, piecewise_linear
from home_radar_valuation.warnings import apply_warning_rules

AS_OF = datetime(2026, 9, 2, 4, tzinfo=UTC)


def _target(**changes: object) -> TargetProperty:
    value = TargetProperty(
        listing_id=uuid.uuid4(),
        district="徐汇",
        submarket="徐家汇",
        community="示例花园",
        area_sqm=Decimal("60"),
        current_ask=Decimal("2760000"),
        bedrooms=2,
        layout="2BR",
        floor="中楼层",
        total_floors=6,
        orientation="南北",
        year_built=2005,
        elevator=False,
        building_type="walk_up",
        metro_distance_m=450,
        employment_accessibility=Decimal("80"),
        mature_amenity_accessibility=Decimal("85"),
        layout_quality="mainstream",
        road_noise_exposure="normal",
        hard_defects=(),
        hard_defects_known=True,
        data_mode=DataMode.SAMPLE.value,
    )
    return replace(value, **changes)


def _candidate(index: int, **changes: object) -> ComparableRecord:
    value = ComparableRecord(
        observation_id=uuid.UUID(int=index + 100),
        listing_id=uuid.UUID(int=index + 1000),
        observation_type="listing",
        source="authorized-feed-a",
        source_record_id=f"record-{index}",
        observed_at=AS_OF - timedelta(days=10 + index),
        source_confidence=Decimal("0.9"),
        district="徐汇",
        submarket="徐家汇",
        community="示例花园",
        area_sqm=Decimal("60"),
        total_price=Decimal("3000000"),
        unit_price=Decimal("50000"),
        bedrooms=2,
        layout="2BR",
        floor="中楼层",
        total_floors=6,
        orientation="南北",
        year_built=2005,
        elevator=False,
        building_type="walk_up",
        metro_distance_m=450,
        layout_quality="mainstream",
        road_noise_exposure="normal",
    )
    return replace(value, **changes)


def _baseline(**changes: object) -> BaselineEvidence:
    value = BaselineEvidence(
        observation_type="listing",
        level_used="community",
        confidence="high",
        sample_count=20,
        unit_price_p25=Decimal("48000"),
        unit_price_p50=Decimal("50000"),
        unit_price_p75=Decimal("52000"),
        price_p50=Decimal("3000000"),
        liquidity_score=Decimal("78"),
        baseline_versions=("p3-baseline-v1",),
        fallback_path=("community",),
        fallback_reason=None,
        generated_at=AS_OF - timedelta(hours=2),
    )
    return replace(value, **changes)


def _history(ask: Decimal = Decimal("2760000"), cuts: int = 1) -> PriceHistory:
    original = Decimal("3000000")
    return PriceHistory(
        original_ask=original,
        current_ask=ask,
        absolute_reduction=max(Decimal("0"), original - ask),
        percentage_reduction=max(Decimal("0"), original - ask) / original,
        price_cut_count=cuts,
        days_since_last_cut=15 if cuts else None,
        days_on_market=100,
        relisting_flag=False,
    )


def _inputs(
    target: TargetProperty | None = None,
    candidates: list[ComparableRecord] | None = None,
    baseline: BaselineEvidence | None = None,
    history: PriceHistory | None = None,
) -> EvaluationInputs:
    target = target or _target()
    baseline = baseline if baseline is not None else _baseline()
    return EvaluationInputs(
        target=target,
        candidates=candidates or [_candidate(index) for index in range(6)],
        valuation_baseline=baseline,
        listing_baseline=_baseline(),
        price_history=history or _history(target.current_ask),
        latest_observation_id=uuid.UUID(int=1),
    )


def test_exact_community_comparable_is_tier_1() -> None:
    config = load_valuation_config().comparables
    selection = select_comparables(
        _target(), [_candidate(1), _candidate(2), _candidate(3)], AS_OF, config
    )
    assert {item.tier for item in selection.comparables} == {"tier_1"}
    assert sum(item.weight for item in selection.comparables) == pytest.approx(
        Decimal("1"), abs=Decimal("0.00001")
    )
    assert all(item.reasons for item in selection.comparables)


def test_tier_and_freshness_fallback_are_explicit() -> None:
    config = load_valuation_config().comparables
    candidates = [
        _candidate(
            index,
            community="邻近花园",
            area_sqm=Decimal("65"),
            observed_at=AS_OF - timedelta(days=120 + index),
        )
        for index in range(3)
    ]
    selection = select_comparables(_target(), candidates, AS_OF, config)
    assert selection.window_days == 180
    assert {item.tier for item in selection.comparables} == {"tier_3"}
    assert selection.fallback_reason == "freshness_expanded_to_180d"


def test_area_layout_and_age_rules_prevent_false_tier_1() -> None:
    config = load_valuation_config().comparables
    candidates = [
        _candidate(1, area_sqm=Decimal("70")),
        _candidate(2, layout="3BR+", bedrooms=3),
        _candidate(3, year_built=1990),
    ]
    selection = select_comparables(_target(), candidates, AS_OF, config)
    assert selection.comparables
    assert all(item.tier != "tier_1" for item in selection.comparables)


def test_transaction_weight_exceeds_equivalent_listing_weight() -> None:
    config = load_valuation_config().comparables
    listing = _candidate(1)
    transaction = _candidate(
        2,
        observation_type="transaction",
        source="authorized-transaction-feed",
    )
    selection = select_comparables(_target(), [listing, transaction, _candidate(3)], AS_OF, config)
    weights = {item.record.observation_type: item.weight for item in selection.comparables}
    assert weights["transaction"] > weights["listing"]


def test_floor_elevator_interaction_is_single_adjustment() -> None:
    config = load_valuation_config().adjustments
    target = _target(floor="顶层", elevator=False, orientation="南北", year_built=2000)
    selection = select_comparables(
        target,
        [_candidate(1, orientation="北", year_built=2010), _candidate(2), _candidate(3)],
        AS_OF,
        load_valuation_config().comparables,
    )
    comparable = next(
        item for item in selection.comparables if item.record.observation_id == uuid.UUID(int=101)
    )
    adjusted = adjust_comparable(target, comparable, config)
    factors = [item.factor for item in adjusted.adjustments]
    assert factors.count("floor_elevator_interaction") == 1
    assert "orientation" in factors
    assert "building_age_relative" in factors
    assert "elevator" not in factors


def test_unknown_target_features_add_uncertainty_not_silent_adjustment() -> None:
    target = _target(
        floor=None,
        total_floors=None,
        orientation=None,
        year_built=None,
        elevator=None,
        building_type=None,
        metro_distance_m=None,
        layout_quality="unknown",
        road_noise_exposure="unknown",
    )
    selection = select_comparables(
        target,
        [_candidate(1), _candidate(2), _candidate(3)],
        AS_OF,
        load_valuation_config().comparables,
    )
    adjusted = adjust_comparable(
        target, selection.comparables[0], load_valuation_config().adjustments
    )
    assert not adjusted.adjustments
    assert len(target_missing_attributes(target)) >= 8


def test_weighted_median_and_outlier_protection() -> None:
    config = load_valuation_config()
    assert weighted_quantile(
        [(Decimal("100"), Decimal("0.2")), (Decimal("200"), Decimal("0.8"))],
        Decimal("0.5"),
    ) == Decimal("200")
    candidates = [_candidate(index) for index in range(5)] + [
        _candidate(9, total_price=Decimal("12000000"), unit_price=Decimal("200000"))
    ]
    selection = select_comparables(_target(), candidates, AS_OF, config.comparables)
    adjusted = tuple(
        adjust_comparable(_target(), item, config.adjustments) for item in selection.comparables
    )
    result = aggregate_fair_value(
        _target(),
        adjusted,
        _baseline(),
        config.comparables,
        config.adjustments,
        0,
    )
    assert result.fair_value < Decimal("4000000")
    assert len(result.retained_comparables) < len(adjusted)


def test_listing_and_transaction_basis_are_not_conflated() -> None:
    engine = DeterministicValuationEngine(load_valuation_config())
    listing_result = engine.evaluate(_inputs(), AS_OF)
    mixed = [_candidate(index) for index in range(4)] + [
        _candidate(
            10,
            observation_type="transaction",
            source="authorized-transaction-feed",
        )
    ]
    mixed_result = engine.evaluate(_inputs(candidates=mixed), AS_OF)
    assert listing_result.valuation_basis == "listing_derived"
    assert listing_result.transaction_support == "none"
    assert mixed_result.valuation_basis == "mixed_source"
    assert mixed_result.transaction_support == "weak"


def test_price_edge_curve_is_continuous_and_configured() -> None:
    config = load_valuation_config().value_score
    below = piecewise_linear(Decimal("0.0499"), config.price_edge_curve)
    above = piecewise_linear(Decimal("0.0501"), config.price_edge_curve)
    assert abs(above - below) < Decimal("0.2")
    assert piecewise_linear(Decimal("0.10"), config.price_edge_curve) == Decimal("32")


def test_large_discount_bad_product_is_capped_below_attack() -> None:
    config = load_valuation_config()
    target = _target(floor="顶层", elevator=False, building_type="walk_up")
    warning = apply_warning_rules(
        target,
        Decimal("0.15"),
        Decimal("20"),
        2,
        "low",
        "low",
        "none",
        Decimal("98"),
        config,
    )
    assert "VALUE_TRAP_RISK" in warning.warnings
    assert "EXTREME_FLOOR_RISK" in warning.warnings
    assert warning.capped_score == Decimal("89")
    assert warning.decision == "view"


def test_price_cuts_raise_value_score_without_changing_fair_value() -> None:
    config = load_valuation_config()
    engine = DeterministicValuationEngine(config)
    original_target = _target(current_ask=Decimal("3000000"))
    reduced_target = replace(original_target, current_ask=Decimal("2700000"))
    original = engine.evaluate(
        _inputs(target=original_target, history=_history(Decimal("3000000"), 0)), AS_OF
    )
    reduced = engine.evaluate(
        _inputs(target=reduced_target, history=_history(Decimal("2700000"), 3)), AS_OF
    )
    assert original.fair_value == reduced.fair_value
    assert reduced.value_score > original.value_score


def test_executable_price_is_null_without_structured_evidence() -> None:
    result = DeterministicValuationEngine(load_valuation_config()).evaluate(_inputs(), AS_OF)
    assert result.estimated_executable_price is None
    documented = _target(
        documented_executable_price=Decimal("2650000"),
        executable_price_evidence="authorized broker written quote",
    )
    result_with_evidence = DeterministicValuationEngine(load_valuation_config()).evaluate(
        _inputs(target=documented), AS_OF
    )
    assert result_with_evidence.estimated_executable_price == Decimal("2650000")


def test_no_comparables_and_no_baseline_is_insufficient() -> None:
    inputs = replace(_inputs(), candidates=[], valuation_baseline=None, listing_baseline=None)
    with pytest.raises(InsufficientValuationEvidenceError):
        DeterministicValuationEngine(load_valuation_config()).evaluate(inputs, AS_OF)


def test_sample_mode_is_visibly_demo_only() -> None:
    result = DeterministicValuationEngine(load_valuation_config()).evaluate(_inputs(), AS_OF)
    assert result.recommendation_status == "demo_only"
    assert "SAMPLE" in result.data_notice
    assert result.provenance["investment_recommendation"] is False


def test_backtesting_interface_requires_verified_cases() -> None:
    with pytest.raises(ValueError):
        calculate_backtest_metrics([])
    cases = [
        VerifiedBacktestCase(
            listing_id=uuid.uuid4(),
            valuation_version=f"v{index}",
            valuation_at=AS_OF,
            predicted_fair_value=Decimal("3000000") + index * Decimal("100000"),
            value_score=Decimal("70") + index,
            confidence_bucket="medium",
            verified_transaction_at=AS_OF + timedelta(days=30),
            verified_transaction_price=Decimal("2900000") + index * Decimal("120000"),
        )
        for index in range(3)
    ]
    metrics = calculate_backtest_metrics(cases)
    assert metrics.sample_count == 3
    assert metrics.directional_ranking_quality == Decimal("1")


def test_current_accessibility_and_quality_scores_use_known_evidence_only() -> None:
    config = load_valuation_config().value_score
    strong = calculate_value_score(
        _target(), Decimal("3000000"), Decimal("80"), _history(), 2005, config
    )
    unknown = calculate_value_score(
        _target(
            metro_distance_m=None,
            employment_accessibility=None,
            mature_amenity_accessibility=None,
            floor=None,
            orientation=None,
            year_built=None,
            elevator=None,
            layout_quality="unknown",
            hard_defects_known=False,
        ),
        Decimal("3000000"),
        Decimal("80"),
        _history(),
        None,
        config,
    )
    assert strong.components["employment_transit"]["coverage"] == Decimal("1")
    assert unknown.components["employment_transit"]["coverage"] == Decimal("0")
    assert unknown.components["property_quality"]["coverage"] == Decimal("0")


def test_seed_cases_a_b_and_c_match_screening_expectations() -> None:
    engine = DeterministicValuationEngine(load_valuation_config())

    case_a_target = _target(current_ask=Decimal("2760000"))
    case_a = engine.evaluate(_inputs(target=case_a_target), AS_OF)
    assert case_a.value_score >= Decimal("75")

    case_b_target = _target(
        current_ask=Decimal("2100000"),
        floor="顶层",
        elevator=False,
        year_built=1985,
        hard_defects=("roof_leak_history",),
    )
    low_liquidity = _baseline(liquidity_score=Decimal("20"), confidence="low")
    case_b_inputs = replace(
        _inputs(target=case_b_target),
        valuation_baseline=low_liquidity,
        listing_baseline=low_liquidity,
    )
    case_b = engine.evaluate(case_b_inputs, AS_OF)
    assert "VALUE_TRAP_RISK" in case_b.warnings
    assert case_b.decision != "attack"

    case_c_target = _target(current_ask=Decimal("3300000"))
    case_c = engine.evaluate(_inputs(target=case_c_target), AS_OF)
    assert case_c.decision in {"pass", "watch"}

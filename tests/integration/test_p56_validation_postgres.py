from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import home_radar_decision.real_world_validation as validation_service
import pytest
from home_radar_decision.config import load_decision_config
from home_radar_decision.p56_config import load_real_world_validation_config
from home_radar_decision.real_world_validation import (
    RealWorldValidationError,
    append_label_amendment,
    create_validation_run,
    finalize_validation_run,
    freeze_human_labels,
    generate_model_results,
    label_validation_item,
    record_review,
    reveal_validation_run,
    validation_run_items,
)
from home_radar_forecasting.config import load_future_config
from home_radar_market.config import load_market_config
from home_radar_models.decision import DecisionAssessment
from home_radar_models.enums import DataMode
from home_radar_models.future import FutureFactorObservation
from home_radar_models.listing import Listing
from home_radar_valuation.config import load_valuation_config
from sqlalchemy.orm import Session

pytestmark = pytest.mark.integration

AS_OF = datetime(2026, 9, 2, 8, tzinfo=UTC)
GEOGRAPHIES = (
    "OUTER_XUHUI",
    "NORTHERN_MINHANG",
    "PUTUO",
    "YANGPU",
    "MATURE_PUDONG",
)
ARCHETYPES = tuple(f"ARCHETYPE_{index}" for index in range(9))
CONFIG_PATHS = tuple(sorted(Path("config").glob("*.yaml")))


def test_p56_freeze_generate_reveal_review_and_finalize(
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    listings = [_listing(db_session, index) for index in range(45)]
    db_session.commit()
    market = load_market_config()
    valuation = load_valuation_config()
    future = load_future_config()
    decision = load_decision_config()
    validation = load_real_world_validation_config()
    entries = [
        {
            "listing_id": listing.id,
            "geography_bucket": GEOGRAPHIES[index % len(GEOGRAPHIES)],
            "archetypes": [ARCHETYPES[index % len(ARCHETYPES)]],
            "data_provenance": {
                "kind": "PROPERLY_ANONYMIZED",
                "reference": f"blind-source-{index}",
            },
        }
        for index, listing in enumerate(listings)
    ]
    batch = create_validation_run(
        db_session,
        name="P5.6 integration",
        entries=entries,
        data_mode=DataMode.SAMPLE,
        as_of=AS_OF,
        source_commit="test-commit",
        source_tree_hash="test-tree",
        config_paths=CONFIG_PATHS,
        market_config=market,
        valuation_config=valuation,
        future_config=future,
        decision_config=decision,
        validation_config=validation,
    )
    assert batch.status == "blind_labeling"
    assert batch.model_outputs_fingerprint is None
    db_session.commit()

    for listing in listings:
        label_validation_item(
            db_session,
            batch.id,
            listing_id=listing.id,
            opportunity_classification="QUALITY_AT_DISCOUNT",
            workflow_recommendation="VIEW",
            confidence="HIGH",
            positive_reasons=["价格有折让"],
            risks=["仍需线下核验"],
            would_visit=True,
            notes=None,
        )
    db_session.commit()
    freeze_human_labels(db_session, batch.id)
    frozen_labels = batch.human_labels_fingerprint
    assert batch.status == "labels_frozen"
    assert frozen_labels
    db_session.commit()
    with pytest.raises(RealWorldValidationError, match="immutable"):
        label_validation_item(
            db_session,
            batch.id,
            listing_id=listings[0].id,
            opportunity_classification="VALUE_TRAP",
            workflow_recommendation="PASS",
            confidence="LOW",
            positive_reasons=["便宜"],
            risks=["资产老化"],
            would_visit=False,
            notes="forbidden overwrite",
        )

    monkeypatch.setattr(
        validation_service,
        "evaluate_cached_decision",
        lambda session, listing_id, *_args: (_assessment(session, listing_id), False),
    )
    generate_model_results(
        db_session,
        batch.id,
        market_config=market,
        valuation_config=valuation,
        future_config=future,
        decision_config=decision,
        source_commit="test-commit",
        source_tree_hash="test-tree",
        config_paths=CONFIG_PATHS,
    )
    assert batch.status == "model_generated"
    assert batch.model_outputs_fingerprint
    assert batch.human_labels_fingerprint == frozen_labels
    db_session.commit()

    reveal_validation_run(db_session, batch.id)
    assert batch.status == "revealed"
    assert batch.metrics["regret_at_5"] == 0
    db_session.commit()
    for listing in listings:
        record_review(
            db_session,
            batch.id,
            listing_id=listing.id,
            why_ranked_verdict="AGREE",
            root_cause=None,
            notes=None,
        )
    db_session.commit()
    append_label_amendment(
        db_session,
        batch.id,
        listing_id=listings[0].id,
        proposed_label={"workflow_recommendation": "WATCH"},
        reason="post-reveal discussion",
    )
    finalized = finalize_validation_run(
        db_session,
        batch.id,
        validation_config=validation,
        report_directory=tmp_path,
    )
    assert finalized.status == "finalized"
    assert finalized.final_recommendation == "P6_READY"
    assert (tmp_path / "P56_REAL_WORLD_VALIDATION_REPORT.md").exists()
    assert (tmp_path / f"P56_ERROR_REVIEW_{batch.validation_run_id}.md").exists()
    _, items = validation_run_items(db_session, batch.id)
    assert items[0].human_workflow_recommendation == "VIEW"


def test_p56_rejects_demo_before_creating_a_run(db_session: Session) -> None:
    with pytest.raises(RealWorldValidationError, match="DEMO"):
        create_validation_run(
            db_session,
            name="forbidden demo",
            entries=[],
            data_mode=DataMode.DEMO,
            as_of=AS_OF,
            source_commit="test-commit",
            source_tree_hash="test-tree",
            config_paths=CONFIG_PATHS,
            market_config=load_market_config(),
            valuation_config=load_valuation_config(),
            future_config=load_future_config(),
            decision_config=load_decision_config(),
            validation_config=load_real_world_validation_config(),
        )


def test_p56_rejects_backdated_evidence_added_after_freeze(db_session: Session) -> None:
    listings = [_listing(db_session, index, source_prefix="p56-frozen") for index in range(45)]
    db_session.commit()
    market = load_market_config()
    valuation = load_valuation_config()
    future = load_future_config()
    decision = load_decision_config()
    validation = load_real_world_validation_config()
    entries = [
        {
            "listing_id": listing.id,
            "geography_bucket": GEOGRAPHIES[index % len(GEOGRAPHIES)],
            "archetypes": [ARCHETYPES[index % len(ARCHETYPES)]],
            "data_provenance": {
                "kind": "PROPERLY_ANONYMIZED",
                "reference": f"frozen-input-{index}",
            },
        }
        for index, listing in enumerate(listings)
    ]
    batch = create_validation_run(
        db_session,
        name="P5.6 frozen input integration",
        entries=entries,
        data_mode=DataMode.SAMPLE,
        as_of=AS_OF,
        source_commit="test-commit",
        source_tree_hash="test-tree",
        config_paths=CONFIG_PATHS,
        market_config=market,
        valuation_config=valuation,
        future_config=future,
        decision_config=decision,
        validation_config=validation,
    )
    for listing in listings:
        label_validation_item(
            db_session,
            batch.id,
            listing_id=listing.id,
            opportunity_classification="QUALITY_AT_DISCOUNT",
            workflow_recommendation="VIEW",
            confidence="HIGH",
            positive_reasons=["价格有折让"],
            risks=["仍需线下核验"],
            would_visit=True,
            notes=None,
        )
    freeze_human_labels(db_session, batch.id)
    db_session.add(
        FutureFactorObservation(
            data_mode="sample",
            factor="supply_scarcity",
            scope_type="shanghai",
            current_score=Decimal("60"),
            future_score=Decimal("62"),
            observed_at=AS_OF - timedelta(days=1),
            effective_from=AS_OF - timedelta(days=1),
            source="late-backdated-source",
            source_record_id="late-backdated-1",
            source_timestamp=AS_OF - timedelta(days=1),
            confidence=Decimal("0.7"),
            explanation="Inserted after freeze with a timestamp before the cutoff.",
            observation_metadata={},
            provenance={"test": True},
        )
    )
    db_session.commit()

    with pytest.raises(RealWorldValidationError, match="model_input_fingerprints"):
        generate_model_results(
            db_session,
            batch.id,
            market_config=market,
            valuation_config=valuation,
            future_config=future,
            decision_config=decision,
            source_commit="test-commit",
            source_tree_hash="test-tree",
            config_paths=CONFIG_PATHS,
        )


def _listing(session: Session, index: int, *, source_prefix: str = "p56") -> Listing:
    listing = Listing(
        source="p56-integration",
        source_listing_id=f"{source_prefix}-{index}",
        source_url=f"https://authorized.example/{source_prefix}/{index}",
        district="上海",
        submarket=GEOGRAPHIES[index % len(GEOGRAPHIES)],
        community=f"脱敏小区{index}",
        total_price=Decimal(2_300_000 + index * 20_000),
        unit_price=Decimal(45_000 + index * 100),
        area_sqm=Decimal("60"),
        bedrooms=2,
        first_seen_at=AS_OF,
        last_seen_at=AS_OF,
        status="active",
    )
    session.add(listing)
    session.flush()
    return listing


def _assessment(session: Session, listing_id: object) -> DecisionAssessment:
    assessment = DecisionAssessment(
        listing_id=listing_id,
        data_mode="sample",
        decision_version=f"p56-test-{listing_id}",
        input_fingerprint=str(listing_id).replace("-", "").ljust(64, "0")[:64],
        generated_at=AS_OF,
        current_ask=Decimal("2800000"),
        fair_value=Decimal("3000000"),
        fair_value_low=Decimal("2900000"),
        fair_value_high=Decimal("3100000"),
        value_score=Decimal("90"),
        future_score=Decimal("85"),
        liquidity_score=Decimal("88"),
        obsolescence_risk=Decimal("20"),
        structural_alpha=Decimal("12"),
        valuation_confidence="high",
        future_confidence="high",
        calibration_state="uncalibrated",
        opportunity_classification="QUALITY_AT_DISCOUNT",
        workflow_state="VIEW",
        eligibility_status="ELIGIBLE",
        confidence_gate_passed=True,
        hard_risks=[],
        positive_reasons=[
            {
                "code": "DISCOUNT",
                "text": "价格有折让",
                "source_stage": "P4",
                "evidence": {},
            }
        ],
        negative_reasons=[],
        warnings=["UNCALIBRATED_MODEL"],
        ranking_dimensions={},
        provenance={},
        valuation_version="test-valuation",
        future_assessment_version="test-future",
        baseline_version="test-baseline",
        decision_model_version="p55-decision-v1",
        configuration_version="decision-2026-09-02",
        data_version="test-data",
        data_timestamp=AS_OF,
    )
    session.add(assessment)
    session.flush()
    return assessment

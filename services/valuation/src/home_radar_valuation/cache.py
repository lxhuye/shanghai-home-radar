from __future__ import annotations

import hashlib
import uuid
from datetime import datetime

from home_radar_market.config import MarketBaselineConfig
from home_radar_models.enums import DataMode
from home_radar_models.valuation import ValuationResult
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from home_radar_valuation.config import ValuationConfig
from home_radar_valuation.engine import (
    DeterministicValuationEngine,
    ValuationEvaluation,
    build_input_fingerprint,
)
from home_radar_valuation.repository import load_listing_inputs


def evaluate_cached_listing(
    session: Session,
    listing_id: uuid.UUID,
    data_mode: DataMode,
    as_of: datetime,
    market_config: MarketBaselineConfig,
    valuation_config: ValuationConfig,
    *,
    force: bool = False,
) -> tuple[ValuationResult, bool]:
    inputs = load_listing_inputs(
        session, listing_id, data_mode, as_of, market_config, valuation_config
    )
    fingerprint = build_input_fingerprint(inputs, valuation_config, as_of)
    _advisory_lock(session, listing_id, data_mode)
    existing = session.scalar(
        select(ValuationResult).where(
            ValuationResult.listing_id == listing_id,
            ValuationResult.data_mode == data_mode.value,
            ValuationResult.input_fingerprint == fingerprint,
            ValuationResult.valuation_model_version == valuation_config.valuation_model_version,
            ValuationResult.scoring_model_version == valuation_config.scoring_model_version,
            ValuationResult.configuration_version == valuation_config.configuration_version,
        )
    )
    if existing is not None and not force:
        return existing, True
    evaluation = DeterministicValuationEngine(valuation_config).evaluate(
        inputs, as_of, input_fingerprint=fingerprint
    )
    if existing is not None:
        return existing, True
    result = _model_from_evaluation(listing_id, evaluation)
    session.add(result)
    session.flush()
    return result, False


def _model_from_evaluation(
    listing_id: uuid.UUID, evaluation: ValuationEvaluation
) -> ValuationResult:
    return ValuationResult(
        listing_id=listing_id,
        data_mode=evaluation.data_mode,
        valuation_version=evaluation.valuation_version,
        input_fingerprint=evaluation.input_fingerprint,
        calculated_at=evaluation.generated_at,
        fair_value=evaluation.fair_value,
        fair_value_low=evaluation.fair_value_low,
        fair_value_high=evaluation.fair_value_high,
        valuation_basis=evaluation.valuation_basis,
        valuation_confidence_score=evaluation.valuation_confidence_score,
        valuation_confidence=evaluation.valuation_confidence,
        transaction_support=evaluation.transaction_support,
        current_ask=evaluation.current_ask,
        ask_discount_to_fair_value=evaluation.ask_discount_to_fair_value,
        estimated_executable_price=evaluation.estimated_executable_price,
        executable_discount_to_fair_value=(evaluation.executable_discount_to_fair_value),
        value_score=evaluation.value_score,
        decision=evaluation.decision,
        comparable_count=evaluation.comparable_count,
        effective_comparable_count=evaluation.effective_comparable_count,
        baseline_level_used=evaluation.baseline_level_used,
        baseline_confidence=evaluation.baseline_confidence,
        baseline_version=evaluation.baseline_version,
        fallback_reason=evaluation.fallback_reason,
        adjustments=evaluation.adjustments,
        comparables=evaluation.comparables,
        warnings=evaluation.warnings,
        score_components=evaluation.score_components,
        price_history=evaluation.price_history,
        provenance=evaluation.provenance,
        valuation_model_version=evaluation.valuation_model_version,
        scoring_model_version=evaluation.scoring_model_version,
        configuration_version=evaluation.configuration_version,
        baseline_generated_at=evaluation.baseline_generated_at,
    )


def _advisory_lock(session: Session, listing_id: uuid.UUID, data_mode: DataMode) -> None:
    bind = session.get_bind()
    if bind.dialect.name != "postgresql":
        return
    digest = hashlib.sha256(f"valuation:{data_mode.value}:{listing_id}".encode()).digest()
    lock_key = int.from_bytes(digest[:8], byteorder="big", signed=True)
    session.execute(text("SELECT pg_advisory_xact_lock(:lock_key)"), {"lock_key": lock_key})

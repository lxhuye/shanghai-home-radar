from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Protocol

from home_radar_decision.config import DecisionConfig
from home_radar_decision.domain import DecisionEvaluation, RankedDecision


class RankableDecision(Protocol):
    @property
    def listing_id(self) -> uuid.UUID: ...

    @property
    def eligibility_status(self) -> str: ...

    @property
    def opportunity_classification(self) -> str: ...

    @property
    def valuation_confidence(self) -> str: ...

    @property
    def future_confidence(self) -> str: ...

    @property
    def liquidity_score(self) -> Decimal | None: ...

    @property
    def future_score(self) -> Decimal | None: ...

    @property
    def value_score(self) -> Decimal: ...

    @property
    def obsolescence_risk(self) -> Decimal | None: ...

    @property
    def structural_alpha(self) -> Decimal: ...


def rank_eligible(
    evaluations: list[DecisionEvaluation], config: DecisionConfig
) -> list[RankedDecision]:
    eligible = [item for item in evaluations if item.eligibility_status == "ELIGIBLE"]
    ordered = sorted(eligible, key=lambda item: decision_sort_key(item, config))
    count = len(ordered)
    return [
        RankedDecision(rank=index, eligible_count=count, evaluation=item)
        for index, item in enumerate(ordered, start=1)
    ]


def decision_sort_key(item: RankableDecision, config: DecisionConfig) -> tuple[object, ...]:
    class_priority = config.ranking.classification_priority[item.opportunity_classification]
    confidence_priority = min(
        config.ranking.confidence_priority[item.valuation_confidence],
        config.ranking.confidence_priority[item.future_confidence],
    )
    return (
        -class_priority,
        -confidence_priority,
        -(item.liquidity_score or 0),
        -(item.future_score or 0),
        -item.value_score,
        item.obsolescence_risk if item.obsolescence_risk is not None else 101,
        -item.structural_alpha,
        str(item.listing_id),
    )

from __future__ import annotations

import uuid
from types import SimpleNamespace
from typing import cast

import home_radar_api.routes.decision as decision_route
import pytest
from home_radar_decision.config import DecisionConfig


def test_rank_context_excludes_inactive_listings(monkeypatch: pytest.MonkeyPatch) -> None:
    active_id = uuid.uuid4()
    inactive_id = uuid.uuid4()
    assessments = [
        SimpleNamespace(listing_id=active_id, eligibility_status="ELIGIBLE", score=80),
        SimpleNamespace(listing_id=inactive_id, eligibility_status="ELIGIBLE", score=99),
    ]
    db = _FakeDb([active_id])
    config = cast(
        DecisionConfig,
        SimpleNamespace(decision_model_version="decision-v1", configuration_version="config-v1"),
    )
    monkeypatch.setattr(
        decision_route,
        "latest_decision_assessments",
        lambda *_args, **_kwargs: assessments,
    )
    monkeypatch.setattr(decision_route, "_mode", lambda: SimpleNamespace(value="sample"))
    monkeypatch.setattr(
        decision_route,
        "decision_sort_key",
        lambda item, _config: (-item.score,),
    )

    rank_by_id, eligible_count, latest = decision_route._rank_context(db, config)  # type: ignore[arg-type]

    assert rank_by_id == {active_id: 1}
    assert eligible_count == 1
    assert [item.listing_id for item in latest] == [active_id]


class _FakeDb:
    def __init__(self, active_ids: list[uuid.UUID]) -> None:
        self.active_ids = active_ids

    def scalars(self, _statement: object) -> list[uuid.UUID]:
        return self.active_ids

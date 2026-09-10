from __future__ import annotations

import uuid
from datetime import UTC, datetime

import home_radar_valuation.materializer as valuation_materializer
import pytest
from home_radar_models.enums import DataMode
from home_radar_valuation.aggregation import InsufficientValuationEvidenceError

AS_OF = datetime(2026, 9, 2, tzinfo=UTC)


def test_batch_materializer_separates_insufficient_data_from_failures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _FakeSession([uuid.uuid4(), uuid.uuid4(), uuid.uuid4()])
    calls = 0

    def evaluate(*_args: object, **_kwargs: object) -> tuple[object, bool]:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise InsufficientValuationEvidenceError("no comparable evidence")
        if calls == 3:
            raise RuntimeError("isolated failure")
        return object(), True

    monkeypatch.setattr(valuation_materializer, "evaluate_cached_listing", evaluate)
    result = valuation_materializer.materialize_active_listings(
        session,
        DataMode.SAMPLE,
        AS_OF,
        object(),
        object(),
    )

    assert result.evaluated == 1
    assert result.cache_hits == 1
    assert result.skipped_insufficient_data == 1
    assert "InsufficientValuationEvidenceError" in result.skips[0]
    assert result.failed == 1
    assert "RuntimeError" in result.failures[0]
    assert session.commits == 1
    assert session.rollbacks == 2


class _FakeSession:
    def __init__(self, listing_ids: list[uuid.UUID]) -> None:
        self.listing_ids = listing_ids
        self.commits = 0
        self.rollbacks = 0

    def scalars(self, _statement: object) -> list[uuid.UUID]:
        return self.listing_ids

    def commit(self) -> None:
        self.commits += 1

    def rollback(self) -> None:
        self.rollbacks += 1

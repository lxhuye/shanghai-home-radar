from __future__ import annotations

from typing import cast

from home_radar_collector.service import CollectorService
from home_radar_models.enums import DataMode
from sqlalchemy.orm import Session


def _service(*, minimum: int = 1, ratio: float = 0.5) -> CollectorService:
    return CollectorService(
        cast(Session, object()),
        data_mode=DataMode.LIVE,
        min_complete_items=minimum,
        min_complete_count_ratio=ratio,
    )


def test_coverage_guard_rejects_below_absolute_minimum() -> None:
    reason, ratio = _service(minimum=10)._coverage_guard_reason(
        current_count=9, previous_count=None
    )

    assert reason == "below_minimum_item_count"
    assert ratio is None


def test_coverage_guard_rejects_large_drop_from_previous_complete_run() -> None:
    reason, ratio = _service(ratio=0.5)._coverage_guard_reason(current_count=49, previous_count=100)

    assert reason == "unexpected_item_count_drop"
    assert ratio == 0.49


def test_coverage_guard_accepts_count_at_threshold() -> None:
    reason, ratio = _service(ratio=0.5)._coverage_guard_reason(current_count=50, previous_count=100)

    assert reason is None
    assert ratio == 0.5

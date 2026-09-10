from __future__ import annotations

import subprocess
import uuid
from types import SimpleNamespace

import pytest
from home_radar_decision.real_world_validation import (
    RealWorldValidationError,
    _validation_listing_snapshot,
)

from scripts import p57_set_a


def test_custom_blind_snapshot_blocks_model_fields_and_source_url() -> None:
    listing = SimpleNamespace(id=uuid.uuid4())
    snapshot = _validation_listing_snapshot(
        {"blind_listing_snapshot": {"district": "徐汇", "price_wan": 280}}, listing
    )

    assert snapshot == {
        "listing_id": str(listing.id),
        "district": "徐汇",
        "price_wan": 280,
    }
    assert "source_url" not in snapshot

    with pytest.raises(RealWorldValidationError, match="leaks model fields"):
        _validation_listing_snapshot({"blind_listing_snapshot": {"value_score": 90}}, listing)


def test_set_a_cutoff_is_explicit_and_timezone_aware() -> None:
    cutoff = p57_set_a.parse_input_cutoff("2026-09-02T09:55:56+08:00")

    assert cutoff.isoformat() == "2026-09-02T01:55:56+00:00"
    with pytest.raises(ValueError, match="timezone"):
        p57_set_a.parse_input_cutoff("2026-09-02T09:55:56")


def test_git_value_uses_explicit_commit_when_git_is_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def unavailable(*args: object, **kwargs: object) -> object:
        raise FileNotFoundError

    monkeypatch.setattr(subprocess, "run", unavailable)
    monkeypatch.setenv("SHR_SOURCE_COMMIT", "a" * 40)

    assert p57_set_a.git_value("rev-parse", "HEAD") == "a" * 40

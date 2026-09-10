from __future__ import annotations

import pytest
from home_radar_collector.feed import (
    CanonicalFeedEnvelope,
    ProviderCapabilities,
    assess_coverage,
)
from home_radar_models.enums import CrawlCompleteness
from pydantic import ValidationError


def envelope(**overrides: object) -> CanonicalFeedEnvelope:
    payload: dict[str, object] = {
        "schema_version": "1.0",
        "source_id": "partner_feed",
        "scope": {"city": "shanghai", "filters": {}},
        "completeness": "complete",
        "coverage": {
            "reported_total": 0,
            "items_returned": 0,
        },
        "metadata": {},
        "items": [],
    }
    payload.update(overrides)
    return CanonicalFeedEnvelope.model_validate(payload)


def test_transport_success_does_not_imply_coverage_complete() -> None:
    assessment = assess_coverage(
        envelope(coverage=None),
        capabilities=ProviderCapabilities(),
        allow_complete_without_coverage=False,
    )

    assert assessment.completeness is CrawlCompleteness.PARTIAL
    assert assessment.coverage_complete is False
    assert assessment.reasons == ("coverage_missing",)


def test_explicit_coverage_override_is_auditable() -> None:
    assessment = assess_coverage(
        envelope(coverage=None),
        capabilities=ProviderCapabilities(),
        allow_complete_without_coverage=True,
    )

    assert assessment.completeness is CrawlCompleteness.COMPLETE
    assert assessment.coverage_complete is True


@pytest.mark.parametrize(
    "payload",
    [
        {"metadata": {"api_token": "secret"}},
        {"items": [{"listing_id": "a", "authorization": "secret"}]},
        {"scope": {"city": "shanghai", "future_boundary": "unsafe"}},
    ],
)
def test_sensitive_fields_and_unknown_scope_boundaries_fail_closed(
    payload: dict[str, object],
) -> None:
    with pytest.raises(ValidationError):
        envelope(**payload)

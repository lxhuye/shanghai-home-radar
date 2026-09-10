import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from scripts.prepare_public_monitor_pilot import SOURCE_ID
from scripts.recommendation_future import FutureEvidenceStore


def factor(**updates: object) -> dict:
    return {
        "factor": "rental_demand",
        "scope_type": "district",
        "district": "徐汇",
        "source": "public_statistics",
        "source_record_id": "1",
        "observed_at": "2025-01-01T00:00:00Z",
        "current_score": 60,
        "future_score": 65,
        "current_value": 123,
        "unit": "count",
        "confidence": 0.5,
        "explanation": "Submitted model using source observations, not calibrated.",
        "provenance": provenance(),
    } | updates


def provenance(**updates: object) -> dict:
    return {
        "url": "https://example.org/statistics",
        "publisher": "Public statistics",
        "published_at": "2025-01-01T00:00:00Z",
        "retrieved_at": "2025-01-02T00:00:00Z",
        "synthetic": False,
    } | updates


def payload(**updates: object) -> dict:
    return {
        "data_mode": "sample",
        "verified_by": "reviewer",
        "factor_observations": [factor()],
    } | updates


def center(**updates: object) -> dict:
    return {
        "name": "Measured center",
        "category": "business",
        "source": "survey",
        "source_record_id": "center1",
        "coordinates": {"lon": 121.4, "lat": 31.1},
        "current_employment_weight": 1000,
        "future_employment_weight": 1100,
        "effective_from": "2025-01-01T00:00:00Z",
        "source_timestamp": "2025-01-01T00:00:00Z",
        "confidence": 0.7,
        "provenance": provenance(
            measurement_basis="observed_employment", coordinates_verified=True
        ),
    } | updates


def test_empty_then_idempotent_provenance_preserving_intake(tmp_path: Path) -> None:
    store = FutureEvidenceStore(tmp_path)
    item = {"listing_id": "S1", "district": "徐汇"}
    assert store.inputs(item, datetime.now(UTC)) == ((), (), ())
    assert store.latest_timestamp() is None
    before = store.fingerprint()
    assert store.import_evidence(payload())["imported"] == 1
    assert store.fingerprint() != before
    after = store.fingerprint()
    assert store.import_evidence(payload())["duplicates"] == 1
    assert store.fingerprint() == after
    factors, _, _ = store.inputs(item, datetime.now(UTC))
    assert factors[0].confidence == Decimal("0.5")
    assert factors[0].metadata["current_value"] == Decimal(123)
    assert factors[0].metadata["evidence_kind"] == "supplied_modeled_score"
    assert factors[0].metadata["provenance"]["url"] == "https://example.org/statistics"
    assert store.inputs(item, datetime(2025, 1, 1, tzinfo=UTC)) == ((), (), ())
    assert store.inputs({**item, "district": "闵行"}, datetime.now(UTC)) == ((), (), ())


@pytest.mark.parametrize(
    "change",
    [
        {"data_mode": "demo"},
        {"verified_by": " "},
        {"factor_observations": [factor(provenance=provenance(synthetic=True))]},
        {"factor_observations": [factor(provenance=provenance(url="http://example.org"))]},
        {
            "factor_observations": [
                factor(provenance=provenance(url="https://user:pass@example.org"))
            ]
        },
        {
            "factor_observations": [
                factor(provenance=provenance(url="https://example.org?token=secret"))
            ]
        },
        {
            "factor_observations": [
                factor(
                    provenance=provenance(
                        retrieved_at=(datetime.now(UTC) + timedelta(days=1)).isoformat()
                    )
                )
            ]
        },
        {"factor_observations": [factor(observed_at="2025-01-01")]},
        {"factor_observations": [factor(current_score="NaN")]},
    ],
)
def test_invalid_batch_never_writes(tmp_path: Path, change: dict) -> None:
    store = FutureEvidenceStore(tmp_path)
    with pytest.raises(ValueError):
        store.import_evidence(payload(**change))
    assert not store.path.exists()


def test_conflicting_batch_atomic_and_scoped_latest_selection(tmp_path: Path) -> None:
    store = FutureEvidenceStore(tmp_path)
    store.import_evidence(payload())
    before = store.path.read_bytes()
    with pytest.raises(ValueError, match="conflict"):
        store.import_evidence(
            payload(factor_observations=[factor(source_record_id="2"), factor(current_score=20)])
        )
    assert store.path.read_bytes() == before
    listing_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"{SOURCE_ID}/S1"))
    scoped = factor(
        source_record_id="scoped",
        scope_type="listing",
        submarket="长桥",
        community="测试",
        listing_id=listing_id,
        future_score=80,
    )
    store.import_evidence(payload(factor_observations=[scoped]))
    item = {"listing_id": "S1", "district": "徐汇", "submarket": "长桥", "community": "测试"}
    assert store.inputs(item, datetime.now(UTC))[0][0].future_score == Decimal(80)
    assert store.inputs({**item, "listing_id": "S2"}, datetime.now(UTC))[0][
        0
    ].future_score == Decimal(65)


def test_employment_requires_measurement_and_verified_positions(tmp_path: Path) -> None:
    store = FutureEvidenceStore(tmp_path)
    for invalid in [
        provenance(),
        provenance(measurement_basis="ordinal_planning", coordinates_verified=True),
        provenance(measurement_basis="observed_employment"),
    ]:
        with pytest.raises(ValueError, match="employment"):
            store.import_evidence(
                payload(factor_observations=[], employment_centers=[center(provenance=invalid)])
            )
    store.import_evidence(payload(factor_observations=[], employment_centers=[center()]))
    centers = store.inputs({"listing_id": "S1"}, datetime.now(UTC))[1]
    assert centers[0].current_employment_weight == Decimal(1000)
    assert centers[0].source == "https://example.org/statistics"


def test_project_expiry_and_source_properties(tmp_path: Path) -> None:
    store = FutureEvidenceStore(tmp_path)
    project = center()
    for key in [
        "category",
        "current_employment_weight",
        "future_employment_weight",
        "source_timestamp",
    ]:
        project.pop(key)
    project.update(
        project_type="transport",
        status="approved",
        source_date="2025-01-01T00:00:00Z",
        expected_completion="2030-01-01",
        probability=0.6,
        district="徐汇",
    )
    store.import_evidence(payload(factor_observations=[], future_projects=[project]))
    selected = store.inputs({"listing_id": "S1", "district": "徐汇"}, datetime.now(UTC))[2]
    assert selected[0].expected_completion == datetime(2030, 1, 1, tzinfo=UTC)
    assert selected[0].probability == Decimal("0.6")
    store2 = FutureEvidenceStore(tmp_path / "expired")
    store2.import_evidence(
        payload(factor_observations=[factor(effective_to="2025-02-01T00:00:00Z")])
    )
    assert store2.inputs({"listing_id": "S1", "district": "徐汇"}, datetime.now(UTC)) == (
        (),
        (),
        (),
    )

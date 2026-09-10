from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import yaml

from scripts import build_district_factor_evidence as builder
from scripts import import_future_evidence as importer

CUTOFF = datetime(2026, 9, 2, 9, 55, 56, tzinfo=UTC)


def test_declared_source_contract_builds_two_factors_for_all_districts(
    district_statistics: dict[str, Any],
) -> None:
    observations = builder.build_observations(district_statistics, cutoff=CUTOFF)

    assert len(observations) == 32
    assert {row["factor"] for row in observations} == {
        "supply_scarcity",
        "buyer_pool_depth",
    }
    assert len({row["district"] for row in observations}) == 16
    assert all(
        str(row["provenance"]["url"]).startswith("https://tjj.sh.gov.cn/") for row in observations
    )
    assert all(row["provenance"]["synthetic"] is False for row in observations)


def test_percentile_transform_is_directional_and_bounded() -> None:
    values = {"low": Decimal("1"), "mid": Decimal("2"), "high": Decimal("3")}

    upward = builder.percentiles(values, higher_is_better=True)
    inverse = builder.percentiles(values, higher_is_better=False)

    assert upward == {"low": Decimal("0.00"), "mid": Decimal("50.00"), "high": Decimal("100.00")}
    assert inverse == {"low": Decimal("100.00"), "mid": Decimal("50.00"), "high": Decimal("0.00")}


def test_generated_yaml_passes_the_evidence_import_contract(
    tmp_path: Path, district_statistics: dict[str, Any]
) -> None:
    output = tmp_path / "district_factors.yaml"
    raw_path = tmp_path / "synthetic_statistics.yaml"
    raw_path.write_text(yaml.safe_dump(district_statistics), encoding="utf-8")

    code = builder.main(
        [
            "--input",
            str(raw_path),
            "--cutoff",
            "2026-09-02T09:55:56Z",
            "--output",
            str(output),
        ]
    )

    assert code == 0
    evidence = importer.load_evidence_file(output, cutoff=CUTOFF)
    assert len(evidence.records) == 32
    assert {record.table for record in evidence.records} == {"future_factor_observation"}


def test_rejects_a_source_published_after_cutoff(district_statistics: dict[str, Any]) -> None:
    raw = district_statistics
    raw["sources"]["population_2024"]["published_at"] = "2026-09-03T00:00:00Z"

    try:
        builder.build_observations(raw, cutoff=CUTOFF)
    except ValueError as exc:
        assert "published after cutoff" in str(exc)
    else:
        raise AssertionError("look-ahead source was accepted")

"""Test-only generated inputs. Never import these fixtures into research or LIVE data."""

from __future__ import annotations

from typing import Any

import pytest


@pytest.fixture
def district_statistics() -> dict[str, Any]:
    """Exercise the source contract without redistributing real statistical tables.

    The builder requires SAMPLE and an official-domain-shaped URL. These are deliberately
    fake paths, publisher and numbers, never fetched. Outputs exist only in pytest's tmp_path.
    Tests check declared provenance validation, not the truth of a publisher's declaration.
    """
    names = (
        "population_2022",
        "population_2023",
        "population_2024",
        "housing_2023",
        "housing_2024",
    )
    return {
        "data_mode": "sample",
        "publisher": "SYNTHETIC TEST FIXTURE - NOT OFFICIAL DATA",
        "retrieved_at": "2026-04-01T00:00:00Z",
        "sources": {
            name: {
                "table": f"SYNTHETIC TEST TABLE {name}",
                "url": f"https://tjj.sh.gov.cn/__test_fixture__/{name}",
                "publication_url": "https://tjj.sh.gov.cn/__test_fixture__/publication",
                "published_at": "2026-03-01T00:00:00Z",
            }
            for name in names
        },
        "districts": [
            {
                "district": "徐汇" if index == 0 else f"test-district-{index:02d}",
                "population_10k": {2022: 100 + index, 2024: 102 + index * 2},
                "population_density_2024": 1000 + index * 100,
                "residential_floor_area_10k_sqm": {2023: 200 + index, 2024: 204 + index * 3},
            }
            for index in range(16)
        ],
    }

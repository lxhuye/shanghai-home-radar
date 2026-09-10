#!/usr/bin/env python3
"""Build transparent district-level P5 proxy evidence from official Shanghai tables.

This builder does not invent raw observations. It converts published district statistics into
cross-sectional percentile scores with a frozen formula:

* supply_scarcity: inverse percentile of 2023-2024 residential floor-area growth;
* buyer_pool_depth current: percentile of 2024 resident-population density;
* buyer_pool_depth future: equal-weight mean of density percentile and 2022-2024 population
  change percentile.

These are low-confidence calibration proxies, not transaction-calibrated forecasts. The output
retains every raw value, source URL, formula, and universe size in metadata.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Any

import yaml

sys.path.insert(0, str(Path(__file__).parent.parent))

BUILDER_VERSION = "district-factor-evidence-v1"
EXPECTED_DISTRICT_COUNT = 16
BUYER_POOL_FORMULA = "mean(density_2024_percentile,population_change_2022_2024_percentile)"


def parse_cutoff(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("--cutoff must include a timezone")
    return parsed.astimezone(UTC)


def _decimal(value: Any, *, label: str) -> Decimal:
    try:
        return Decimal(str(value))
    except Exception as exc:
        raise ValueError(f"{label} must be numeric") from exc


def _year_value(values: Any, year: int, *, label: str) -> Decimal:
    if not isinstance(values, Mapping):
        raise ValueError(f"{label} must be a year mapping")
    value = values.get(year, values.get(str(year)))
    if value is None:
        raise ValueError(f"{label} is missing {year}")
    return _decimal(value, label=f"{label}.{year}")


def percentiles(values: Mapping[str, Decimal], *, higher_is_better: bool) -> dict[str, Decimal]:
    """Return 0-100 average-rank percentiles across the complete district universe."""
    if len(values) < 2:
        raise ValueError("percentiles require at least two districts")
    ordered = sorted(values.items(), key=lambda item: (item[1], item[0]))
    by_value: dict[Decimal, list[int]] = {}
    for index, (_, value) in enumerate(ordered):
        by_value.setdefault(value, []).append(index)
    denominator = Decimal(len(values) - 1)
    result: dict[str, Decimal] = {}
    for district, value in values.items():
        positions = by_value[value]
        rank = Decimal(sum(positions)) / Decimal(len(positions))
        score = rank / denominator * Decimal("100")
        if not higher_is_better:
            score = Decimal("100") - score
        result[district] = score.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return result


def build_observations(raw: Mapping[str, Any], *, cutoff: datetime) -> list[dict[str, Any]]:
    if raw.get("data_mode") != "sample":
        raise ValueError("district statistics must be sample data")
    rows = raw.get("districts")
    sources = raw.get("sources")
    if not isinstance(rows, Sequence) or isinstance(rows, str):
        raise ValueError("districts must be an array")
    if len(rows) != EXPECTED_DISTRICT_COUNT:
        raise ValueError(f"district universe must contain {EXPECTED_DISTRICT_COUNT} rows")
    if not isinstance(sources, Mapping):
        raise ValueError("sources must be a mapping")

    source_names = (
        "population_2022",
        "population_2023",
        "population_2024",
        "housing_2023",
        "housing_2024",
    )
    parsed_sources: dict[str, dict[str, Any]] = {}
    for name in source_names:
        source = sources.get(name)
        if not isinstance(source, Mapping):
            raise ValueError(f"missing source {name}")
        published_at = parse_cutoff(str(source.get("published_at")))
        if published_at > cutoff:
            raise ValueError(f"source {name} was published after cutoff")
        if not str(source.get("url") or "").startswith("https://tjj.sh.gov.cn/"):
            raise ValueError(f"source {name} must use the official Shanghai Statistics URL")
        parsed_sources[name] = {**source, "published_at": published_at}

    prepared: dict[str, dict[str, Any]] = {}
    for value in rows:
        if not isinstance(value, Mapping):
            raise ValueError("every district row must be a mapping")
        district = str(value.get("district") or "").strip()
        if not district or district in prepared:
            raise ValueError("district names must be non-empty and unique")
        population_2022 = _year_value(value.get("population_10k"), 2022, label=district)
        population_2024 = _year_value(value.get("population_10k"), 2024, label=district)
        housing_2023 = _year_value(
            value.get("residential_floor_area_10k_sqm"), 2023, label=district
        )
        housing_2024 = _year_value(
            value.get("residential_floor_area_10k_sqm"), 2024, label=district
        )
        if min(population_2022, population_2024, housing_2023, housing_2024) <= 0:
            raise ValueError(f"{district} raw values must be positive")
        prepared[district] = {
            "official_name": str(value.get("official_name") or district),
            "population_2022": population_2022,
            "population_2024": population_2024,
            "density_2024": _decimal(
                value.get("population_density_2024"), label=f"{district}.density"
            ),
            "housing_2023": housing_2023,
            "housing_2024": housing_2024,
            "population_change_pct": (
                (population_2024 / population_2022 - Decimal("1")) * Decimal("100")
            ),
            "housing_growth_pct": ((housing_2024 / housing_2023 - Decimal("1")) * Decimal("100")),
        }

    supply_scores = percentiles(
        {name: value["housing_growth_pct"] for name, value in prepared.items()},
        higher_is_better=False,
    )
    density_scores = percentiles(
        {name: value["density_2024"] for name, value in prepared.items()},
        higher_is_better=True,
    )
    population_trend_scores = percentiles(
        {name: value["population_change_pct"] for name, value in prepared.items()},
        higher_is_better=True,
    )

    latest_publication = max(source["published_at"] for source in parsed_sources.values())
    retrieved_at = parse_cutoff(str(raw.get("retrieved_at")))
    publisher = str(raw.get("publisher") or "").strip()
    if not publisher:
        raise ValueError("publisher is required")
    observations: list[dict[str, Any]] = []
    for district, values in prepared.items():
        common_metadata = {
            "builder_version": BUILDER_VERSION,
            "universe": "all_16_shanghai_districts",
            "universe_size": len(prepared),
            "official_district_name": values["official_name"],
        }
        supply_growth = values["housing_growth_pct"].quantize(
            Decimal("0.0001"), rounding=ROUND_HALF_UP
        )
        supply_score = supply_scores[district]
        observations.append(
            {
                "factor": "supply_scarcity",
                "scope_type": "district",
                "district": district,
                "current_score": supply_score,
                "future_score": supply_score,
                "observed_at": latest_publication.isoformat(),
                "effective_from": latest_publication.isoformat(),
                "source_timestamp": latest_publication.isoformat(),
                "source": "shanghai-statistical-yearbook-district",
                "source_record_id": f"{district}-housing-stock-growth-2023-2024-v1",
                "confidence": Decimal("0.55"),
                "explanation": (
                    "Inverse average-rank percentile of 2023-2024 residential floor-area "
                    "growth across all 16 Shanghai districts. The current observation is "
                    "carried forward unchanged as a low-confidence persistence proxy, not a "
                    "new-supply forecast."
                ),
                "metadata": {
                    **common_metadata,
                    "formula": "100 - percentile_rank(residential_stock_growth_2023_2024)",
                    "residential_floor_area_2023_10k_sqm": str(values["housing_2023"]),
                    "residential_floor_area_2024_10k_sqm": str(values["housing_2024"]),
                    "residential_stock_growth_pct": str(supply_growth),
                },
                "provenance": _provenance(
                    publisher,
                    retrieved_at,
                    parsed_sources["housing_2024"],
                    comparison=parsed_sources["housing_2023"],
                ),
            }
        )

        current_buyer = density_scores[district]
        trend_buyer = population_trend_scores[district]
        future_buyer = ((current_buyer + trend_buyer) / Decimal("2")).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )
        population_change = values["population_change_pct"].quantize(
            Decimal("0.0001"), rounding=ROUND_HALF_UP
        )
        observations.append(
            {
                "factor": "buyer_pool_depth",
                "scope_type": "district",
                "district": district,
                "current_score": current_buyer,
                "future_score": future_buyer,
                "observed_at": latest_publication.isoformat(),
                "effective_from": latest_publication.isoformat(),
                "source_timestamp": latest_publication.isoformat(),
                "source": "shanghai-statistical-yearbook-district",
                "source_record_id": f"{district}-population-depth-2022-2024-v1",
                "confidence": Decimal("0.50"),
                "explanation": (
                    "Current score is the 2024 population-density percentile. Future score is "
                    "the equal-weight mean of that percentile and the 2022-2024 resident-"
                    "population-change percentile across all 16 Shanghai districts. This is a "
                    "low-confidence demand-depth proxy, not observed buyer transactions."
                ),
                "metadata": {
                    **common_metadata,
                    "formula": BUYER_POOL_FORMULA,
                    "population_2022_10k": str(values["population_2022"]),
                    "population_2024_10k": str(values["population_2024"]),
                    "population_density_2024_people_per_sq_km": str(values["density_2024"]),
                    "population_change_2022_2024_pct": str(population_change),
                    "density_percentile": str(current_buyer),
                    "population_change_percentile": str(trend_buyer),
                },
                "provenance": _provenance(
                    publisher,
                    retrieved_at,
                    parsed_sources["population_2024"],
                    comparison=parsed_sources["population_2022"],
                ),
            }
        )
    return observations


def _provenance(
    publisher: str,
    retrieved_at: datetime,
    primary: Mapping[str, Any],
    *,
    comparison: Mapping[str, Any],
) -> dict[str, Any]:
    published_at = primary["published_at"]
    assert isinstance(published_at, datetime)
    return {
        "url": str(primary["url"]),
        "publisher": publisher,
        "published_at": published_at.isoformat(),
        "retrieved_at": retrieved_at.isoformat(),
        "table": str(primary["table"]),
        "publication_url": str(primary["publication_url"]),
        "comparison_url": str(comparison["url"]),
        "comparison_table": str(comparison["table"]),
        "builder_version": BUILDER_VERSION,
        "synthetic": False,
    }


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--cutoff", required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args(argv)


def _yaml_safe(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _yaml_safe(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_yaml_safe(item) for item in value]
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    return value


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        cutoff = parse_cutoff(args.cutoff)
        raw = yaml.safe_load(args.input.read_text(encoding="utf-8"))
        if not isinstance(raw, Mapping):
            raise ValueError("input must be a YAML mapping")
        observations = build_observations(raw, cutoff=cutoff)
    except (OSError, ValueError, yaml.YAMLError) as exc:
        print(json.dumps({"status": "error", "error": str(exc)}, ensure_ascii=False))
        return 2
    output = {
        "data_mode": "sample",
        "cutoff": cutoff.isoformat(),
        "factor_observations": observations,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        yaml.safe_dump(_yaml_safe(output), allow_unicode=True, sort_keys=False), encoding="utf-8"
    )
    print(
        json.dumps(
            {"status": "ok", "output": str(args.output), "observations": len(observations)},
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

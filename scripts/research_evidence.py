"""Public evidence adapter. Never imports historic listing prices or invented inputs."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import yaml
from home_radar_forecasting.domain import FactorEvidence, FutureProjectInput

from scripts.geocode_listings import enrich_items
from scripts.import_future_evidence import build_factor_observation, build_future_project


class ResearchEvidence:
    def __init__(self, directory: Path) -> None:
        self.directory = directory
        self.names = (
            "transport_stations.osm.yaml",
            "district_factors.yearbook_2022_2024.yaml",
            "browser_geocodes.json",
        )
        self._loaded: tuple[str, datetime] | None = None
        self._factors: list[dict[str, Any]] = []
        self._projects: list[tuple[dict[str, Any], FutureProjectInput]] = []

    def fingerprint(self) -> dict[str, str]:
        return {
            name: hashlib.sha256((self.directory / name).read_bytes()).hexdigest()
            for name in self.names
            if (self.directory / name).exists()
        }

    def enrich(
        self, items: list[dict[str, Any]], as_of: datetime
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        path = self.directory / "browser_geocodes.json"
        cache = json.loads(path.read_text()) if path.exists() else {"entries": {}}
        cache["entries"] = {
            key: entry
            for key, entry in cache["entries"].items()
            if entry.get("retrieved_at")
            and datetime.fromisoformat(entry["retrieved_at"]) <= as_of
            and (entry.get("answer") or {}).get("datum") == "WGS84"
        }
        enriched, summary = enrich_items(items, cache, accept_levels=("residential",))
        summary["files"] = self.fingerprint()
        return enriched, summary

    def future(
        self, item: dict[str, Any], as_of: datetime
    ) -> tuple[tuple[FactorEvidence, ...], tuple[FutureProjectInput, ...]]:
        version = (json.dumps(self.fingerprint(), sort_keys=True), as_of)
        if version != self._loaded:
            factors: list[dict[str, Any]] = []
            projects: list[tuple[dict[str, Any], FutureProjectInput]] = []
            for name in self.names[:2]:
                path = self.directory / name
                if not path.exists():
                    continue
                document = yaml.safe_load(path.read_text())
                if document.get("data_mode") != "sample":
                    raise ValueError("browser evidence must be public research sample")
                if datetime.fromisoformat(str(document["cutoff"])) > as_of:
                    continue
                for index, raw in enumerate(document.get("factor_observations", [])):
                    record = build_factor_observation(
                        raw, data_mode="sample", cutoff=as_of, index=index
                    ).values
                    if self.active(record, as_of):
                        factors.append(record)
                for index, raw in enumerate(document.get("future_projects", [])):
                    record = build_future_project(
                        raw, data_mode="sample", cutoff=as_of, index=index
                    ).values
                    if not self.active(record, as_of):
                        continue
                    coords = raw["coordinates"]
                    # Accept existing stations only; do not infer future projects.
                    if record["project_type"] != "transport" or record["status"] != "current":
                        raise ValueError("unsupported transport evidence")
                    projects.append(
                        (
                            record,
                            FutureProjectInput(
                                name=record["name"],
                                project_type="transport",
                                status="current",
                                longitude=Decimal(str(coords["lon"])),
                                latitude=Decimal(str(coords["lat"])),
                                expected_completion=None,
                                source=record["provenance"]["url"],
                                source_date=record["source_date"],
                                confidence=record["confidence"],
                            ),
                        )
                    )
            self._factors, self._projects, self._loaded = factors, projects, version
        selected = [
            record
            for record in self._factors
            if record["scope_type"] == "district" and record["district"] == item["district"]
        ]
        factors_result = tuple(
            FactorEvidence(
                factor=record["factor"],
                current_score=record["current_score"],
                future_score=record["future_score"],
                confidence=record["confidence"],
                source=record["source"],
                source_timestamp=record["source_timestamp"],
                data_mode="sample",
                explanation=record["explanation"],
                metadata={
                    **record["observation_metadata"],
                    "provenance": record["provenance"],
                    "evidence_kind": "regional_proxy",
                },
            )
            for record in selected
        )
        project_result = tuple(
            project
            for record, project in self._projects
            if all(
                not record.get(field) or record[field] == item.get(field)
                for field in ("district", "submarket", "community")
            )
        )
        return factors_result, project_result

    @staticmethod
    def active(record: dict[str, Any], as_of: datetime) -> bool:
        return datetime.fromisoformat(record["provenance"]["retrieved_at"]) <= as_of and (
            record.get("effective_to") is None or record["effective_to"] > as_of
        )

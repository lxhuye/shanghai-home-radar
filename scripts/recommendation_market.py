"""Reviewed provider aggregates; these never change browser-feed coverage claims."""

from __future__ import annotations

from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any, Literal

from home_radar_market.config import MarketBaselineConfig
from home_radar_market.liquidity import LiquidityInputs, calculate_liquidity
from pydantic import BaseModel, ConfigDict, Field, model_validator

from scripts.recommendation_evidence import EvidenceModel, digest, write_json


class MarketAggregate(EvidenceModel):
    source_id: str = Field(pattern=r"^[a-zA-Z0-9_-]{1,50}$")
    source_record_id: str = Field(min_length=1, max_length=255)
    window_start: datetime
    window_end: datetime
    coverage_complete: bool = Field(strict=True)
    district: str = Field(min_length=1, max_length=80)
    submarket: str | None = Field(default=None, min_length=1, max_length=120)
    community: str | None = Field(default=None, min_length=1, max_length=160)
    active_inventory: int | None = Field(default=None, strict=True, ge=0)
    new_listings: int | None = Field(default=None, strict=True, ge=0)
    listing_exits: int | None = Field(default=None, strict=True, ge=0)
    median_days_on_market: float | None = Field(
        default=None, strict=True, ge=0, allow_inf_nan=False
    )
    price_cut_ratio: float | None = Field(
        default=None, strict=True, ge=0, le=1, allow_inf_nan=False
    )
    relisting_rate: float | None = Field(default=None, strict=True, ge=0, le=1, allow_inf_nan=False)
    buyer_pool_ratio: float | None = Field(
        default=None, strict=True, ge=0, le=1, allow_inf_nan=False
    )

    @model_validator(mode="after")
    def aggregate_contract(self) -> MarketAggregate:
        if self.window_start.utcoffset() is None or self.window_end.utcoffset() is None:
            raise ValueError("aggregate window timestamps require timezones")
        if self.window_end > self.observed_at:
            raise ValueError("aggregate window cannot end after observation")
        if self.window_end - self.window_start != timedelta(days=30):
            raise ValueError("aggregate window must be exactly 30 days")
        if not self.coverage_complete:
            raise ValueError("explicit provider complete-coverage attestation required")
        if self.community and not self.submarket:
            raise ValueError("community scope requires submarket")
        return self


class MarketBatch(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    data_mode: Literal["sample"]
    verified_by: str = Field(min_length=1, max_length=120)
    records: list[dict[str, Any]] = Field(min_length=1, max_length=2000)


class MarketEvidenceStore:
    def __init__(self, directory: Path) -> None:
        self.path = Path(directory) / "market_evidence.json"

    def _read(self) -> list[dict[str, Any]]:
        import json

        return json.loads(self.path.read_text())["records"] if self.path.exists() else []

    def fingerprint(self) -> str:
        return digest(self._read())

    def latest_timestamp(self) -> datetime | None:
        return max(
            (datetime.fromisoformat(row["recorded_at"]) for row in self._read()), default=None
        )

    def import_evidence(self, payload: dict[str, Any]) -> dict[str, Any]:
        batch = MarketBatch.model_validate(payload)
        records = [
            MarketAggregate.model_validate({**raw, "verified_by": batch.verified_by}).model_dump(
                mode="json"
            )
            for raw in batch.records
        ]
        stored = self._read()
        known = {(row["source_id"], row["source_record_id"]): row for row in stored}
        added = []
        duplicates = 0
        for value in records:
            evidence_id = digest(value)
            key = (value["source_id"], value["source_record_id"])
            if key in known:
                if known[key]["evidence_id"] != evidence_id:
                    raise ValueError("aggregate natural key conflict; entire batch rejected")
                duplicates += 1
                continue
            row = {
                **value,
                "evidence_id": evidence_id,
                "recorded_at": datetime.now(UTC).isoformat(),
                "evidence_kind": "provider_reported_complete_aggregate",
                "verification_method": "human_supplied_provenance_not_independently_verified",
            }
            added.append(row)
            known[key] = row
        if added:
            write_json(self.path, {"records": stored + added})
        return {
            "imported": len(added),
            "duplicates": duplicates,
            "total": len(stored) + len(added),
            "evidence_kind": "provider_reported_complete_aggregate",
        }

    def liquidity(
        self,
        item: dict[str, Any],
        as_of: datetime,
        config: MarketBaselineConfig,
        *,
        baseline_confidence: Decimal = Decimal("0.42"),
    ) -> dict[str, Any] | None:
        if as_of.utcoffset() is None:
            raise ValueError("assessment cutoff requires timezone")
        if not baseline_confidence.is_finite() or not 0 <= baseline_confidence <= 1:
            raise ValueError("baseline confidence must be finite and between zero and one")
        candidates = []
        for row in self._read():
            if as_of - datetime.fromisoformat(row["window_end"]) > timedelta(hours=36):
                continue
            if any(
                datetime.fromisoformat(row[key]) > as_of
                for key in ("observed_at", "recorded_at", "window_end")
            ):
                continue
            if any(
                row.get(key)
                and (
                    str(row[key]).removesuffix("区") != str(item.get(key, "")).removesuffix("区")
                    if key == "district"
                    else row[key] != item.get(key)
                )
                for key in ("district", "submarket", "community")
            ):
                continue
            candidates.append(row)
        if not candidates:
            return None
        selected = max(
            candidates,
            key=lambda row: (
                sum(bool(row.get(key)) for key in ("district", "submarket", "community")),
                datetime.fromisoformat(row["observed_at"]),
                datetime.fromisoformat(row["recorded_at"]),
            ),
        )

        def decimal_field(field: str) -> Decimal | None:
            return Decimal(str(selected[field])) if selected.get(field) is not None else None

        result = calculate_liquidity(
            LiquidityInputs(
                active_inventory=selected["active_inventory"],
                new_listings=selected["new_listings"],
                listing_exits=selected["listing_exits"],
                median_days_on_market=decimal_field("median_days_on_market"),
                price_cut_ratio=decimal_field("price_cut_ratio"),
                relisting_rate=decimal_field("relisting_rate"),
                buyer_pool_ratio=decimal_field("buyer_pool_ratio"),
                baseline_confidence=baseline_confidence,
            ),
            config.liquidity,
        )
        return {
            **asdict(result),
            "evidence": selected,
            "confidence_basis": "caller_baseline_confidence",
            "baseline_confidence": baseline_confidence,
        }

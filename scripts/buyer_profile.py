"""Explicit buyer constraints and conservative, evidence-backed listing matching."""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, Field, model_validator


class BuyerProfile(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    budget_min_wan: float | None = Field(default=250, gt=0)
    budget_max_wan: float | None = Field(default=300, gt=0)
    districts: list[str] = Field(default_factory=lambda: ["徐汇"])
    bedrooms_min: int | None = Field(default=None, ge=0, le=20)
    bedrooms_max: int | None = Field(default=None, ge=0, le=20)
    elevator_required: bool | None = None
    allowed_floor_labels: list[str] = Field(default_factory=list)
    commute_destination: str | None = None
    commute_max_minutes: int | None = Field(default=None, gt=0, le=1440)
    commute_not_required: bool = False
    confirmed: bool = False

    @model_validator(mode="after")
    def validate_constraints(self) -> BuyerProfile:
        for lower, upper in (
            (self.budget_min_wan, self.budget_max_wan),
            (self.bedrooms_min, self.bedrooms_max),
        ):
            if lower is not None and upper is not None and lower > upper:
                raise ValueError("minimum cannot exceed maximum")
        for value in (self.budget_min_wan, self.budget_max_wan):
            if value is not None and not math.isfinite(value):
                raise ValueError("budget must be finite")
        for values in (self.districts, self.allowed_floor_labels):
            if any(not value.strip() for value in values):
                raise ValueError("constraints cannot contain blank values")
        self.districts = list(dict.fromkeys(value.strip() for value in self.districts))
        self.allowed_floor_labels = list(
            dict.fromkeys(value.strip() for value in self.allowed_floor_labels)
        )
        if self.commute_destination is not None:
            self.commute_destination = self.commute_destination.strip() or None
        if self.commute_not_required and (
            self.commute_destination is not None or self.commute_max_minutes is not None
        ):
            raise ValueError("commute exemption conflicts with commute constraints")
        return self


def completeness_missing(profile: BuyerProfile) -> list[str]:
    missing = []
    if not profile.confirmed:
        missing.append("购房条件尚未确认")
    if profile.budget_max_wan is None:
        missing.append("预算上限尚未明确")
    if not profile.districts:
        missing.append("关注区域尚未明确")
    if not profile.commute_not_required:
        if not profile.commute_destination:
            missing.append("通勤目的地尚未明确")
        if profile.commute_max_minutes is None:
            missing.append("最长通勤时间尚未明确")
    return missing


def _stamp(value: Any) -> datetime:
    if not isinstance(value, str):
        raise ValueError("timestamp must be an ISO string")
    stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if stamp.tzinfo is None or stamp.utcoffset() is None:
        raise ValueError("timestamp must include timezone")
    return stamp


def _envelope(profile: BuyerProfile, saved_at: str | None) -> dict[str, Any]:
    payload = profile.model_dump(mode="json")
    revision = hashlib.sha256(
        json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()
    return {
        **payload,
        "revision": revision,
        "saved_at": saved_at,
        "assumed_fields": []
        if profile.confirmed
        else ["budget_min_wan", "budget_max_wan", "districts"],
        "completeness_missing": completeness_missing(profile),
    }


class BuyerProfileStore:
    def __init__(self, directory: Path) -> None:
        self.path = Path(directory) / "buyer_profile.json"

    def get(self) -> dict[str, Any]:
        if not self.path.exists():
            return _envelope(BuyerProfile(), None)
        value = json.loads(self.path.read_text(encoding="utf-8"))
        saved_at = value["saved_at"]
        if _stamp(saved_at) > datetime.now(UTC):
            raise ValueError("profile timestamp is in the future")
        return _envelope(BuyerProfile.model_validate(value["profile"]), saved_at)

    def save(self, payload: dict[str, Any]) -> dict[str, Any]:
        profile = BuyerProfile.model_validate(payload)
        saved_at = datetime.now(UTC).isoformat()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, name = tempfile.mkstemp(prefix=".buyer-profile-", dir=self.path.parent)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                os.fchmod(handle.fileno(), 0o600)
                json.dump(
                    {"profile": profile.model_dump(mode="json"), "saved_at": saved_at},
                    handle,
                    ensure_ascii=False,
                )
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(name, self.path)
        finally:
            Path(name).unlink(missing_ok=True)
        return _envelope(profile, saved_at)


def _number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        parsed = float(value)
    except (ValueError, TypeError):
        return None
    return parsed if math.isfinite(parsed) else None


def _floor_label(value: Any) -> str | None:
    """Normalize explicit labels only; do not infer a category from floor numbers."""
    if not isinstance(value, str):
        return None
    text = value.strip()
    match = re.fullmatch(r"(低|中|高|顶|底)(?:楼层|层)(?:\s*[（(]\s*共\s*\d+\s*层\s*[)）])?", text)
    if match:
        return match.group(1) + "层"
    # An exact numbered-floor requirement can match without categorizing it.
    if re.fullmatch(r"\d+层", text):
        return text
    return None


def match_listing(
    item: dict[str, Any],
    profile: dict[str, Any],
    evidence: list[dict[str, Any]] | None = None,
    *,
    as_of: datetime | None = None,
) -> dict[str, Any]:
    """Match explicit constraints; source absence never becomes a passing value."""
    if as_of is not None and (as_of.tzinfo is None or as_of.utcoffset() is None):
        raise ValueError("assessment timestamp must include timezone")
    preferences = BuyerProfile.model_validate(
        {key: value for key, value in profile.items() if key in BuyerProfile.model_fields}
    )
    incomplete = completeness_missing(preferences)
    if incomplete:
        return {"status": "PROFILE_INCOMPLETE", "reasons": [], "missing": incomplete}
    reasons: list[str] = []
    missing: list[str] = []
    for field, lower, upper, label in (
        ("price_wan", preferences.budget_min_wan, preferences.budget_max_wan, "挂牌价格"),
        ("bedrooms", preferences.bedrooms_min, preferences.bedrooms_max, "卧室数"),
    ):
        if lower is None and upper is None:
            continue
        value = _number(item.get(field))
        if value is None or value < 0:
            missing.append(f"缺少{label}证据")
        elif (lower is not None and value < lower) or (upper is not None and value > upper):
            reasons.append(f"{label}超出条件")
    district = item.get("district")
    if not district:
        missing.append("缺少区域证据")
    elif str(district).removesuffix("区") not in {
        value.removesuffix("区") for value in preferences.districts
    }:
        reasons.append("不在关注区域")
    if preferences.elevator_required is not None:
        elevator = item.get("elevator")
        if not isinstance(elevator, bool):
            missing.append("电梯情况待核实")
        elif elevator != preferences.elevator_required:
            reasons.append("电梯条件不符")
    if preferences.allowed_floor_labels:
        floor = _floor_label(item.get("floor"))
        allowed = {_floor_label(value) for value in preferences.allowed_floor_labels}
        if floor is None or None in allowed:
            missing.append("楼层待核实")
        elif floor not in allowed:
            if any(floor[0].isdigit() != value[0].isdigit() for value in allowed if value):
                missing.append("楼层类别或具体楼层待核实")
            else:
                reasons.append("楼层条件不符")
    if not preferences.commute_not_required:
        durations: list[float] = []
        try:
            cutoff = as_of if as_of is not None else _stamp(item["observed_at"])
        except (KeyError, ValueError, TypeError):
            cutoff = datetime.now(UTC)
        for record in evidence or []:
            value = record.get("value")
            if record.get("field") != "commute_minutes" or not isinstance(value, dict):
                continue
            if value.get("destination") != preferences.commute_destination:
                continue
            url = urlparse(str(record.get("source_url", "")))
            if url.scheme not in {"http", "https"} or not url.netloc:
                continue
            if not str(record.get("verified_by") or "").strip():
                continue
            try:
                observed = _stamp(record.get("observed_at"))
            except (ValueError, TypeError):
                continue
            duration = _number(value.get("minutes"))
            if observed > min(cutoff, datetime.now(UTC)) or duration is None or duration <= 0:
                continue
            durations.append(duration)
        if not durations:
            missing.append("缺少到指定目的地的可核查通勤时间")
        elif (
            preferences.commute_max_minutes is not None
            and max(durations) > preferences.commute_max_minutes
        ):
            reasons.append("通勤时间超过上限")
    return {
        "status": "EXCLUDED" if reasons else "NEEDS_EVIDENCE" if missing else "MATCH",
        "reasons": reasons,
        "missing": missing,
    }

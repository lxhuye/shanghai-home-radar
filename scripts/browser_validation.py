"""Local, narrow-scope blind research batches; never a production release gate.

Only source listing facts are public before explicit reveal. Input snapshots, config,
labels and an append-only audit chain are stored with integrity hashes and file locks.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from home_radar_decision.p56_config import RealWorldValidationConfig, ValidationGateConfig
from home_radar_decision.p56_metrics import (
    HUMAN_CLASSES,
    MODEL_CLASSES,
    calculate_real_world_metrics,
    gate_recommendation,
)
from home_radar_shared.config import load_yaml_config

SAMPLE_SIZE = 50
WORKFLOWS = {"PASS", "WATCH", "CONTACT", "VIEW"}
VERDICTS = {"AGREE", "PARTIAL", "CONTRADICT"}
ROOT_CAUSES = {
    "DATA_GAP",
    "BAD_COMPARABLE_SELECTION",
    "VALUATION_ERROR",
    "LIQUIDITY_ERROR",
    "FUTURE_FACTOR_ERROR",
    "OBSOLESCENCE_ERROR",
    "DECISION_RULE_ERROR",
    "HUMAN_DISAGREEMENT",
    "UNKNOWN",
}
SOURCE_FIELDS = (
    "listing_id",
    "url",
    "district",
    "submarket",
    "community",
    "price_wan",
    "area_sqm",
    "bedrooms",
    "living_rooms",
    "bathrooms",
    "floor",
    "total_floors",
    "orientation",
    "year_built",
    "elevator",
    "building_type",
    "observed_at",
    "unit_price_yuan_sqm",
    "address",
    "metro_station",
    "metro_distance_m",
    "listing_date",
    "property_type",
)
PROFILE_FIELDS = (
    "budget_min_wan",
    "budget_max_wan",
    "districts",
    "bedrooms_min",
    "bedrooms_max",
    "elevator_required",
    "allowed_floor_labels",
    "commute_destination",
    "commute_max_minutes",
    "commute_not_required",
    "confirmed",
    "revision",
    "saved_at",
    "completeness_missing",
)
SCOPE_WARNING = (
    "同一筛选页的50套独立房源研究样本，存在地域与价格选择偏差。"
    "不替代原五区域独立验证，不解锁P5.7，不构成已验证的购房推荐。"
)


def _hash(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, ensure_ascii=False, default=str).encode()
    ).hexdigest()


def _json(value: Any) -> Any:
    return json.loads(json.dumps(value, ensure_ascii=False, default=str, allow_nan=False))


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _same_home(left: dict[str, Any], right: dict[str, Any]) -> bool:
    keys = ("district", "community", "bedrooms", "floor", "orientation")
    if not left.get("community") or not right.get("community"):
        return False
    if any(left.get(key) != right.get(key) for key in keys):
        return False
    try:
        return abs(float(left["area_sqm"]) - float(right["area_sqm"])) <= 0.3
    except (TypeError, ValueError, KeyError):
        return False


class BrowserValidationStore:
    def __init__(self, directory: Path, config_path: Path = Path("config/validation.yaml")) -> None:
        self.directory = Path(directory)
        self.config_path = Path(config_path)
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)

    @contextmanager
    def _lock(self) -> Iterator[None]:
        descriptor = os.open(self.directory / ".lock", os.O_CREAT | os.O_RDWR, 0o600)
        with os.fdopen(descriptor, "w") as stream:
            fcntl.flock(stream, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(stream, fcntl.LOCK_UN)

    def _path(self, batch_id: str) -> Path:
        try:
            if str(uuid.UUID(batch_id)) != batch_id:
                raise ValueError("invalid batch id")
        except (ValueError, AttributeError, TypeError) as exc:
            raise ValueError("invalid batch id") from exc
        return self.directory / f"{batch_id}.json"

    def _save(self, state: dict[str, Any], action: str, payload: Any) -> None:
        event = {
            "action": action,
            "at": _now(),
            "input": _json(payload),
            "previous": state["audit"][-1]["hash"] if state["audit"] else None,
        }
        event["hash"] = _hash(event)
        state["audit"].append(event)
        state.pop("integrity", None)
        state["integrity"] = _hash(state)
        temporary = self.directory / f".{uuid.uuid4()}.tmp"
        try:
            with os.fdopen(
                os.open(temporary, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600), "w"
            ) as out:
                json.dump(state, out, ensure_ascii=False, allow_nan=False)
                out.flush()
                os.fsync(out.fileno())
            os.replace(temporary, self._path(state["batch_id"]))
        finally:
            temporary.unlink(missing_ok=True)

    def _load(self, batch_id: str) -> dict[str, Any]:
        path = self._path(batch_id)
        try:
            state = json.loads(path.read_text())
            if not isinstance(state, dict):
                raise ValueError("invalid batch")
            digest = state.pop("integrity")
            if digest != _hash(state) or state["batch_id"] != batch_id:
                raise ValueError("batch integrity check failed")
            state["integrity"] = digest
            previous = None
            for event in state["audit"]:
                data = {key: value for key, value in event.items() if key != "hash"}
                if event["previous"] != previous or _hash(data) != event["hash"]:
                    raise ValueError("batch audit integrity check failed")
                previous = event["hash"]
            if _hash(state["inputs"]) != state["inputs_fingerprint"]:
                raise ValueError("frozen inputs changed")
            if (
                state["status"] != "LABELING"
                and _hash(state["labels"]) != state["labels_fingerprint"]
            ):
                raise ValueError("frozen labels changed")
            return state
        except (json.JSONDecodeError, KeyError, TypeError) as exc:
            raise ValueError("invalid or corrupted validation batch") from exc

    def create(
        self,
        feed: dict[str, Any],
        report: dict[str, Any],
        name: str,
        reviewer: str,
        independent_review: bool,
    ) -> dict[str, Any]:
        if not isinstance(feed, dict) or not isinstance(report, dict):
            raise ValueError("feed and report must be objects")
        if not isinstance(name, str) or not name.strip() or len(name) > 200:
            raise ValueError("name must contain 1–200 characters")
        if not isinstance(reviewer, str) or not reviewer.strip() or len(reviewer) > 200:
            raise ValueError("reviewer must contain 1–200 characters")
        if type(independent_review) is not bool:
            raise ValueError("independent_review requires an explicit boolean attestation")
        feed, report = _json(feed), _json(report)
        if report.get("feed_fingerprint") != _hash(feed) or report.get("status") != "complete":
            raise ValueError("analysis must be complete and match the exact source feed")
        if not report.get("input_fingerprint"):
            raise ValueError("analysis input fingerprint is required")
        if (
            feed.get("metadata", {}).get("synthetic")
            or feed.get("metadata", {}).get("data_mode") == "demo"
        ):
            raise ValueError("synthetic/demo listings cannot form a real listing batch")
        if not isinstance(feed.get("items"), list) or not isinstance(report.get("items"), list):
            raise ValueError("feed and report items are required")
        if any(
            not isinstance(item, dict)
            or not isinstance(item.get("listing_id"), str)
            or not item["listing_id"]
            for item in [*feed["items"], *report["items"]]
        ):
            raise ValueError("every source and model item requires a nonempty listing id")
        model = {str(item["listing_id"]): item for item in report["items"]}
        if len(model) != len(report["items"]):
            raise ValueError("analysis contains duplicate listing ids")
        candidates: dict[str, dict[str, Any]] = {}
        for item in feed["items"]:
            listing_id = str(item.get("listing_id", ""))
            if not listing_id or listing_id in candidates:
                raise ValueError("feed requires unique nonempty listing ids")
            if listing_id not in model:
                raise ValueError("analysis is missing a source listing")
            if item.get("synthetic") or item.get("provenance", {}).get("synthetic"):
                raise ValueError("synthetic listings cannot form a real listing batch")
            candidates[listing_id] = item
        ranks = []
        for row in model.values():
            decision = row.get("decision")
            if decision is not None and not isinstance(decision, dict):
                raise ValueError("model decision must be an object or null")
            decision = decision or {}
            if decision.get(
                "opportunity_classification", "INSUFFICIENT_DATA"
            ) not in MODEL_CLASSES or decision.get("workflow_state", "PASS") not in tuple(
                WORKFLOWS
            ):
                raise ValueError("invalid model classification/workflow")
            rank = row.get("rank")
            if rank is not None:
                if type(rank) is not int or rank < 1:
                    raise ValueError("model ranks must be positive integers")
                ranks.append(rank)
        if len(set(ranks)) != len(ranks):
            raise ValueError("model ranks must be unique")
        unique: list[dict[str, Any]] = []
        for listing_id in sorted(candidates):
            item = candidates[listing_id]
            if not any(_same_home(item, other) for other in unique):
                unique.append(item)
        if len(unique) < SAMPLE_SIZE:
            raise ValueError(
                f"need 50 distinct homes after deduplication; only {len(unique)} available"
            )
        # The source-only seed and sorting never consult model ranks or scores.
        selected = sorted(unique, key=lambda item: _hash([_hash(feed), item["listing_id"]]))[
            :SAMPLE_SIZE
        ]
        config = RealWorldValidationConfig.model_validate(load_yaml_config(self.config_path))
        inputs = {
            "feed": feed,
            "report": report,
            "config": config.model_dump(mode="json"),
            "selected_ids": [str(item["listing_id"]) for item in selected],
            "sampling_method": "source_hash_order_after_same_home_dedup_v1",
            "unique_home_count": len(unique),
        }
        state = {
            "batch_id": str(uuid.uuid4()),
            "name": name.strip(),
            "reviewer": reviewer.strip(),
            "independent_review": independent_review,
            "mode": "independent_blind_attested" if independent_review else "practice",
            "exposure_attestation": (
                "评审者明确声明未查看本批次房源的模型结果；此声明未被系统独立核实。"
                if independent_review
                else "未声明独立盲评，仅用于练习，不作为独立验证证据。"
            ),
            "status": "LABELING",
            "created_at": _now(),
            "frozen_at": None,
            "revealed_at": None,
            "inputs": inputs,
            "inputs_fingerprint": _hash(inputs),
            "labels": {},
            "reviews": {},
            "qualified_at": None,
            "labels_fingerprint": None,
            "comparison": None,
            "audit": [],
        }
        with self._lock():
            self._save(
                state,
                "create",
                {
                    "name": name,
                    "reviewer": reviewer,
                    "independent_review": independent_review,
                    "inputs_fingerprint": state["inputs_fingerprint"],
                },
            )
        return self._public(state)

    def list(self) -> list[dict[str, Any]]:
        with self._lock():
            batches = [
                self._public(self._load(path.stem), summary=True)
                for path in self.directory.glob("*.json")
            ]
        return sorted(batches, key=lambda batch: batch["created_at"], reverse=True)

    def read(self, batch_id: str) -> dict[str, Any]:
        with self._lock():
            return self._public(self._load(batch_id))

    def label(self, batch_id: str, listing_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(payload, dict):
            raise ValueError("label must be an object")
        required = {"human_class", "human_workflow", "human_would_visit", "notes"}
        if set(payload) != required:
            raise ValueError(
                "label requires exactly human_class, human_workflow, human_would_visit, notes"
            )
        if (
            not isinstance(payload["human_class"], str)
            or not isinstance(payload["human_workflow"], str)
            or payload["human_class"] not in HUMAN_CLASSES
            or payload["human_workflow"] not in WORKFLOWS
        ):
            raise ValueError("invalid human class or workflow")
        if type(payload["human_would_visit"]) is not bool:
            raise ValueError("human_would_visit must be a boolean")
        if not isinstance(payload["notes"], str) or len(payload["notes"]) > 10000:
            raise ValueError("notes must be text of at most 10000 characters")
        with self._lock():
            state = self._load(batch_id)
            if state["status"] != "LABELING":
                raise ValueError("labels are frozen and cannot be changed")
            if listing_id not in state["inputs"]["selected_ids"]:
                raise ValueError("listing does not belong to this batch")
            state["labels"][listing_id] = _json(payload)
            self._save(state, "label", {"listing_id": listing_id, **payload})
            return self._public(state)

    def freeze(self, batch_id: str) -> dict[str, Any]:
        with self._lock():
            state = self._load(batch_id)
            if state["status"] != "LABELING":
                raise ValueError("batch is already frozen")
            if set(state["labels"]) != set(state["inputs"]["selected_ids"]):
                raise ValueError("all 50 listings require complete human labels before freezing")
            state.update(
                status="FROZEN", frozen_at=_now(), labels_fingerprint=_hash(state["labels"])
            )
            self._save(state, "freeze", {"labels_fingerprint": state["labels_fingerprint"]})
            return self._public(state)

    def reveal(self, batch_id: str) -> dict[str, Any]:
        with self._lock():
            state = self._load(batch_id)
            if state["status"] == "LABELING":
                raise ValueError("freeze all human labels before revealing model results")
            if state["status"] == "REVEALED":
                return self._public(state)
            state.update(status="REVEALED", revealed_at=_now(), comparison=self._compare(state))
            self._save(state, "reveal", {"comparison_fingerprint": _hash(state["comparison"])})
            return self._public(state)

    def _compare(self, state: dict[str, Any]) -> dict[str, Any]:
        models = {str(item["listing_id"]): item for item in state["inputs"]["report"]["items"]}
        cases = []
        for listing_id in state["inputs"]["selected_ids"]:
            row = models[listing_id]
            decision = row.get("decision") or {}
            model_class = decision.get("opportunity_classification", "INSUFFICIENT_DATA")
            workflow = decision.get("workflow_state", "PASS")
            rank = row.get("rank")
            if model_class not in MODEL_CLASSES or workflow not in WORKFLOWS:
                raise ValueError("invalid frozen model classification/workflow")
            if rank is not None and (type(rank) is not int or rank < 1):
                raise ValueError("invalid frozen model rank")
            cases.append(
                {
                    "listing_id": listing_id,
                    **state["labels"][listing_id],
                    "rank": rank,
                    "model_class": model_class,
                    "model_workflow": workflow,
                    "model_eligibility": decision.get("eligibility_status", "INSUFFICIENT"),
                    "valuation_confidence": decision.get("valuation_confidence", "insufficient"),
                    "future_confidence": decision.get("future_confidence", "insufficient"),
                }
            )
        ranks = [case["rank"] for case in cases if case["rank"] is not None]
        if len(set(ranks)) != len(ranks):
            raise ValueError("frozen model ranks must be unique")
        reviews = state.get("reviews", {})
        metrics = calculate_real_world_metrics(cases, reviews)
        # Shared metrics use available-prefix denominators. Do not report a short
        # list as top-5/top-10 performance or let empty counts satisfy safety gates.
        for size in (5, 10):
            if len(ranks) < size:
                for key in (
                    f"precision_at_{size}",
                    f"human_acceptance_at_{size}",
                    f"value_trap_rate_at_{size}",
                ):
                    metrics[key] = None
        config = ValidationGateConfig.model_validate(state["inputs"]["config"]["gate"])
        _, checks = gate_recommendation(metrics, config)
        checks["minimum_ranked_5"] = len(ranks) >= 5
        checks["minimum_ranked_10"] = len(ranks) >= 10
        if len(ranks) < 5:
            checks["regret_at_5"] = False
        if len(ranks) < 10:
            checks["value_traps_at_10"] = False
        checks["independent_review_attested"] = state["independent_review"]
        checks["all_cases_reviewed"] = set(reviews) == set(state["inputs"]["selected_ids"])
        return dict(
            _json(
                {
                    "metrics": metrics,
                    "checks": checks,
                    "status": "RESEARCH_ONLY",
                    "formal_gate_eligible": False,
                    "research_checks_passed": all(checks.values()),
                    "limitations": [
                        SCOPE_WARNING,
                        *(
                            []
                            if checks["all_cases_reviewed"]
                            else ["推荐理由复核尚未覆盖全部50套，不自动判定通过。"]
                        ),
                    ],
                    "inputs_fingerprint": state["inputs_fingerprint"],
                    "report_fingerprint": _hash(state["inputs"]["report"]),
                }
            )
        )

    def review(self, batch_id: str, listing_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(payload, dict) or set(payload) != {
            "why_ranked_verdict",
            "root_cause",
            "notes",
        }:
            raise ValueError("review requires exactly why_ranked_verdict, root_cause, notes")
        if (
            not isinstance(payload["why_ranked_verdict"], str)
            or payload["why_ranked_verdict"] not in VERDICTS
            or not isinstance(payload["root_cause"], str)
            or payload["root_cause"] not in ROOT_CAUSES
            or not isinstance(payload["notes"], str)
            or len(payload["notes"]) > 10000
        ):
            raise ValueError("invalid review verdict, root cause or notes")
        with self._lock():
            state = self._load(batch_id)
            if state["status"] != "REVEALED":
                raise ValueError("model reasons can only be reviewed after explicit reveal")
            if listing_id not in state["inputs"]["selected_ids"]:
                raise ValueError("listing does not belong to this batch")
            state.setdefault("reviews", {})[listing_id] = _json(payload)
            state["comparison"] = self._compare(state)
            if state["comparison"]["research_checks_passed"]:
                state["qualified_at"] = state.get("qualified_at") or _now()
            else:
                state["qualified_at"] = None
            self._save(state, "review", {"listing_id": listing_id, **payload})
            return self._public(state)

    def qualification(self, report: dict[str, Any], profile: dict[str, Any]) -> dict[str, Any]:
        """A current matching model needs calibration and a later disjoint holdout.

        This qualifies only research recommendations within this exact source scope;
        neither the original five-region validation nor P5.7 is modified.
        """
        current_config = RealWorldValidationConfig.model_validate(
            load_yaml_config(self.config_path)
        )
        expected = {
            "configuration_fingerprint": report.get("configuration_fingerprint"),
            "profile_fingerprint": profile.get("revision"),
            "source_id": report.get("source_id"),
            "source_scope": report.get("source_scope"),
        }
        output: dict[str, Any] = {
            "status": "uncalibrated",
            "formal_gate_eligible": False,
            "qualifying_batch_ids": [],
            "holdout_batch_ids": [],
            "scope": report.get("source_scope"),
            "limitations": [
                SCOPE_WARNING,
                "需要同模型、同条件下的独立盲评，以及随后新观测且不重叠的50套留出验证。",
            ],
        }
        if not all(expected.values()):
            return output
        with self._lock():
            states = [self._load(path.stem) for path in self.directory.glob("*.json")]
        qualifying = []
        for state in states:
            inputs = state["inputs"]
            frozen = inputs["report"]
            if (
                state["status"] != "REVEALED"
                or not state["independent_review"]
                or not state.get("qualified_at")
                or any(frozen.get(key) != value for key, value in expected.items())
                or inputs["feed"].get("source_id") != expected["source_id"]
                or inputs["feed"].get("scope") != expected["source_scope"]
                or inputs["config"] != current_config.model_dump(mode="json")
                or not self._compare(state)["research_checks_passed"]
            ):
                continue
            qualifying.append(state)
        qualifying.sort(key=lambda state: state["qualified_at"])
        output["qualifying_batch_ids"] = [state["batch_id"] for state in qualifying]
        if qualifying:
            output["status"] = "awaiting_holdout"
        for first in qualifying:
            first_ids = set(first["inputs"]["selected_ids"])
            first_homes = [
                item for item in first["inputs"]["feed"]["items"] if item["listing_id"] in first_ids
            ]
            completed = datetime.fromisoformat(first["qualified_at"])
            for second in qualifying:
                second_ids = set(second["inputs"]["selected_ids"])
                if (
                    first_ids & second_ids
                    or datetime.fromisoformat(second["created_at"]) <= completed
                ):
                    continue
                second_homes = [
                    item
                    for item in second["inputs"]["feed"]["items"]
                    if item["listing_id"] in second_ids
                ]
                try:
                    fresh = all(
                        datetime.fromisoformat(item["observed_at"]) > completed
                        for item in second_homes
                    )
                except (KeyError, TypeError, ValueError):
                    fresh = False
                if not fresh or any(
                    _same_home(left, right) for left in first_homes for right in second_homes
                ):
                    continue
                output.update(
                    status="validated_scoped",
                    holdout_batch_ids=[first["batch_id"], second["batch_id"]],
                )
                return output
        return output

    def _public(self, state: dict[str, Any], summary: bool = False) -> dict[str, Any]:
        output = {
            key: state[key]
            for key in (
                "batch_id",
                "name",
                "reviewer",
                "independent_review",
                "mode",
                "exposure_attestation",
                "status",
                "created_at",
                "frozen_at",
                "revealed_at",
            )
        }
        output.update(
            sample_size=SAMPLE_SIZE,
            labeled_count=len(state["labels"]),
            reviewed_count=len(state.get("reviews", {})),
            formal_gate_eligible=False,
            scope_warning=SCOPE_WARNING,
        )
        if summary:
            return output
        inputs = state["inputs"]
        profile = inputs["report"].get("profile", {})
        output["buyer_profile"] = {key: profile[key] for key in PROFILE_FIELDS if key in profile}
        listings = {str(item["listing_id"]): item for item in inputs["feed"]["items"]}
        models = {str(item["listing_id"]): item for item in inputs["report"]["items"]}
        output["items"] = []
        for listing_id in inputs["selected_ids"]:
            item = {
                "listing_id": listing_id,
                "listing": {key: listings[listing_id].get(key) for key in SOURCE_FIELDS},
                "label": state["labels"].get(listing_id),
            }
            if state["status"] == "REVEALED":
                item["model"] = models[listing_id]
                item["review"] = state.get("reviews", {}).get(listing_id)
            output["items"].append(item)
        if state["status"] == "REVEALED":
            output["comparison"] = state["comparison"]
        return dict(_json(output))

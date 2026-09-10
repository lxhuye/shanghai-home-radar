"""Local recommendation workspace routes, isolated from source browsing and production DB."""

from __future__ import annotations

import asyncio
import copy
from collections import Counter
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any, ParamSpec, TypeVar

from fastapi import Depends, FastAPI, HTTPException, Request

from scripts.browser_validation import BrowserValidationStore
from scripts.buyer_profile import BuyerProfileStore, match_listing
from scripts.recommendation_evidence import RecommendationEvidenceStore, digest
from scripts.recommendation_future import FutureEvidenceStore
from scripts.recommendation_market import MarketEvidenceStore

P = ParamSpec("P")
T = TypeVar("T")


def build_recommendations(
    feed: dict[str, Any],
    report: dict[str, Any],
    profile: dict[str, Any],
    store: RecommendationEvidenceStore,
    *,
    current: bool,
    qualification: dict[str, Any] | None = None,
) -> dict[str, Any]:
    qualification = qualification or {"status": "uncalibrated"}
    validated = qualification["status"] == "validated_scoped"
    as_of = datetime.fromisoformat(report.get("evidence_cutoff_at", report["observed_at"]))
    originals = {item["listing_id"]: item for item in feed["items"]}
    result = copy.deepcopy(report)
    for row in result["items"]:
        original = store.enrich(originals[row["listing_id"]], as_of)
        row["verified_evidence"] = original.get("verified_evidence", [])
        row["fit"] = match_listing(original, profile, row["verified_evidence"], as_of=as_of)
        row["personal_rank"] = None
        row["research_rank"] = None
    eligible = sorted(
        (
            row
            for row in result["items"]
            if row["fit"]["status"] == "MATCH"
            and row.get("rank") is not None
            and (row.get("decision") or {}).get("eligibility_status") == "ELIGIBLE"
        ),
        key=lambda row: row["rank"],
    )
    for index, row in enumerate(eligible, 1):
        row["research_rank"] = index
        if validated and current:
            row["personal_rank"] = index
    # Research validation and listing compatibility do not replace independent model calibration.
    counts = Counter(row["fit"]["status"] for row in result["items"])
    rows = result["items"]
    blockers = [
        {"code": "profile", "label": "条件尚未确认完整", "count": counts["PROFILE_INCOMPLETE"]},
        {"code": "property", "label": "匹配所需事实待核实", "count": counts["NEEDS_EVIDENCE"]},
        {
            "code": "valuation",
            "label": "估值证据不足",
            "count": sum(
                not row.get("valuation")
                or row["valuation"]["valuation_confidence"] == "insufficient"
                for row in rows
            ),
        },
        {
            "code": "future",
            "label": "未来因子置信度不足",
            "count": sum(
                not row.get("future") or row["future"]["confidence"] == "insufficient"
                for row in rows
            ),
        },
        {
            "code": "transactions",
            "label": "缺少可比成交支持",
            "count": sum(
                not row.get("valuation")
                or row["valuation"].get("transaction_support") in {None, "none", "insufficient"}
                for row in rows
            ),
        },
        {
            "code": "liquidity",
            "label": "缺少当前完整市场流动性资料",
            "count": sum(
                not row.get("liquidity_evidence") or row["liquidity_evidence"]["score"] is None
                for row in rows
            ),
        },
        {
            "code": "calibration",
            "label": "尚未通过独立模型校准",
            "count": 0 if validated else len(rows),
        },
    ]
    result.update(
        profile=profile,
        current=current,
        profile_revision=profile["revision"],
        input_fingerprint=digest(
            {
                "analysis": report["input_fingerprint"],
                "profile": profile["revision"],
                "qualification": qualification,
            }
        ),
        readiness={
            "blockers": [block for block in blockers if block["count"]],
            "validation_status": qualification["status"],
            "qualification": qualification,
        },
        summary={
            "observed": len(rows),
            "matched": counts["MATCH"],
            "excluded": counts["EXCLUDED"],
            "needs_evidence": counts["NEEDS_EVIDENCE"],
            "profile_incomplete": counts["PROFILE_INCOMPLETE"],
            "recommended": len(eligible) if validated and current else 0,
            "research_candidates": len(eligible),
        },
    )
    return result


def register_recommendation_routes(app: FastAPI, handoff: Any) -> None:
    directory = handoff.export_dir / "recommendation"
    profiles = BuyerProfileStore(directory)
    evidence = RecommendationEvidenceStore(directory)
    future_evidence = FutureEvidenceStore(directory)
    market_evidence = MarketEvidenceStore(directory)
    validation = BrowserValidationStore(directory / "validation")

    async def local_action(request: Request) -> None:
        origin = request.headers.get("origin")
        if request.headers.get("x-handoff-action") != "1" or (
            origin and origin.rstrip("/") != str(request.base_url).rstrip("/")
        ):
            raise HTTPException(403, "只接受本机页面的操作。")

    async def payload(request: Request) -> dict[str, Any]:
        body = await request.body()
        if len(body) > 2_000_000:
            raise HTTPException(413, "单批数据不能超过 2 MB。")
        try:
            import json

            value = json.loads(body)
        except ValueError:
            raise HTTPException(422, "请提供有效 JSON。") from None
        if not isinstance(value, dict):
            raise HTTPException(422, "请提供 JSON 对象。")
        return value

    def handled(function: Callable[P, T], *args: P.args, **kwargs: P.kwargs) -> T:
        try:
            return function(*args, **kwargs)
        except FileNotFoundError:
            raise HTTPException(404, "记录不存在。") from None
        except (ValueError, TypeError) as exc:
            raise HTTPException(422, str(exc)) from None

    async def latest() -> tuple[dict[str, Any], dict[str, Any]]:
        if handoff.latest is None or handoff.analyzer is None:
            raise HTTPException(404, "尚无房源观测。")
        feed = copy.deepcopy(handoff.latest)
        report = await asyncio.to_thread(handoff.analyzer.read, feed)
        if report is None:
            raise HTTPException(503, "证据更新后的分析尚未完成。")
        return feed, report

    @app.get("/buyer-profile")
    async def get_profile() -> dict[str, Any]:
        return handled(profiles.get)

    @app.put("/buyer-profile", dependencies=[Depends(local_action)])
    async def put_profile(request: Request) -> dict[str, Any]:
        value = await payload(request)
        async with handoff.lock:
            return handled(profiles.save, value)

    @app.get("/recommendations")
    async def recommendations() -> dict[str, Any]:
        async with handoff.lock:
            feed, report = await latest()
            age = datetime.now(UTC) - datetime.fromisoformat(report["observed_at"])
            current = handoff.state["status"] == "READY" and timedelta(0) <= age <= timedelta(
                hours=36
            )
            profile = profiles.get()
            qualification = handled(
                validation.qualification, {**report, "source_scope": feed["scope"]}, profile
            )
            return build_recommendations(
                feed, report, profile, evidence, current=current, qualification=qualification
            )

    @app.get("/property-evidence")
    async def get_evidence() -> dict[str, Any]:
        return {"items": evidence.read()["items"]}

    @app.post("/property-evidence", dependencies=[Depends(local_action)])
    async def add_evidence(request: Request) -> dict[str, Any]:
        value = await payload(request)
        async with handoff.lock:
            if handoff.latest is None or value.get("listing_id") not in {
                row["listing_id"] for row in handoff.latest["items"]
            }:
                raise HTTPException(422, "只能为当前观测内的房源添加证据。")
            receipt = handled(evidence.append_property, value)
            await handoff.update_analysis()
            return {**receipt, "analysis_status": handoff.state.get("analysis_status")}

    @app.get("/transactions/template")
    async def transaction_template() -> dict[str, Any]:
        return {
            "schema_version": "1.0",
            "data_mode": "sample",
            "source_id": "replace_with_provider",
            "records": [],
        }

    @app.get("/transactions/schema")
    async def transaction_schema() -> dict[str, Any]:
        from scripts.recommendation_evidence import TransactionBatch

        return TransactionBatch.model_json_schema()

    @app.post("/transactions/import", dependencies=[Depends(local_action)])
    async def import_transactions(request: Request) -> dict[str, Any]:
        value = await payload(request)
        async with handoff.lock:
            receipt = handled(evidence.import_transactions, value)
            await handoff.update_analysis()
            return {**receipt, "analysis_status": handoff.state.get("analysis_status")}

    @app.get("/market-evidence/template")
    async def market_template() -> dict[str, Any]:
        return {"data_mode": "sample", "verified_by": "", "records": []}

    @app.post("/market-evidence/import", dependencies=[Depends(local_action)])
    async def import_market(request: Request) -> dict[str, Any]:
        value = await payload(request)
        async with handoff.lock:
            receipt = handled(market_evidence.import_evidence, value)
            await handoff.update_analysis()
            return {**receipt, "analysis_status": handoff.state.get("analysis_status")}

    @app.get("/future-evidence/template")
    async def future_template() -> dict[str, Any]:
        return {
            "data_mode": "sample",
            "verified_by": "",
            "factor_observations": [],
            "employment_centers": [],
            "future_projects": [],
        }

    @app.post("/future-evidence/import", dependencies=[Depends(local_action)])
    async def import_future(request: Request) -> dict[str, Any]:
        value = await payload(request)
        async with handoff.lock:
            receipt = handled(future_evidence.import_evidence, value)
            await handoff.update_analysis()
            return {**receipt, "analysis_status": handoff.state.get("analysis_status")}

    @app.get("/validation-runs")
    async def list_validation() -> dict[str, Any]:
        return {"items": handled(validation.list)}

    @app.post("/validation-runs", dependencies=[Depends(local_action)])
    async def create_validation(request: Request) -> dict[str, Any]:
        value = await payload(request)
        if (
            set(value) != {"name", "reviewer", "independent_review"}
            or type(value.get("independent_review")) is not bool
        ):
            raise HTTPException(422, "需要批次名称、复核人和明确的独立复核声明。")
        async with handoff.lock:
            feed, report = await latest()
            profile = profiles.get()
            source_fingerprint = report["feed_fingerprint"]
            cutoff = datetime.fromisoformat(report.get("evidence_cutoff_at", report["observed_at"]))
            feed["items"] = [evidence.enrich(item, cutoff) for item in feed["items"]]
            frozen_report = {
                **report,
                "feed_fingerprint": digest(feed),
                "original_feed_fingerprint": source_fingerprint,
                "source_scope": copy.deepcopy(feed["scope"]),
                "profile": profile,
                "profile_fingerprint": profile["revision"],
            }
            return handled(validation.create, feed, frozen_report, **value)

    @app.get("/validation-runs/{batch_id}")
    async def read_validation(batch_id: str) -> dict[str, Any]:
        return handled(validation.read, batch_id)

    @app.post("/validation-runs/{batch_id}/labels", dependencies=[Depends(local_action)])
    async def label_validation(batch_id: str, request: Request) -> dict[str, Any]:
        value = await payload(request)
        listing_id = value.pop("listing_id", None)
        if not isinstance(listing_id, str):
            raise HTTPException(422, "缺少房源编号。")
        async with handoff.lock:
            return handled(validation.label, batch_id, listing_id, value)

    @app.post("/validation-runs/{batch_id}/reviews", dependencies=[Depends(local_action)])
    async def review_validation(batch_id: str, request: Request) -> dict[str, Any]:
        value = await payload(request)
        listing_id = value.pop("listing_id", None)
        if not isinstance(listing_id, str):
            raise HTTPException(422, "缺少房源编号。")
        async with handoff.lock:
            return handled(validation.review, batch_id, listing_id, value)

    @app.post("/validation-runs/{batch_id}/freeze", dependencies=[Depends(local_action)])
    async def freeze_validation(batch_id: str) -> dict[str, Any]:
        async with handoff.lock:
            return handled(validation.freeze, batch_id)

    @app.post("/validation-runs/{batch_id}/reveal", dependencies=[Depends(local_action)])
    async def reveal_validation(batch_id: str) -> dict[str, Any]:
        async with handoff.lock:
            return handled(validation.reveal, batch_id)

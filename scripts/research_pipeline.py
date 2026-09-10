"""Source-isolated browser research: existing P4 -> P5 -> P5.5 engines.

No database, synthetic comparables, transaction claims, or imported calibration pool.
Insufficient evidence is an ordinary per-listing result, not a made-up price or score.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import asdict, replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import home_radar_decision
import home_radar_forecasting
import home_radar_market
import home_radar_valuation
from home_radar_collector.feed import CanonicalFeedEnvelope
from home_radar_decision.config import DecisionConfig
from home_radar_decision.domain import DecisionInputs
from home_radar_decision.orchestrator import DecisionOrchestrator
from home_radar_decision.ranking import rank_eligible
from home_radar_forecasting.config import FutureConfig
from home_radar_forecasting.domain import FutureInputs, FutureProperty
from home_radar_forecasting.engine import DeterministicFutureEngine
from home_radar_market.computation import compute_slice
from home_radar_market.config import MarketBaselineConfig
from home_radar_market.domain import BaselineSliceKey, ObservationRecord
from home_radar_shared.config import load_yaml_config
from home_radar_valuation.aggregation import InsufficientValuationEvidenceError
from home_radar_valuation.config import ValuationConfig
from home_radar_valuation.domain import BaselineEvidence, ComparableRecord, TargetProperty
from home_radar_valuation.engine import DeterministicValuationEngine
from home_radar_valuation.history import build_price_history
from home_radar_valuation.repository import EvaluationInputs

from scripts.prepare_public_monitor_pilot import SOURCE_ID
from scripts.recommendation_evidence import RecommendationEvidenceStore
from scripts.recommendation_future import FutureEvidenceStore
from scripts.recommendation_market import MarketEvidenceStore
from scripts.research_evidence import ResearchEvidence

PIPELINE_VERSION = "browser-research-2"
WAN = Decimal("10000")


def fingerprint(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, ensure_ascii=False, default=str).encode()
    ).hexdigest()


def safe_json(value: Any) -> Any:
    return json.loads(
        json.dumps(
            value,
            default=lambda item: item.isoformat() if isinstance(item, datetime) else str(item),
            ensure_ascii=False,
        )
    )


def listing_uuid(source_id: str, listing_id: str) -> uuid.UUID:
    return uuid.uuid5(uuid.NAMESPACE_URL, f"{source_id}/{listing_id}")


def attributes(item: dict[str, Any]) -> dict[str, Any]:
    return {
        key: item.get(key)
        for key in (
            "bedrooms",
            "floor",
            "total_floors",
            "orientation",
            "year_built",
            "elevator",
            "building_type",
            "metro_distance_m",
        )
    }


class ResearchPipeline:
    def __init__(
        self,
        export_dir: Path,
        config_dir: Path = Path("config"),
        evidence_dir: Path = Path("data/evidence/sample"),
    ) -> None:
        self.export_dir = export_dir
        self.supplemental = RecommendationEvidenceStore(export_dir / "recommendation")
        self.future_evidence = FutureEvidenceStore(export_dir / "recommendation")
        self.market_evidence = MarketEvidenceStore(export_dir / "recommendation")
        self.evidence = ResearchEvidence(evidence_dir)
        self.market_config = MarketBaselineConfig.model_validate(
            load_yaml_config(config_dir / "market_baseline.yaml")
        )
        self.valuation_config = ValuationConfig.model_validate(
            load_yaml_config(config_dir / "valuation.yaml")
        )
        self.future_config = FutureConfig.model_validate(
            load_yaml_config(config_dir / "forecasting.yaml")
        )
        self.decision_config = DecisionConfig.model_validate(
            load_yaml_config(config_dir / "decision.yaml")
        )
        self.version = fingerprint(
            {
                "pipeline": PIPELINE_VERSION,
                "integration_sources": {
                    name: hashlib.sha256(Path(__file__).with_name(name).read_bytes()).hexdigest()
                    for name in (
                        "research_pipeline.py",
                        "research_evidence.py",
                        "recommendation_evidence.py",
                        "recommendation_future.py",
                        "recommendation_market.py",
                        "import_future_evidence.py",
                        "geocode_listings.py",
                    )
                },
                "engine_sources": {
                    package.__name__: {
                        path.name: hashlib.sha256(path.read_bytes()).hexdigest()
                        for path in sorted(Path(str(package.__file__)).parent.glob("*.py"))
                    }
                    for package in (
                        home_radar_market,
                        home_radar_valuation,
                        home_radar_forecasting,
                        home_radar_decision,
                    )
                },
                "configs": {
                    name: (config_dir / name).read_text()
                    for name in (
                        "market_baseline.yaml",
                        "valuation.yaml",
                        "forecasting.yaml",
                        "decision.yaml",
                    )
                },
            }
        )

    def key(self, feed: dict[str, Any]) -> str:
        return fingerprint(
            {
                "feed": feed,
                "engine": self.version,
                "evidence": self.evidence.fingerprint(),
                "supplemental_evidence": self.supplemental.fingerprint(),
                "future_evidence": self.future_evidence.fingerprint(),
                "market_evidence": self.market_evidence.fingerprint(),
            }
        )

    def read(self, feed: dict[str, Any]) -> dict[str, Any] | None:
        path = self.export_dir / "analysis" / f"{self.key(feed)}.json"
        if not path.exists():
            return None
        value = json.loads(path.read_text())
        if value.get("input_fingerprint") != self.key(feed):
            raise ValueError("analysis does not match the current observation")
        return dict(value)

    def run(self, feed: dict[str, Any]) -> dict[str, Any]:
        try:
            existing = self.read(feed)
        except (OSError, ValueError):
            existing = None  # Rebuild a damaged derived report from preserved raw evidence.
        if existing is not None:
            return existing
        report = self.evaluate(feed)
        output = self.export_dir / "analysis"
        output.mkdir(parents=True, exist_ok=True, mode=0o700)
        path = output / f"{self.key(feed)}.json"
        # A report becomes visible only when all listings have been processed.
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
        temporary.chmod(0o600)
        temporary.replace(path)
        return report

    def evaluate(self, feed: dict[str, Any]) -> dict[str, Any]:
        CanonicalFeedEnvelope.model_validate(feed)
        if feed["source_id"] != SOURCE_ID or feed["metadata"].get("data_mode") != "sample":
            raise ValueError("browser research accepts only its own isolated SAMPLE source")
        if feed["completeness"] != "partial" or not feed["items"]:
            raise ValueError("browser research requires a nonempty partial observation")
        items = sorted(feed["items"], key=lambda item: item["listing_id"])
        timestamps = [datetime.fromisoformat(item["observed_at"]) for item in items]
        if any(stamp.utcoffset() is None for stamp in timestamps):
            raise ValueError("observation timestamps must include timezone")
        observation_as_of = max(timestamps)
        as_of = max(
            observation_as_of,
            self.supplemental.latest_timestamp() or observation_as_of,
            self.future_evidence.latest_timestamp() or observation_as_of,
            self.market_evidence.latest_timestamp() or observation_as_of,
        )
        if as_of > datetime.now(UTC):
            raise ValueError("future observation is invalid")
        items, evidence_summary = self.evidence.enrich(items, as_of)
        items = [self.supplemental.enrich(item, as_of) for item in items]
        candidates = [self.comparable(item) for item in items]
        sources = {
            str(candidate.listing_id): item
            for candidate, item in zip(candidates, items, strict=True)
        }
        transactions = []
        for record in self.supplemental.transaction_items(as_of):
            normalized = {
                **record,
                "listing_id": record["source_record_id"],
                "url": record["source_url"],
                "observed_at": record["sold_at"],
            }
            candidate = replace(
                self.comparable(normalized),
                observation_type="transaction",
                source=record["source_id"],
                listing_id=listing_uuid(
                    "transaction/" + record["source_id"], record["source_record_id"]
                ),
            )
            transactions.append(candidate)
            sources[str(candidate.listing_id)] = normalized
        evidence_summary["transaction_records"] = len(transactions)
        history = self.history(feed, observation_as_of)
        valuation_engine = DeterministicValuationEngine(self.valuation_config)
        future_engine = DeterministicFutureEngine(self.future_config)
        decision_engine = DecisionOrchestrator(self.decision_config)
        results = []
        decisions = []
        for item, candidate in zip(items, candidates, strict=True):
            prices = history.get(item["listing_id"], [])
            prices = sorted(set(prices + [(candidate.observed_at, candidate.total_price)]))
            cuts = [
                prices[index][0]
                for index in range(1, len(prices))
                if prices[index][1] < prices[index - 1][1]
            ]
            target = TargetProperty(
                listing_id=candidate.listing_id,
                district=item["district"],
                submarket=item["submarket"],
                community=item["community"],
                area_sqm=candidate.area_sqm,
                current_ask=candidate.total_price,
                **attributes(item),
            )
            row: dict[str, Any] = {
                "listing_id": item["listing_id"],
                "community": item["community"],
                "url": item["url"],
                "observed_at": item["observed_at"],
                "current_ask_wan": item["price_wan"],
                "status": "insufficient",
                "valuation": None,
                "future": None,
                "decision": None,
                "rank": None,
                "missing_evidence": [],
            }
            peers = [other for other in candidates if not possible_same_home(candidate, other)]
            listing_baseline = self.baseline(candidate, peers, as_of)
            liquidity = self.market_evidence.liquidity(item, as_of, self.market_config)
            liquidity_score = liquidity["score"] if liquidity else None
            row["liquidity_evidence"] = liquidity
            if listing_baseline is not None and liquidity_score is not None:
                listing_baseline = replace(listing_baseline, liquidity_score=liquidity_score)
            transaction_peers = [
                other for other in transactions if not possible_same_home(candidate, other)
            ]
            baseline = (
                self.baseline(
                    candidate,
                    transaction_peers,
                    as_of,
                    observation_type="transaction",
                    window_days=180,
                )
                or listing_baseline
            )
            peers += transaction_peers
            row["verified_evidence"] = item.get("verified_evidence", [])
            row["property_facts"] = {
                key: item.get(key)
                for key in (
                    "district",
                    "submarket",
                    "area_sqm",
                    "price_wan",
                    "bedrooms",
                    "elevator",
                    "floor",
                    "year_built",
                    "property_type",
                )
            }
            row["transaction_evidence_count"] = len(transaction_peers)
            row["baseline_evidence"] = asdict(baseline) if baseline else None
            row["location_evidence"] = item.get("geocode")
            factors, projects = self.evidence.future(item, as_of)
            supplied_factors, centers, supplied_projects = self.future_evidence.inputs(item, as_of)
            factor_map = {factor.factor: factor for factor in factors}
            factor_map.update({factor.factor: factor for factor in supplied_factors})
            factors = tuple(factor_map.values())
            project_map = {(project.name, project.project_type): project for project in projects}
            project_map.update(
                {(project.name, project.project_type): project for project in supplied_projects}
            )
            projects = tuple(project_map.values())
            row["employment_evidence"] = [asdict(center) for center in centers]
            row["external_evidence"] = [asdict(factor) for factor in factors]
            try:
                valuation = valuation_engine.evaluate(
                    EvaluationInputs(
                        target=target,
                        candidates=peers,
                        valuation_baseline=baseline,
                        listing_baseline=listing_baseline,
                        price_history=build_price_history(
                            candidate.total_price,
                            prices[0][0],
                            prices,
                            cuts,
                            [],
                            as_of,
                        ),
                        latest_observation_id=candidate.observation_id,
                    ),
                    as_of,
                )
            except InsufficientValuationEvidenceError:
                row["missing_evidence"] = ["没有足够的可比挂牌，暂不估价"]
                results.append(row)
                continue
            future = future_engine.evaluate(
                FutureInputs(
                    property=FutureProperty(
                        listing_id=listing_uuid(SOURCE_ID, item["listing_id"]),
                        district=item["district"],
                        submarket=item["submarket"],
                        community=item["community"],
                        area_sqm=candidate.area_sqm,
                        fair_value=valuation.fair_value,
                        value_score=valuation.value_score,
                        valuation_version=valuation.valuation_version,
                        baseline_version=valuation.baseline_version,
                        baseline_confidence=valuation.baseline_confidence,
                        valuation_confidence=valuation.valuation_confidence,
                        transaction_support=valuation.transaction_support,
                        data_mode="sample",
                        longitude=Decimal(str(item["longitude"]))
                        if item.get("longitude") is not None
                        else None,
                        latitude=Decimal(str(item["latitude"]))
                        if item.get("latitude") is not None
                        else None,
                        year_built=item.get("year_built"),
                        elevator=item.get("elevator"),
                        bedrooms=item.get("bedrooms"),
                        building_type=item.get("building_type"),
                    ),
                    factors=factors,
                    projects=projects,
                    employment_centers=centers,
                    data_version=self.key(feed),
                    data_timestamp=as_of,
                ),
                as_of,
            )
            decision = decision_engine.evaluate(
                DecisionInputs(
                    listing_id=future.listing_id,
                    data_mode="sample",
                    current_ask=valuation.current_ask,
                    fair_value=valuation.fair_value,
                    fair_value_low=valuation.fair_value_low,
                    fair_value_high=valuation.fair_value_high,
                    ask_discount_to_fair_value=valuation.ask_discount_to_fair_value,
                    value_score=valuation.value_score,
                    liquidity_score=liquidity_score,
                    valuation_confidence=valuation.valuation_confidence,
                    baseline_confidence=valuation.baseline_confidence,
                    future_score=future.future_score,
                    future_score_coverage=future.future_score_coverage,
                    future_confidence=future.confidence,
                    obsolescence_risk=future.obsolescence_risk,
                    obsolescence_coverage=future.obsolescence_coverage,
                    structural_alpha=future.structural_alpha,
                    calibration_state=future.calibration_state,
                    valuation_warnings=tuple(valuation.warnings),
                    future_warnings=future.warnings,
                    factor_breakdown=tuple(asdict(factor) for factor in future.factors),
                    risk_breakdown=future.obsolescence_components,
                    valuation_version=valuation.valuation_version,
                    future_assessment_version=future.future_assessment_version,
                    baseline_version=valuation.baseline_version,
                    valuation_configuration_version=valuation.configuration_version,
                    future_configuration_version=future.configuration_version,
                    data_version=future.data_version,
                    data_timestamp=future.data_timestamp,
                )
            )
            decisions.append(decision)
            valuation_data = asdict(valuation)
            for comparable in valuation_data["comparables"]:
                source = sources[comparable["listing_id"]]
                comparable.update(
                    community=source["community"],
                    url=source["url"],
                    source_record_id=source["listing_id"],
                )
            row.update(
                status="evaluated",
                valuation=valuation_data,
                future=asdict(future),
                decision=asdict(decision),
                reference_low_wan=valuation.fair_value_low / WAN,
                reference_high_wan=valuation.fair_value_high / WAN,
                missing_evidence=(
                    (["同户型同面积段挂牌基线不足"] if baseline is None else [])
                    + (["小区地址尚未精确匹配，无法评估交通"] if not item.get("geocode") else [])
                    + (
                        [f"未来因子覆盖 {future.future_score_coverage:.0%}，未达到现有门槛"]
                        if future.future_score is None
                        else []
                    )
                    + (["老化风险证据不足"] if future.obsolescence_risk is None else [])
                    + (
                        ["缺少可比成交证据"]
                        if valuation.transaction_support in {"none", "insufficient"}
                        else []
                    )
                    + ["独立模型校准未完成"]
                    + (["缺少完整流动性观测"] if liquidity_score is None else [])
                ),
            )
            results.append(row)
        ranks = {
            str(entry.evaluation.listing_id): entry.rank
            for entry in rank_eligible(decisions, self.decision_config)
        }
        for row in results:
            row["rank"] = ranks.get(str(listing_uuid(SOURCE_ID, row["listing_id"])))
        evaluated = sum(row["valuation"] is not None for row in results)
        changes = feed["metadata"].get("changes", [])
        summary = {
            "observed": len(items),
            "evaluated": evaluated,
            "without_comparables": len(items) - evaluated,
            "eligible": len(ranks),
            "with_baseline": sum(row.get("baseline_evidence") is not None for row in results),
            "with_coordinates": evidence_summary["geocoded"],
            "transaction_records": len(transactions),
            "price_cuts": sum(row["event"] == "ASKING_PRICE_DECREASED" for row in changes),
            "first_seen": sum(row["event"] == "FIRST_SEEN_IN_PILOT" for row in changes),
        }
        return dict(
            safe_json(
                {
                    "input_fingerprint": self.key(feed),
                    "feed_fingerprint": fingerprint(feed),
                    "pipeline_version": PIPELINE_VERSION,
                    "configuration_fingerprint": self.version,
                    "generated_at": datetime.now(UTC),
                    "observed_at": observation_as_of,
                    "evidence_cutoff_at": as_of,
                    "source_id": SOURCE_ID,
                    "data_mode": "sample",
                    "usage": "private_research_only",
                    "status": "complete",
                    "recommendation_status": "research_uncalibrated",
                    "summary": summary,
                    "evidence_summary": evidence_summary,
                    "items": results,
                    "digest": f"本次观测 {len(items)} 条挂牌，{evaluated} 条可计算挂牌参考区间，"
                    f"{len(items) - evaluated} 条缺少可比挂牌。"
                    f"降价 {summary['price_cuts']} 条，首次见到 {summary['first_seen']} 条。"
                    f"其中 {summary['with_baseline']} 条已接入挂牌基线，"
                    f"{summary['with_coordinates']} 条匹配小区坐标。"
                    f"通过推荐门槛 {len(ranks)} 条。逐套缺口见查看依据。",
                    "limitations": [
                        "仅使用同一筛选页的公开挂牌，存在价格筛选偏差，不代表全市场成交价值",
                        "参考区间来自现有 P4 引擎；未来评估与决策使用现有 P5 / P5.5 门槛",
                        "坐标与地铁使用公开地图证据；人口和房屋存量仅作为低置信度区域代理指标，不是买家、成交或未来供给实测",
                        "未将规划中心权重当作就业实测；外部成交仅接受附来源与人工核验记录的导入，不自动视为模型校准完成",
                        "挂牌历史仅表示首次被本监控观察到的时间，不是真实挂牌天数",
                    ],
                }
            )
        )

    def baseline(
        self,
        target: ComparableRecord,
        peers: list[ComparableRecord],
        as_of: datetime,
        *,
        observation_type: str = "listing",
        window_days: int = 30,
    ) -> BaselineEvidence | None:
        config = self.market_config
        bucket = config.area_bucket_for(target.area_sqm)
        peers = [
            peer
            for peer in peers
            if as_of - timedelta(days=window_days) <= peer.observed_at <= as_of
            and config.area_bucket_for(peer.area_sqm) == bucket
            and peer.resolved_layout == target.resolved_layout
        ]
        # De-duplicate repeated advertisements before computing a distribution.
        unique: list[ComparableRecord] = []
        for peer in peers:
            if not any(possible_same_home(peer, other) for other in unique):
                unique.append(peer)
        for level in ("community", "submarket", "district"):
            selected = [
                peer
                for peer in unique
                if peer.district == target.district
                and (level == "district" or peer.submarket == target.submarket)
                and (level != "community" or peer.community == target.community)
            ]
            if len(selected) < getattr(config.fallback, f"{level}_minimum_samples"):
                continue
            records = [
                ObservationRecord(
                    id=peer.observation_id,
                    observation_type=observation_type,
                    source=peer.source,
                    source_record_id=peer.source_record_id,
                    listing_id=peer.listing_id,
                    district=peer.district,
                    submarket=peer.submarket,
                    community=peer.community,
                    area_bucket=bucket,
                    layout=peer.resolved_layout,
                    total_price=peer.total_price,
                    unit_price=peer.unit_price,
                    monthly_rent=None,
                    rent_per_sqm=None,
                    index_value=None,
                    observed_at=peer.observed_at,
                    created_at=peer.observed_at,
                    coverage_complete=False,
                    metadata={},
                )
                for peer in selected
            ]
            key = BaselineSliceKey(
                observation_type,
                window_days,
                level,
                target.district,
                target.submarket if level != "district" else None,
                target.community if level == "community" else None,
                bucket,
                target.resolved_layout,
            )
            result = compute_slice(
                records, records, key, as_of - timedelta(days=window_days), as_of, config
            )
            return BaselineEvidence(
                observation_type,
                level,
                result.confidence.level.value,
                result.effective_unit_price_sample_count,
                result.unit_price.p25,
                result.unit_price.p50,
                result.unit_price.p75,
                result.price.p50,
                None,
                (fingerprint([record.id for record in records]),),
                (level,),
                (
                    "同页价格筛选样本；排除自身及疑似重复；不代表完整市场"
                    if observation_type == "listing"
                    else "人工核验成交样本；排除自身及疑似重复；覆盖不完整"
                ),
                as_of,
            )
        return None

    def comparable(self, item: dict[str, Any]) -> ComparableRecord:
        price = Decimal(str(item["price_wan"])) * WAN
        area = Decimal(str(item["area_sqm"]))
        return ComparableRecord(
            observation_id=uuid.uuid5(uuid.NAMESPACE_URL, fingerprint(item)),
            listing_id=listing_uuid(SOURCE_ID, item["listing_id"]),
            observation_type="listing",
            source=SOURCE_ID,
            source_record_id=item["listing_id"],
            observed_at=datetime.fromisoformat(item["observed_at"]),
            source_confidence=Decimal("0.5"),
            district=item["district"],
            submarket=item["submarket"],
            community=item["community"],
            area_sqm=area,
            total_price=price,
            unit_price=price / area,
            **attributes(item),
        )

    def history(
        self, feed: dict[str, Any], as_of: datetime
    ) -> dict[str, list[tuple[datetime, Decimal]]]:
        history: dict[str, list[tuple[datetime, Decimal]]] = {}
        for path in sorted((self.export_dir / "observations").glob("*.canonical.json")):
            previous = json.loads(path.read_text())
            if (
                previous.get("source_id") != feed["source_id"]
                or previous.get("scope") != feed["scope"]
            ):
                continue
            for item in previous["items"]:
                stamp = datetime.fromisoformat(item["observed_at"])
                if stamp <= as_of:
                    history.setdefault(item["listing_id"], []).append(
                        (stamp, Decimal(str(item["price_wan"])) * WAN)
                    )
        return history


def possible_same_home(a: ComparableRecord, b: ComparableRecord) -> bool:
    """Exclude the target and conservatively exclude its suspected duplicate advertisements."""
    return a.listing_id == b.listing_id or (
        a.community == b.community
        and a.bedrooms == b.bedrooms
        and a.floor == b.floor
        and a.orientation == b.orientation
        and abs(a.area_sqm - b.area_sqm) <= Decimal("0.3")
    )

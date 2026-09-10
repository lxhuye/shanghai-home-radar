#!/usr/bin/env python3
"""Local-only bridge from supervised browser exports; never fetches a website."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from home_radar_collector.feed import CanonicalFeedEnvelope, CanonicalListingItem

from scripts.build_calibration_set import normalize_record

SOURCE_ID = "anjuke_public_research_pilot"
IDENTITY_FIELDS = (
    "community",
    "district",
    "area_sqm",
    "bedrooms",
    "living_rooms",
    "floor",
    "total_floors",
    "year_built",
    "orientation",
)


def prepare(
    raw: list[dict[str, Any]], previous: dict[str, Any]
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Reuse the existing parser; preserve observation time and partial coverage."""
    if not raw:
        raise ValueError("empty observation is not evidence of delisting")
    baseline = {item["listing_id"]: item for item in previous["items"]}
    items: list[dict[str, Any]] = []
    changes: list[dict[str, Any]] = []
    seen: set[str] = set()
    for record in raw:
        observed_at = str(record["observed_at"])
        if datetime.fromisoformat(observed_at).utcoffset() is None:
            raise ValueError("observed_at must include timezone")
        url = urlsplit(record["source_url"])
        if url.scheme != "https" or url.hostname != "shanghai.anjuke.com":
            raise ValueError("unexpected public source URL")
        if url.username or url.password or not url.path.startswith("/prop/view/"):
            raise ValueError("invalid public listing URL")
        cleaned = dict(record, source_url=urlunsplit((url.scheme, url.netloc, url.path, "", "")))
        item = normalize_record(cleaned, observed_at)
        CanonicalListingItem.model_validate(item)
        listing_id = item["listing_id"]
        if listing_id in seen:
            raise ValueError(f"duplicate source listing: {listing_id}")
        seen.add(listing_id)
        old = baseline.get(listing_id)
        if old and datetime.fromisoformat(observed_at) <= datetime.fromisoformat(
            old["observed_at"]
        ):
            raise ValueError(f"observation is not newer than baseline: {listing_id}")
        identity_changes = {
            key: {"before": old.get(key), "after": item.get(key)}
            for key in IDENTITY_FIELDS
            if old and old.get(key) != item.get(key)
        }
        possible_duplicates = [
            candidate["listing_id"]
            for candidate in previous["items"]
            if candidate["listing_id"] != listing_id
            and all(
                candidate.get(key) == item.get(key)
                for key in ("district", "community", "bedrooms", "total_floors", "orientation")
            )
            and abs(Decimal(str(candidate["area_sqm"])) - Decimal(str(item["area_sqm"])))
            <= Decimal("0.3")
        ]
        delta = (
            None
            if old is None
            else Decimal(str(item["price_wan"])) - Decimal(str(old["price_wan"]))
        )
        event = (
            "FIRST_SEEN_IN_PILOT"
            if old is None
            else (
                "ASKING_PRICE_DECREASED"
                if delta is not None and delta < 0
                else "ASKING_PRICE_INCREASED"
                if delta is not None and delta > 0
                else "UNCHANGED_PRICE"
            )
        )
        item["provenance"] = {
            "method": "supervised_public_browser",
            "data_mode": "sample",
            "usage": "private_research_only",
            "source_url": item["url"],
            "observed_at": observed_at,
            "search_page": item["search_page"],
            "raw_claims_verified": False,
        }
        items.append(item)
        changes.append(
            {
                "listing_id": listing_id,
                "community": item["community"],
                "area_sqm": item["area_sqm"],
                "bedrooms": item["bedrooms"],
                "event": event,
                "before_wan": old["price_wan"] if old else None,
                "after_wan": item["price_wan"],
                "delta_wan": str(delta) if delta is not None else None,
                "observed_at": observed_at,
                "source_url": item["url"],
                "identity_changes": identity_changes,
                "possible_duplicate_ids": possible_duplicates,
            }
        )
    envelope = {
        "schema_version": "1.0",
        "source_id": SOURCE_ID,
        "scope": {"city": "shanghai", "query": "private-monitor-pilot"},
        "completeness": "partial",
        "coverage": {
            "pages_fetched": len({item["search_page"] for item in items}),
            "items_returned": len(items),
        },
        "metadata": {
            "data_mode": "sample",
            "usage": "private_research_only",
            "collection_method": "supervised_public_browser",
            "validation_set": False,
            "unattended_monitoring_enabled": False,
        },
        "items": items,
    }
    CanonicalFeedEnvelope.model_validate(envelope)
    return envelope, changes


def report(changes: list[dict[str, Any]]) -> str:
    fresh = [row for row in changes if row["event"] == "FIRST_SEEN_IN_PILOT"]
    changed = [row for row in changes if row["event"].startswith("ASKING_PRICE_")]
    cuts = sum(row["event"] == "ASKING_PRICE_DECREASED" for row in changed)
    lines = [
        "# 公开挂牌观测小样本",
        "",
        f"观测时间 {changes[0]['observed_at']}。共 {len(changes)} 条，"
        f"旧池外首次见到 {len(fresh)} 条；"
        f"同标识挂牌降价 {cuts} 条、涨价 {len(changed) - cuts} 条。",
        "",
        "这是个人研究观测，不是成交价、已核实真房源或买入推荐。"
        "首次见到不等于今天挂牌。只看了一个筛选页面，未出现的旧记录不视为下架。",
        "",
        "持续自动监控尚未启用。原冻结校准集和模型未修改；本文件未写入生产数据库。",
        "",
        "## 同一来源标识的价格变化",
        "",
        "| 房源 | 面积 | 原挂牌万 | 本次挂牌万 | 需核对 |",
        "| --- | ---: | ---: | ---: | --- |",
    ]
    for row in sorted(changed, key=lambda value: Decimal(str(value["delta_wan"]))):
        flags = ", ".join(row["identity_changes"]) or "公开挂牌，真实性仍待核实"
        lines.append(
            f"| [{row['community']}]({row['source_url']}) | {row['area_sqm']} | "
            f"{row['before_wan']} | {row['after_wan']} | {flags} |"
        )
    lines += [
        "",
        "楼龄、朝向等字段同时改变的记录另有字段变更明细，不能只按价格变化解读。",
        "",
        "## 旧池外首次见到",
        "",
        "以下按小区名称排列，没有模型评分或机会排名。",
        "",
        "| 房源 | 房间 | 面积 | 挂牌万 | 同物理房源待排查 |",
        "| --- | ---: | ---: | ---: | --- |",
    ]
    for row in sorted(fresh, key=lambda value: (value["community"], value["listing_id"])):
        duplicate = "是" if row["possible_duplicate_ids"] else "未发现，不代表排除"
        lines.append(
            f"| [{row['community']}]({row['source_url']}) | {row['bedrooms']} | "
            f"{row['area_sqm']} | {row['after_wan']} | {duplicate} |"
        )
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--raw", required=True, type=Path)
    parser.add_argument("--baseline", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    raw_bytes, baseline_bytes = args.raw.read_bytes(), args.baseline.read_bytes()
    envelope, changes = prepare(json.loads(raw_bytes), json.loads(baseline_bytes))
    args.output.mkdir(parents=True, exist_ok=False)
    outputs = {"observations.canonical.json": envelope, "changes.json": changes}
    for name, content in outputs.items():
        (args.output / name).write_text(json.dumps(content, ensure_ascii=False, indent=2) + "\n")
    (args.output / "REPORT.md").write_text(report(changes), encoding="utf-8")
    with (args.output / "changes.csv").open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(changes[0]))
        writer.writeheader()
        for change in changes:
            writer.writerow(
                {
                    key: json.dumps(value, ensure_ascii=False)
                    if isinstance(value, (list, dict))
                    else value
                    for key, value in change.items()
                }
            )
    manifest = {
        "raw_sha256": hashlib.sha256(raw_bytes).hexdigest(),
        "baseline_sha256": hashlib.sha256(baseline_bytes).hexdigest(),
        "count": len(changes),
        "source_id": SOURCE_ID,
        "model_run": False,
        "database_imported": False,
        "unattended_monitoring_enabled": False,
        "output_sha256": {
            path.name: hashlib.sha256(path.read_bytes()).hexdigest()
            for path in sorted(args.output.iterdir())
        },
    }
    (args.output / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print(json.dumps(manifest))


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Normalize supervised public-page observations into research-only artifacts."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

ROOM_PATTERN = re.compile(
    r"^(?P<title>.*?)\s+"
    r"(?P<bedrooms>\d+)\s*室\s+(?P<living_rooms>\d+)\s*厅\s+(?P<bathrooms>\d+)\s*卫\s+"
    r"(?P<area>[\d.]+)㎡\s+(?P<orientation>[东南西北]+)\s+"
    r"(?P<floor>(?:(?:低|中|高)层\(共\d+层\)|共\d+层))\s+"
    r"(?P<year>\d{4})年建造\s+(?P<location>.*?)\s+"
    r"(?P<price>[\d.]+)\s*万\s+(?P<unit_price>\d+)元/㎡$"
)
TOTAL_FLOORS_PATTERN = re.compile(r"共(?P<total>\d+)层")
METRO_DISTANCE_PATTERN = re.compile(r"(?:距地铁(?:仅)?|地铁)(?P<distance>\d{2,4})m", re.IGNORECASE)
TAG_WORDS = (
    "官方验真",
    "政府平台权属核验",
    "房东发布",
    "经纪人力荐",
    "热门小区",
    "房东急售",
    "随时可看",
    "唯一住房",
    "满五年",
    "满五",
    "满二年",
    "满二",
    "近地铁",
    "有电梯",
    "次新房",
    "车位充足",
    "采光较好",
    "南北通透",
    "户型方正",
    "配套成熟",
    "价格优惠",
    "交易周期可谈",
    "新上小区",
    "绿化率高",
    "优势户型",
    "公摊小",
    "多人关注",
)
SUBMARKETS = {
    "徐汇": [
        "漕宝路站",
        "漕河泾",
        "长桥",
        "复兴中路",
        "衡山路",
        "华东理工",
        "淮海西路",
        "华泾",
        "湖南路",
        "建国西路",
        "康健",
        "龙华",
        "上海南站",
        "上海师大",
        "上海植物园",
        "田林",
        "万体馆",
        "襄阳公园",
        "斜土路",
        "徐汇滨江",
        "徐家汇",
        "肇嘉浜路站",
    ],
    "闵行": [
        "北桥",
        "漕宝路",
        "春申",
        "古美罗阳",
        "航华",
        "虹桥",
        "华漕",
        "江川路",
        "静安新城",
        "金虹桥",
        "老闵行",
        "龙柏金汇",
        "马桥",
        "梅陇",
        "浦江",
        "七宝",
        "七莘路",
        "吴泾",
        "莘庄北广场",
        "莘庄工业区",
        "莘庄南广场",
        "颛桥",
        "诸翟",
    ],
    "普陀": [
        "曹杨",
        "长风",
        "长寿路",
        "长征",
        "甘泉宜川",
        "光新",
        "华东师大",
        "环球港",
        "李子园",
        "轻纺市场",
        "桃浦",
        "万里城",
        "武宁",
        "真光",
        "真如",
        "中远两湾城",
    ],
    "杨浦": [
        "鞍山",
        "长白新村",
        "长阳路",
        "东外滩",
        "黄兴",
        "江浦路",
        "控江路",
        "平凉路",
        "五角场北",
        "五角场",
        "新华医院",
        "新江湾城",
        "杨浦大桥",
        "杨浦公园",
        "运光/复旦",
        "中原",
        "周家嘴路",
    ],
    "浦东": [
        "八佰伴",
        "北蔡",
        "碧云",
        "曹路",
        "川沙",
        "大团",
        "东昌路站",
        "高东",
        "高桥",
        "高行",
        "航头",
        "合庆",
        "花木",
        "惠南",
        "江镇",
        "金桥",
        "金杨新村",
        "康桥",
        "老港",
        "联洋",
        "临港新城",
        "龙阳路站",
        "芦潮港",
        "陆家嘴",
        "梅园",
        "南码头",
        "泥城",
        "前滩",
        "三林",
        "三灶",
        "上钢新村",
        "上南",
        "世博滨江",
        "世博",
        "世纪公园",
        "书院",
        "塘桥",
        "唐镇",
        "外高桥",
        "王港",
        "万祥",
        "潍坊新村",
        "新场",
        "宣桥",
        "杨东",
        "洋泾",
        "杨思",
        "源深",
        "御桥",
        "张江",
        "周浦",
        "祝桥",
        "竹园",
    ],
}
CSV_FIELDS = [
    "source",
    "source_listing_id",
    "source_url",
    "observed_at",
    "validation_role",
    "district",
    "submarket",
    "community",
    "total_price_wan",
    "area_sqm",
    "unit_price",
    "bedrooms",
    "living_rooms",
    "bathrooms",
    "floor_position",
    "total_floors",
    "orientation",
    "year_built",
    "elevator",
    "metro_station",
    "metro_distance_m",
    "listing_date",
    "views_30d",
    "showings_30d",
    "government_verified",
    "property_type",
    "title",
    "description",
    "tags",
    "search_page",
]


def split_location(location: str, district: str) -> tuple[str, str, str]:
    marker = f" {district}"
    marker_index = location.find(marker)
    if marker_index < 0:
        raise ValueError(f"district marker missing for {district}: {location}")
    community = location[:marker_index].strip()
    district_tail = location[marker_index + len(marker) :].strip()
    submarket = next(
        (
            name
            for name in sorted(SUBMARKETS[district], key=len, reverse=True)
            if district_tail.startswith(name)
        ),
        None,
    )
    if submarket is None:
        raise ValueError(f"submarket missing for {district}: {district_tail}")
    address_and_tags = district_tail[len(submarket) :].strip()
    tag_starts = [address_and_tags.find(tag) for tag in TAG_WORDS if tag in address_and_tags]
    address_end = min(tag_starts) if tag_starts else len(address_and_tags)
    return community, submarket, address_and_tags[:address_end].strip()


def normalize_record(raw: dict[str, Any], observed_at: str) -> dict[str, Any]:
    text = str(raw["raw_text"]).strip()
    match = ROOM_PATTERN.fullmatch(text)
    if match is None:
        raise ValueError(f"unrecognized listing card: {text[:120]}")
    values = match.groupdict()
    district = str(raw["district_expected"])
    community, submarket, address = split_location(values["location"], district)
    source_url = str(raw["source_url"])
    listing_id = source_url.rstrip("/").rsplit("/", 1)[-1]
    title = re.sub(r"^(?:VR看房\s+)+", "", values["title"]).strip()
    total_floor_match = TOTAL_FLOORS_PATTERN.search(values["floor"])
    source_year_built = int(values["year"])
    year_built = None if source_year_built == 1901 else source_year_built
    tags = [tag for tag in TAG_WORDS if tag in text]
    metro_match = METRO_DISTANCE_PATTERN.search(text)
    government_verified = (
        True if any(tag in text for tag in ("官方验真", "政府平台权属核验")) else None
    )
    elevator = True if "有电梯" in text else None
    return {
        "listing_id": listing_id,
        "url": source_url,
        "district": district,
        "submarket": submarket,
        "community": community,
        "price_wan": float(values["price"]),
        "area_sqm": float(values["area"]),
        "bedrooms": int(values["bedrooms"]),
        "living_rooms": int(values["living_rooms"]),
        "floor": values["floor"],
        "total_floors": int(total_floor_match.group("total")) if total_floor_match else None,
        "orientation": values["orientation"],
        "year_built": year_built,
        "elevator": elevator,
        "building_type": None,
        "status": "active",
        "observed_at": observed_at,
        "unit_price_yuan_sqm": int(values["unit_price"]),
        "bathrooms": int(values["bathrooms"]),
        "address": address or None,
        "metro_station": None,
        "metro_distance_m": int(metro_match.group("distance")) if metro_match else None,
        "listing_date": None,
        "views_30d": None,
        "showings_30d": None,
        "government_verified": government_verified,
        "property_type": None,
        "title": title,
        "description": None,
        "tags": tags,
        "search_page": raw["search_page"],
        "source_year_built_raw": source_year_built,
        "source_raw_text": text,
    }


def target_ids(items: list[dict[str, Any]], seed: str) -> set[str]:
    selected: set[str] = set()
    for district in SUBMARKETS:
        district_items = [item for item in items if item["district"] == district]
        ranked = sorted(
            district_items,
            key=lambda item: hashlib.sha256(f"{seed}:{item['listing_id']}".encode()).hexdigest(),
        )
        selected.update(item["listing_id"] for item in ranked[:10])
    return selected


def csv_row(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "source": "anjuke_public_research",
        "source_listing_id": item["listing_id"],
        "source_url": item["url"],
        "observed_at": item["observed_at"],
        "validation_role": item["validation_role"],
        "district": item["district"],
        "submarket": item["submarket"],
        "community": item["community"],
        "total_price_wan": item["price_wan"],
        "area_sqm": item["area_sqm"],
        "unit_price": item["unit_price_yuan_sqm"],
        "bedrooms": item["bedrooms"],
        "living_rooms": item["living_rooms"],
        "bathrooms": item["bathrooms"],
        "floor_position": item["floor"],
        "total_floors": item["total_floors"],
        "orientation": item["orientation"],
        "year_built": item["year_built"],
        "elevator": item["elevator"],
        "metro_station": item["metro_station"],
        "metro_distance_m": item["metro_distance_m"],
        "listing_date": item["listing_date"],
        "views_30d": item["views_30d"],
        "showings_30d": item["showings_30d"],
        "government_verified": item["government_verified"],
        "property_type": item["property_type"],
        "title": item["title"],
        "description": item["description"],
        "tags": "|".join(item["tags"]),
        "search_page": item["search_page"],
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("raw", type=Path)
    parser.add_argument("output_dir", type=Path)
    parser.add_argument("--seed", default="calibration-set-a-2026-09-02")
    args = parser.parse_args()

    raw_bytes = args.raw.read_bytes()
    raw_records = json.loads(raw_bytes)
    if not isinstance(raw_records, list):
        raise ValueError("raw browser export must be an array")
    observed_at = datetime.fromtimestamp(
        args.raw.stat().st_mtime,
        tz=ZoneInfo("Asia/Shanghai"),
    ).isoformat(timespec="seconds")
    items = [normalize_record(record, observed_at) for record in raw_records]
    if len(items) != 500 or len({item["listing_id"] for item in items}) != 500:
        raise ValueError("calibration pool must contain exactly 500 unique listings")
    district_counts = Counter(item["district"] for item in items)
    if set(district_counts.values()) != {100}:
        raise ValueError(f"expected 100 listings per district, got {district_counts}")

    selected_targets = target_ids(items, args.seed)
    for item in items:
        item["validation_role"] = "TARGET" if item["listing_id"] in selected_targets else "CONTEXT"

    args.output_dir.mkdir(parents=True, exist_ok=True)
    envelope_path = args.output_dir / "market_pool.canonical.json"
    csv_path = args.output_dir / "market_pool.csv"
    manifest_path = args.output_dir / "manifest.json"

    envelope = {
        "schema_version": "1.0",
        "source_id": "sample_json",
        "scope": {"city": "shanghai"},
        "completeness": "partial",
        "coverage": {"page_count": 10, "pages_fetched": 10, "items_returned": len(items)},
        "metadata": {
            "dataset": "Shanghai Home Radar Calibration Set A",
            "source_kind": "public_research",
            "data_mode": "sample",
            "allowed_use": "internal_research_and_calibration_only",
            "collection_method": "low_frequency_supervised_browser",
            "observed_at": observed_at,
            "raw_sha256": hashlib.sha256(raw_bytes).hexdigest(),
            "target_selection_seed": args.seed,
            "target_count": len(selected_targets),
            "context_count": len(items) - len(selected_targets),
        },
        "items": items,
    }
    envelope_path.write_text(
        json.dumps(envelope, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    with csv_path.open("w", encoding="utf-8-sig", newline="") as csv_file:
        writer = csv.DictWriter(csv_file, fieldnames=CSV_FIELDS)
        writer.writeheader()
        writer.writerows(csv_row(item) for item in items)
    manifest = {
        "dataset": envelope["metadata"]["dataset"],
        "data_mode": "SAMPLE",
        "records": len(items),
        "targets": len(selected_targets),
        "context": len(items) - len(selected_targets),
        "district_counts": dict(sorted(district_counts.items())),
        "observed_at": observed_at,
        "raw_sha256": envelope["metadata"]["raw_sha256"],
        "canonical_sha256": hashlib.sha256(envelope_path.read_bytes()).hexdigest(),
        "source_pages": sorted({item["search_page"] for item in items}),
        "parse_failures": 0,
        "notes": [
            "Public-page observations are research data, not an authorized production feed.",
            "Missing fields remain null; absence of an elevator tag is not treated as no elevator.",
            "The provider's 1901 construction-year placeholder is retained as raw evidence "
            "and normalized to null.",
        ],
    }
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(manifest, ensure_ascii=False))


if __name__ == "__main__":
    main()

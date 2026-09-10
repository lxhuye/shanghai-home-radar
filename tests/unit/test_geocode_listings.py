from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from scripts import geocode_listings as geocoder


def _item(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "listing_id": "L1",
        "district": "徐汇",
        "submarket": "康健",
        "community": "寿山坊",
        "address": "桂林西街201弄1-51号",
        "price_wan": 299.0,
    }
    base.update(overrides)
    return base


def _amap_payload(
    *, level: str = "门牌号", district: str = "徐汇区", location: str = "121.4300,31.1700"
) -> dict[str, Any]:
    return {
        "status": "1",
        "count": "1",
        "geocodes": [
            {
                "formatted_address": "上海市徐汇区桂林西街201弄",
                "district": district,
                "adcode": "310104",
                "location": location,
                "level": level,
            }
        ],
    }


def _nominatim_payload(
    *, district: str = "徐汇区", addresstype: str = "residential"
) -> dict[str, Any]:
    return {
        "query": "寿山坊,徐汇区,上海市,中国",
        "results": [
            {
                "lat": "31.1541187",
                "lon": "121.4178547",
                "category": "landuse",
                "type": "residential",
                "addresstype": addresstype,
                "name": "寿山坊",
                "osm_type": "way",
                "osm_id": 1430475325,
                "display_name": f"寿山坊, 康健新村街道, {district}, 上海市, 中国",
                "licence": "Data © OpenStreetMap contributors, ODbL 1.0",
                "address": {
                    "residential": "寿山坊",
                    "city": district,
                    "state": "上海市",
                    "country_code": "cn",
                },
            }
        ],
    }


def test_gcj02_round_trip_is_sub_metre() -> None:
    lon, lat = 121.4372, 31.1946
    gcj_lon, gcj_lat = geocoder.wgs84_to_gcj02(lon, lat)
    back_lon, back_lat = geocoder.gcj02_to_wgs84(gcj_lon, gcj_lat)

    assert abs(gcj_lon - lon) > 0.001  # the offset is real (hundreds of metres)
    assert abs(back_lon - lon) < 1e-6
    assert abs(back_lat - lat) < 1e-6


def test_distinct_queries_deduplicate_by_address() -> None:
    items = [_item(), _item(listing_id="L2"), _item(listing_id="L3", address="上中西路151弄")]

    queries = geocoder.distinct_queries(items)

    assert len(queries) == 2
    assert queries[0].candidates() == ("上海市徐汇区桂林西街201弄1-51号", "上海市徐汇区寿山坊")


def test_fetch_is_idempotent_and_falls_back_to_community_query() -> None:
    calls: list[str] = []

    def provider(query: str) -> Mapping[str, Any]:
        calls.append(query)
        if query.endswith("寿山坊"):
            return _amap_payload(level="兴趣点")
        return {"status": "1", "count": "0", "geocodes": []}

    cache: dict[str, Any] = {"geocoder_version": geocoder.GEOCODER_VERSION, "entries": {}}
    queries = geocoder.distinct_queries([_item()])

    first = geocoder.fetch_missing(
        queries,
        cache,
        provider=provider,
        parse_answer=geocoder.parse_amap_answer,
        provider_name="amap",
        qps=0,
    )
    second = geocoder.fetch_missing(
        queries,
        cache,
        provider=provider,
        parse_answer=geocoder.parse_amap_answer,
        provider_name="amap",
        qps=0,
    )

    assert first == {"cached": 0, "resolved": 1, "unresolved": 0}
    assert second == {"cached": 1, "resolved": 0, "unresolved": 0}
    assert calls == ["上海市徐汇区桂林西街201弄1-51号", "上海市徐汇区寿山坊"]
    entry = cache["entries"][queries[0].cache_key]
    assert entry["answer"]["query"] == "上海市徐汇区寿山坊"
    assert entry["answer"]["level"] == "兴趣点"
    assert len(entry["attempts"]) == 2


def test_nominatim_answer_is_wgs84_and_district_scoped() -> None:
    answer = geocoder.parse_nominatim_answer(_nominatim_payload())

    assert answer is not None
    assert answer["datum"] == "WGS84"
    assert answer["district"] == "徐汇区"
    assert answer["level"] == "residential"
    assert answer["osm_id"] == 1430475325


def test_nominatim_prefers_city_district_level_over_subdistrict() -> None:
    payload = _nominatim_payload()
    address = payload["results"][0]["address"]
    address["city_district"] = "田林街道"

    answer = geocoder.parse_nominatim_answer(payload)

    assert answer is not None
    assert answer["district"] == "徐汇区"


def test_loading_cache_reparses_derived_answer_without_network(tmp_path: Path) -> None:
    query = geocoder.distinct_queries([_item()])[0]
    cache_path = tmp_path / "cache.json"
    cache_path.write_text(
        json.dumps(
            {
                "provider": "nominatim",
                "entries": {
                    query.cache_key: {
                        "answer": {"district": "old-wrong-value"},
                        "attempts": [
                            {
                                "query": query.nominatim_candidates()[0],
                                "raw": _nominatim_payload(),
                                "parsed": None,
                            }
                        ],
                    }
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    cache = geocoder.load_cache(cache_path)

    assert cache["entries"][query.cache_key]["answer"]["district"] == "徐汇区"


def test_nominatim_candidates_prefer_community_and_fetch_accepts_selector() -> None:
    query = geocoder.distinct_queries([_item()])[0]
    calls: list[str] = []
    cache: dict[str, Any] = {"geocoder_version": geocoder.GEOCODER_VERSION, "entries": {}}

    geocoder.fetch_missing(
        [query],
        cache,
        provider=lambda value: calls.append(value) or _nominatim_payload(),
        parse_answer=geocoder.parse_nominatim_answer,
        provider_name="nominatim",
        qps=0,
        candidate_selector=geocoder.GeocodeQuery.nominatim_candidates,
    )

    assert calls == ["寿山坊,徐汇区,上海市,中国"]
    assert cache["entries"][query.cache_key]["answer"]["provider"] == "nominatim"


def test_fetch_retries_transient_provider_error(monkeypatch: Any) -> None:
    query = geocoder.distinct_queries([_item()])[0]
    calls = 0
    sleeps: list[float] = []

    def provider(_: str) -> Mapping[str, Any]:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise geocoder.ProviderRequestError("temporary TLS error")
        return _amap_payload()

    monkeypatch.setattr(geocoder.time, "sleep", sleeps.append)
    cache: dict[str, Any] = {"entries": {}}
    counts = geocoder.fetch_missing(
        [query],
        cache,
        provider=provider,
        parse_answer=geocoder.parse_amap_answer,
        provider_name="amap",
        qps=1,
    )

    assert counts["resolved"] == 1
    assert calls == 2
    assert sleeps == [1.0, 1.0]


def test_apply_writes_wgs84_and_provenance_and_rejects_coarse_levels() -> None:
    precise = _item()
    coarse = _item(listing_id="L2", address="漕溪路")
    wrong_district = _item(listing_id="L3", address="某某路1号")
    unfetched = _item(listing_id="L4", address="未查询路9号")
    cache: dict[str, Any] = {"geocoder_version": geocoder.GEOCODER_VERSION, "entries": {}}

    def provider(query: str) -> Mapping[str, Any]:
        if "漕溪路" in query:
            return _amap_payload(level="道路")
        if "某某路" in query:
            return _amap_payload(district="闵行区")
        return _amap_payload()

    geocoder.fetch_missing(
        geocoder.distinct_queries([precise, coarse, wrong_district]),
        cache,
        provider=provider,
        parse_answer=geocoder.parse_amap_answer,
        provider_name="amap",
        qps=0,
    )

    enriched, summary = geocoder.enrich_items(
        [precise, coarse, wrong_district, unfetched],
        cache,
        accept_levels=geocoder.DEFAULT_ACCEPT_LEVELS,
    )

    assert summary["geocoded"] == 1
    assert [r["reason"] for r in summary["rejected_items"]] == [
        "level='道路'",
        "district_mismatch='闵行区'",
        "not_fetched",
    ]
    geocoded = enriched[0]
    expected_lon, expected_lat = geocoder.gcj02_to_wgs84(121.43, 31.17)
    assert geocoded["longitude"] == round(expected_lon, 6)
    assert geocoded["latitude"] == round(expected_lat, 6)
    assert geocoded["geocode"]["source_datum"] == "GCJ-02"
    assert geocoded["geocode"]["output_datum"] == "WGS84"
    assert geocoded["geocode"]["level"] == "门牌号"
    assert "longitude" not in enriched[1]
    assert precise.get("longitude") is None  # input untouched


def test_apply_cli_writes_sibling_feed_with_metadata(tmp_path: Path) -> None:
    feed = tmp_path / "market_pool.canonical.json"
    feed.write_text(
        json.dumps({"metadata": {"name": "set-a"}, "items": [_item()]}, ensure_ascii=False),
        encoding="utf-8",
    )
    cache_path = tmp_path / "geocodes.json"
    cache: dict[str, Any] = {"geocoder_version": geocoder.GEOCODER_VERSION, "entries": {}}
    geocoder.fetch_missing(
        geocoder.distinct_queries([_item()]),
        cache,
        provider=lambda _query: _amap_payload(),
        parse_answer=geocoder.parse_amap_answer,
        provider_name="amap",
        qps=0,
    )
    geocoder.save_cache(cache_path, cache)
    output = tmp_path / "market_pool.canonical.geocoded.json"

    code = geocoder.main(
        [
            "apply",
            "--input",
            str(feed),
            "--cache",
            str(cache_path),
            "--output",
            str(output),
        ]
    )

    assert code == 0
    written = json.loads(output.read_text(encoding="utf-8"))
    assert written["metadata"]["name"] == "set-a"
    assert written["metadata"]["geocoding"]["geocoded"] == 1
    assert written["metadata"]["geocoding"]["cache_sha256"]
    assert written["items"][0]["longitude"] is not None
    assert json.loads(feed.read_text(encoding="utf-8"))["items"][0].get("longitude") is None


def test_fetch_cli_requires_key(tmp_path: Path, capsys: Any) -> None:
    feed = tmp_path / "feed.json"
    feed.write_text(json.dumps({"items": [_item()]}), encoding="utf-8")

    code = geocoder.main(
        ["fetch", "--input", str(feed), "--cache", str(tmp_path / "c.json"), "--key-env", "NOPE"]
    )

    assert code == 2
    assert "NOPE is not set" in capsys.readouterr().out


def test_apply_refuses_to_overwrite_the_source_feed(tmp_path: Path, capsys: Any) -> None:
    feed = tmp_path / "feed.json"
    original = json.dumps({"items": [_item()]})
    feed.write_text(original, encoding="utf-8")
    cache = tmp_path / "cache.json"
    cache.write_text(json.dumps({"entries": {}}), encoding="utf-8")

    code = geocoder.main(
        ["apply", "--input", str(feed), "--cache", str(cache), "--output", str(feed)]
    )

    assert code == 2
    assert "must differ" in capsys.readouterr().out
    assert feed.read_text(encoding="utf-8") == original

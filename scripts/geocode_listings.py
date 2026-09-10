#!/usr/bin/env python3
"""Geocode canonical listing addresses and emit an enriched canonical feed.

Two idempotent stages:

``fetch``
    Resolve every distinct (district, community, address) through a geocoding provider and
    persist the raw provider answer in a JSON cache. Already-cached queries are never
    re-requested, so the command is resumable and repeat runs make no network calls.

``apply``
    Read the original canonical feed plus the cache and write a sibling feed in which items
    that resolved at an acceptable precision carry ``longitude``/``latitude`` (WGS-84) and a
    ``geocode`` provenance block. Unresolved items are copied unchanged and listed in the
    summary. The original canonical file is never modified.

Coordinates returned by AMap use the GCJ-02 datum and are converted to WGS-84 before being
written because ``listing.coordinates`` is SRID 4326. Nominatim already returns WGS-84 and
is available only for a cached, one-time calibration run at no more than one request/second.

Usage::

    # Preferred when an authorized AMap key is available.
    export SHR_GEOCODER_KEY=...
    python scripts/geocode_listings.py fetch \
        --input data/raw/calibration_set_a/market_pool.canonical.json \
        --cache data/raw/calibration_set_a/geocodes.amap.json

    # One-time public research fallback. Keep --qps at or below 1 and retain the cache.
    python scripts/geocode_listings.py fetch \
        --input data/raw/calibration_set_a/market_pool.canonical.json \
        --cache data/raw/calibration_set_a/geocodes.nominatim.json \
        --provider nominatim --qps 1

    python scripts/geocode_listings.py apply \
        --input data/raw/calibration_set_a/market_pool.canonical.json \
        --cache data/raw/calibration_set_a/geocodes.nominatim.json \
        --output data/raw/calibration_set_a/market_pool.canonical.geocoded.json
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import sys
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).parent.parent))

GEOCODER_VERSION = "listing-geocoder-v2"
DEFAULT_ACCEPT_LEVELS = ("门牌号", "单元号", "兴趣点")
AMAP_ENDPOINT = "https://restapi.amap.com/v3/geocode/geo"
NOMINATIM_ENDPOINT = "https://nominatim.openstreetmap.org/search"
NOMINATIM_USER_AGENT = "ShanghaiHomeRadar/0.1 (one-time Set A research calibration)"
NOMINATIM_ACCEPT_LEVELS = ("residential", "neighbourhood", "quarter", "building", "house")


# --------------------------------------------------------------------------- datum conversion

_A = 6378245.0
_EE = 0.00669342162296594323


def _out_of_china(lon: float, lat: float) -> bool:
    return not (72.004 <= lon <= 137.8347 and 0.8293 <= lat <= 55.8271)


def _transform_lat(x: float, y: float) -> float:
    ret = -100.0 + 2.0 * x + 3.0 * y + 0.2 * y * y + 0.1 * x * y + 0.2 * math.sqrt(abs(x))
    ret += (20.0 * math.sin(6.0 * x * math.pi) + 20.0 * math.sin(2.0 * x * math.pi)) * 2.0 / 3.0
    ret += (20.0 * math.sin(y * math.pi) + 40.0 * math.sin(y / 3.0 * math.pi)) * 2.0 / 3.0
    ret += (160.0 * math.sin(y / 12.0 * math.pi) + 320 * math.sin(y * math.pi / 30.0)) * 2.0 / 3.0
    return ret


def _transform_lon(x: float, y: float) -> float:
    ret = 300.0 + x + 2.0 * y + 0.1 * x * x + 0.1 * x * y + 0.1 * math.sqrt(abs(x))
    ret += (20.0 * math.sin(6.0 * x * math.pi) + 20.0 * math.sin(2.0 * x * math.pi)) * 2.0 / 3.0
    ret += (20.0 * math.sin(x * math.pi) + 40.0 * math.sin(x / 3.0 * math.pi)) * 2.0 / 3.0
    ret += (150.0 * math.sin(x / 12.0 * math.pi) + 300.0 * math.sin(x / 30.0 * math.pi)) * 2.0 / 3.0
    return ret


def wgs84_to_gcj02(lon: float, lat: float) -> tuple[float, float]:
    if _out_of_china(lon, lat):
        return lon, lat
    dlat = _transform_lat(lon - 105.0, lat - 35.0)
    dlon = _transform_lon(lon - 105.0, lat - 35.0)
    radlat = lat / 180.0 * math.pi
    magic = math.sin(radlat)
    magic = 1 - _EE * magic * magic
    sqrtmagic = math.sqrt(magic)
    dlat = (dlat * 180.0) / ((_A * (1 - _EE)) / (magic * sqrtmagic) * math.pi)
    dlon = (dlon * 180.0) / (_A / sqrtmagic * math.cos(radlat) * math.pi)
    return lon + dlon, lat + dlat


def gcj02_to_wgs84(lon: float, lat: float, *, iterations: int = 5) -> tuple[float, float]:
    """Iterative inverse of the GCJ-02 obfuscation (sub-metre residual after 5 rounds)."""
    if _out_of_china(lon, lat):
        return lon, lat
    wgs_lon, wgs_lat = lon, lat
    for _ in range(iterations):
        gcj_lon, gcj_lat = wgs84_to_gcj02(wgs_lon, wgs_lat)
        wgs_lon += lon - gcj_lon
        wgs_lat += lat - gcj_lat
    return wgs_lon, wgs_lat


# --------------------------------------------------------------------------- provider layer


@dataclass(frozen=True)
class GeocodeQuery:
    district: str
    community: str
    address: str

    @property
    def cache_key(self) -> str:
        return hashlib.sha256(
            f"{self.district}|{self.community}|{self.address}".encode()
        ).hexdigest()

    def candidates(self) -> tuple[str, ...]:
        """Ordered query strings: full address first, community name as fallback."""
        district = self.district if self.district.endswith("区") else f"{self.district}区"
        return (
            f"上海市{district}{self.address}",
            f"上海市{district}{self.community}",
        )

    def nominatim_candidates(self) -> tuple[str, ...]:
        """Prefer the named community because OSM rarely indexes Chinese lane addresses."""
        district = self.district if self.district.endswith("区") else f"{self.district}区"
        candidates = (
            f"{self.community},{district},上海市,中国",
            f"{self.address},{district},上海市,中国",
        )
        return tuple(candidate for candidate in candidates if candidate.split(",", 1)[0])


ProviderCall = Callable[[str], Mapping[str, Any]]


class ProviderRequestError(RuntimeError):
    """Transient provider transport/status failure eligible for bounded retry."""


def amap_provider(
    key: str, *, endpoint: str = AMAP_ENDPOINT, timeout_seconds: float = 10.0
) -> ProviderCall:
    import httpx

    def call(query: str) -> Mapping[str, Any]:
        try:
            response = httpx.get(
                endpoint,
                params={"key": key, "address": query, "city": "上海", "output": "JSON"},
                timeout=timeout_seconds,
            )
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise ProviderRequestError(f"AMap request failed: {exc}") from exc
        payload = response.json()
        if not isinstance(payload, Mapping):
            raise ValueError("AMap response is not a JSON object")
        return payload

    return call


def parse_amap_answer(payload: Mapping[str, Any]) -> dict[str, Any] | None:
    if str(payload.get("status")) != "1":
        return None
    geocodes = payload.get("geocodes") or []
    if not geocodes:
        return None
    best = geocodes[0]
    location = str(best.get("location") or "")
    if "," not in location:
        return None
    lon_text, lat_text = location.split(",", 1)
    return {
        "provider": "amap",
        "datum": "GCJ-02",
        "lon": float(lon_text),
        "lat": float(lat_text),
        "level": str(best.get("level") or ""),
        "district": str(best.get("district") or ""),
        "adcode": str(best.get("adcode") or ""),
        "formatted_address": str(best.get("formatted_address") or ""),
        "candidate_count": len(geocodes),
    }


def nominatim_provider(
    *,
    endpoint: str = NOMINATIM_ENDPOINT,
    user_agent: str = NOMINATIM_USER_AGENT,
    timeout_seconds: float = 20.0,
) -> ProviderCall:
    import httpx

    def call(query: str) -> Mapping[str, Any]:
        try:
            response = httpx.get(
                endpoint,
                params={
                    "q": query,
                    "format": "jsonv2",
                    "limit": 3,
                    "addressdetails": 1,
                },
                headers={"User-Agent": user_agent},
                timeout=timeout_seconds,
            )
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise ProviderRequestError(f"Nominatim request failed: {exc}") from exc
        payload = response.json()
        if not isinstance(payload, list):
            raise ValueError("Nominatim response is not a JSON array")
        return {"query": query, "results": payload}

    return call


def parse_nominatim_answer(payload: Mapping[str, Any]) -> dict[str, Any] | None:
    results = payload.get("results")
    if not isinstance(results, list):
        return None
    expected = str(payload.get("query") or "").split(",", 1)[0].strip()
    for result in results:
        if not isinstance(result, Mapping):
            continue
        address = result.get("address")
        if not isinstance(address, Mapping):
            address = {}
        if str(address.get("country_code") or "").lower() != "cn":
            continue
        state = str(address.get("state") or "")
        if state and state not in {"上海市", "上海"}:
            continue
        name = str(result.get("name") or "")
        display_name = str(result.get("display_name") or "")
        if expected and expected not in name and expected not in display_name:
            continue
        district = str(
            address.get("city")
            or address.get("county")
            or address.get("state_district")
            or address.get("city_district")
            or ""
        )
        try:
            lon = float(result["lon"])
            lat = float(result["lat"])
        except (KeyError, TypeError, ValueError):
            continue
        return {
            "provider": "nominatim",
            "datum": "WGS84",
            "lon": lon,
            "lat": lat,
            "level": str(result.get("addresstype") or result.get("type") or ""),
            "district": district,
            "adcode": "",
            "formatted_address": display_name,
            "candidate_count": len(results),
            "osm_type": str(result.get("osm_type") or ""),
            "osm_id": result.get("osm_id"),
            "license": str(result.get("licence") or ""),
        }
    return None


# --------------------------------------------------------------------------- cache handling


def load_cache(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"geocoder_version": GEOCODER_VERSION, "entries": {}}
    document = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(document, dict) or not isinstance(document.get("entries"), dict):
        raise ValueError(f"{path} is not a geocode cache")
    reparse_cached_answers(document)
    return document


def reparse_cached_answers(cache: dict[str, Any]) -> None:
    """Rebuild derived answers from immutable raw attempts after parser improvements."""
    parser = parse_nominatim_answer if cache.get("provider") == "nominatim" else parse_amap_answer
    entries = cache.get("entries")
    if not isinstance(entries, dict):
        return
    for entry in entries.values():
        if not isinstance(entry, dict):
            continue
        attempts = entry.get("attempts")
        if not isinstance(attempts, list):
            continue
        answer = None
        for attempt in attempts:
            if not isinstance(attempt, dict):
                continue
            raw = attempt.get("raw")
            parsed = parser(raw) if isinstance(raw, Mapping) else None
            attempt["parsed"] = parsed
            if parsed is not None:
                answer = {**parsed, "query": str(attempt.get("query") or "")}
                break
        entry["answer"] = answer


def save_cache(path: Path, cache: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(cache, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


def distinct_queries(items: Sequence[Mapping[str, Any]]) -> list[GeocodeQuery]:
    seen: dict[str, GeocodeQuery] = {}
    for item in items:
        query = GeocodeQuery(
            district=str(item.get("district") or "").strip(),
            community=str(item.get("community") or "").strip(),
            address=str(item.get("address") or "").strip(),
        )
        if not query.district or not (query.address or query.community):
            continue
        seen.setdefault(query.cache_key, query)
    return list(seen.values())


def fetch_missing(
    queries: list[GeocodeQuery],
    cache: dict[str, Any],
    *,
    provider: ProviderCall,
    parse_answer: Callable[[Mapping[str, Any]], dict[str, Any] | None],
    provider_name: str,
    qps: float,
    candidate_selector: Callable[[GeocodeQuery], tuple[str, ...]] | None = None,
    max_provider_attempts: int = 3,
    on_progress: Callable[[str], None] | None = None,
) -> dict[str, int]:
    if max_provider_attempts < 1:
        raise ValueError("max_provider_attempts must be at least 1")
    entries: dict[str, Any] = cache["entries"]
    counts = {"cached": 0, "resolved": 0, "unresolved": 0}
    delay = 1.0 / qps if qps > 0 else 0.0
    for query in queries:
        if query.cache_key in entries:
            counts["cached"] += 1
            continue
        attempts: list[dict[str, Any]] = []
        answer: dict[str, Any] | None = None
        candidates = candidate_selector(query) if candidate_selector else query.candidates()
        for candidate in candidates:
            for provider_attempt in range(1, max_provider_attempts + 1):
                try:
                    payload = provider(candidate)
                    break
                except ProviderRequestError:
                    if provider_attempt == max_provider_attempts:
                        raise
                    time.sleep(max(delay, 1.0) * (2 ** (provider_attempt - 1)))
            parsed = parse_answer(payload)
            attempts.append({"query": candidate, "raw": payload, "parsed": parsed})
            if delay:
                time.sleep(delay)
            if parsed is not None:
                answer = {**parsed, "query": candidate}
                break
        entries[query.cache_key] = {
            "district": query.district,
            "community": query.community,
            "address": query.address,
            "provider": provider_name,
            "retrieved_at": datetime.now(UTC).isoformat(),
            "answer": answer,
            "attempts": attempts,
        }
        counts["resolved" if answer is not None else "unresolved"] += 1
        if on_progress is not None:
            on_progress(f"{query.district} {query.community}: {'ok' if answer else 'miss'}")
    return counts


# --------------------------------------------------------------------------- apply stage


def acceptable(entry: Mapping[str, Any], *, accept_levels: tuple[str, ...]) -> tuple[bool, str]:
    answer = entry.get("answer")
    if not isinstance(answer, Mapping):
        return False, "unresolved"
    if answer.get("level") not in accept_levels:
        return False, f"level={answer.get('level')!r}"
    expected = str(entry.get("district") or "")
    returned = str(answer.get("district") or "")
    if expected and not returned.startswith(expected):
        return False, f"district_mismatch={returned!r}"
    return True, "ok"


def enrich_items(
    items: list[dict[str, Any]],
    cache: Mapping[str, Any],
    *,
    accept_levels: tuple[str, ...],
    precision: int = 6,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    entries: Mapping[str, Any] = cache["entries"]
    enriched: list[dict[str, Any]] = []
    rejected: list[dict[str, str]] = []
    resolved = 0
    for item in items:
        copy = dict(item)
        query = GeocodeQuery(
            district=str(item.get("district") or "").strip(),
            community=str(item.get("community") or "").strip(),
            address=str(item.get("address") or "").strip(),
        )
        entry = entries.get(query.cache_key)
        if entry is None:
            rejected.append({"listing_id": str(item.get("listing_id")), "reason": "not_fetched"})
            enriched.append(copy)
            continue
        ok, reason = acceptable(entry, accept_levels=accept_levels)
        if not ok:
            rejected.append({"listing_id": str(item.get("listing_id")), "reason": reason})
            enriched.append(copy)
            continue
        answer = entry["answer"]
        lon, lat = float(answer["lon"]), float(answer["lat"])
        if answer.get("datum") == "GCJ-02":
            wgs_lon, wgs_lat = gcj02_to_wgs84(lon, lat)
        else:
            wgs_lon, wgs_lat = lon, lat
        copy["longitude"] = round(wgs_lon, precision)
        copy["latitude"] = round(wgs_lat, precision)
        copy["geocode"] = {
            "geocoder_version": GEOCODER_VERSION,
            "provider": answer.get("provider"),
            "query": answer.get("query"),
            "level": answer.get("level"),
            "adcode": answer.get("adcode"),
            "formatted_address": answer.get("formatted_address"),
            "osm_type": answer.get("osm_type"),
            "osm_id": answer.get("osm_id"),
            "license": answer.get("license"),
            "source_datum": answer.get("datum"),
            "source_lon": lon,
            "source_lat": lat,
            "output_datum": "WGS84",
            "retrieved_at": entry.get("retrieved_at"),
            "cache_key": query.cache_key,
        }
        resolved += 1
        enriched.append(copy)
    summary = {
        "items": len(items),
        "geocoded": resolved,
        "rejected": len(rejected),
        "rejected_items": rejected,
        "accept_levels": list(accept_levels),
    }
    return enriched, summary


# --------------------------------------------------------------------------- CLI


def _load_feed(path: Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    envelope = json.loads(path.read_text(encoding="utf-8"))
    items = envelope.get("items") if isinstance(envelope, dict) else None
    if not isinstance(items, list):
        raise ValueError("canonical envelope items must be an array")
    return envelope, items


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = parser.add_subparsers(dest="command", required=True)

    fetch = sub.add_parser("fetch", help="resolve addresses through the provider into the cache")
    fetch.add_argument("--input", type=Path, required=True)
    fetch.add_argument("--cache", type=Path, required=True)
    fetch.add_argument("--provider", choices=("amap", "nominatim"), default="amap")
    fetch.add_argument("--key-env", default="SHR_GEOCODER_KEY")
    fetch.add_argument("--qps", type=float, default=None)
    fetch.add_argument(
        "--endpoint",
        default=None,
        help="provider endpoint override; useful for a self-hosted Nominatim instance",
    )
    fetch.add_argument("--limit", type=int, default=None, help="stop after N new queries")

    apply = sub.add_parser("apply", help="write the geocoded canonical feed from the cache")
    apply.add_argument("--input", type=Path, required=True)
    apply.add_argument("--cache", type=Path, required=True)
    apply.add_argument("--output", type=Path, required=True)
    apply.add_argument(
        "--accept-level",
        action="append",
        default=None,
        help=f"provider precision levels to accept (default: {', '.join(DEFAULT_ACCEPT_LEVELS)})",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.command == "fetch":
        qps = args.qps if args.qps is not None else (1.0 if args.provider == "nominatim" else 2.0)
        if args.provider == "nominatim" and (qps <= 0 or qps > 1):
            print(
                json.dumps({"status": "error", "error": "public Nominatim requires 0 < --qps <= 1"})
            )
            return 2
        if args.provider == "amap":
            key = os.environ.get(args.key_env, "")
            if not key:
                print(json.dumps({"status": "error", "error": f"{args.key_env} is not set"}))
                return 2
            provider = amap_provider(key, endpoint=args.endpoint or AMAP_ENDPOINT)
            parse_answer = parse_amap_answer
            candidate_selector = None
        else:
            provider = nominatim_provider(
                endpoint=args.endpoint or NOMINATIM_ENDPOINT,
                user_agent=os.environ.get("SHR_GEOCODER_USER_AGENT", NOMINATIM_USER_AGENT),
            )
            parse_answer = parse_nominatim_answer
            candidate_selector = GeocodeQuery.nominatim_candidates
        _, items = _load_feed(args.input)
        queries = distinct_queries(items)
        cache = load_cache(args.cache)
        cache["geocoder_version"] = GEOCODER_VERSION
        cached_provider = cache.get("provider")
        if cached_provider not in (None, args.provider):
            print(
                json.dumps(
                    {
                        "status": "error",
                        "error": f"cache provider is {cached_provider}, not {args.provider}",
                    }
                )
            )
            return 2
        cache["provider"] = args.provider
        if args.limit is not None:
            pending = [q for q in queries if q.cache_key not in cache["entries"]][: args.limit]
            queries = [q for q in queries if q.cache_key in cache["entries"]] + pending
        try:
            counts = fetch_missing(
                queries,
                cache,
                provider=provider,
                parse_answer=parse_answer,
                provider_name=args.provider,
                qps=qps,
                candidate_selector=candidate_selector,
                on_progress=lambda line: print(line, file=sys.stderr),
            )
        finally:
            save_cache(args.cache, cache)
        print(json.dumps({"status": "ok", "distinct_queries": len(queries), **counts}))
        return 0

    if args.output.resolve() == args.input.resolve():
        print(json.dumps({"status": "error", "error": "--output must differ from --input"}))
        return 2
    envelope, items = _load_feed(args.input)
    cache = load_cache(args.cache)
    if args.accept_level:
        accept_levels = tuple(args.accept_level)
    elif cache.get("provider") == "nominatim":
        accept_levels = NOMINATIM_ACCEPT_LEVELS
    else:
        accept_levels = DEFAULT_ACCEPT_LEVELS
    enriched, summary = enrich_items(items, cache, accept_levels=accept_levels)
    metadata = dict(envelope.get("metadata") or {})
    metadata["geocoding"] = {
        "geocoder_version": GEOCODER_VERSION,
        "input_file": args.input.name,
        "input_sha256": hashlib.sha256(args.input.read_bytes()).hexdigest(),
        "cache_file": args.cache.name,
        "cache_sha256": hashlib.sha256(args.cache.read_bytes()).hexdigest(),
        "applied_at": datetime.now(UTC).isoformat(),
        "geocoded": summary["geocoded"],
        "rejected": summary["rejected"],
        "accept_levels": summary["accept_levels"],
    }
    output = {**envelope, "metadata": metadata, "items": enriched}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(output, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps({"status": "ok", "output": str(args.output), **summary}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

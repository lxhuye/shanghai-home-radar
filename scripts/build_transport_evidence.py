#!/usr/bin/env python3
"""Build P5A transport evidence (existing metro stations) from OpenStreetMap.

Existing stations are the ``status: current`` baseline that transport_accessibility needs
before any planned line can add uplift. OpenStreetMap is used because it is public, licensed
(ODbL 1.0), and every element carries an id and a last-edit timestamp that can be re-checked.

Stages (both idempotent):

``fetch``  run one Overpass query for subway stations inside 上海市 and store the raw JSON
          answer from the requested historical snapshot verbatim (this file is the
          provenance artifact — commit it).
``build``  convert the stored answer into an evidence YAML consumable by
          ``scripts/import_future_evidence.py``. The raw snapshot must match ``--cutoff``.

Planned / under-construction lines are NOT produced here. They need a dated official notice
per project; use ``data/evidence/sample/future_projects.planned.template.yaml``.

Usage::

    python scripts/build_transport_evidence.py fetch \
        --raw data/evidence/sample/osm_subway_stations.raw.json \
        --cutoff 2026-09-02T09:55:56Z
    python scripts/build_transport_evidence.py build \
        --raw data/evidence/sample/osm_subway_stations.raw.json \
        --cutoff 2026-09-02T09:55:56Z \
        --output data/evidence/sample/transport_stations.osm.yaml
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

sys.path.insert(0, str(Path(__file__).parent.parent))

BUILDER_VERSION = "osm-transport-evidence-v1"
OVERPASS_ENDPOINT = "https://overpass-api.de/api/interpreter"
OVERPASS_MAX_ATTEMPTS = 5
OVERPASS_RETRY_STATUSES = frozenset({406, 408, 425, 429, 500, 502, 503, 504})
OVERPASS_USER_AGENT = "ShanghaiHomeRadar/0.1 (research calibration; contact: local-user)"
OVERPASS_QUERY_TEMPLATE = """
[out:json][timeout:180][date:"{cutoff}"];
area["name"="上海市"]["admin_level"="4"]->.sh;
(
  node["railway"="station"]["station"="subway"](area.sh);
  node["railway"="station"]["subway"="yes"](area.sh);
);
out meta;
""".strip()


def parse_cutoff(value: str) -> datetime:
    cutoff = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if cutoff.tzinfo is None:
        raise ValueError("--cutoff must include a timezone")
    return cutoff.astimezone(UTC)


def overpass_query(cutoff: datetime) -> str:
    timestamp = cutoff.astimezone(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")
    return OVERPASS_QUERY_TEMPLATE.format(cutoff=timestamp)


def fetch_overpass(
    query: str,
    *,
    snapshot_at: datetime,
    timeout_seconds: float = 240.0,
    max_attempts: int = OVERPASS_MAX_ATTEMPTS,
    backoff_seconds: float = 2.0,
) -> dict[str, Any]:
    import httpx

    if max_attempts < 1:
        raise ValueError("max_attempts must be at least 1")
    response: httpx.Response | None = None
    last_network_error: Exception | None = None
    for attempt in range(1, max_attempts + 1):
        try:
            response = httpx.post(
                OVERPASS_ENDPOINT,
                content=query.encode(),
                headers={
                    "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
                    "User-Agent": OVERPASS_USER_AGENT,
                },
                timeout=timeout_seconds,
            )
            last_network_error = None
        except httpx.HTTPError as exc:
            last_network_error = exc
            if attempt == max_attempts:
                break
        else:
            if response.status_code < 400:
                break
            if response.status_code not in OVERPASS_RETRY_STATUSES:
                raise ValueError(
                    f"Overpass returned HTTP {response.status_code}: {_response_excerpt(response)}"
                )
            if attempt == max_attempts:
                break
        time.sleep(backoff_seconds * (2 ** (attempt - 1)))

    if response is None:
        detail = f"network error {last_network_error}"
        raise ValueError(f"Overpass failed after {max_attempts} attempts; {detail}")
    if response.status_code >= 400:
        detail = f"HTTP {response.status_code}: {_response_excerpt(response)}"
        raise ValueError(f"Overpass failed after {max_attempts} attempts; {detail}")
    try:
        payload = response.json()
    except ValueError as exc:
        raise ValueError(f"Overpass returned invalid JSON: {_response_excerpt(response)}") from exc
    if not isinstance(payload, dict) or "elements" not in payload:
        raise ValueError("Overpass answer has no elements")
    return {
        "builder_version": BUILDER_VERSION,
        "endpoint": OVERPASS_ENDPOINT,
        "query": query,
        "query_sha256": hashlib.sha256(query.encode()).hexdigest(),
        "snapshot_at": snapshot_at.astimezone(UTC).isoformat(),
        "retrieved_at": datetime.now(UTC).isoformat(),
        "osm3s": payload.get("osm3s"),
        "elements": payload["elements"],
    }


def _response_excerpt(response: Any, limit: int = 500) -> str:
    text = " ".join(str(response.text).split())
    return text[:limit] or "empty response body"


def _parse_osm_timestamp(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)
    except ValueError:
        return None


def build_projects(
    raw: Mapping[str, Any], *, cutoff: datetime, confidence: str = "0.8"
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    snapshot_at = _parse_osm_timestamp(raw.get("snapshot_at"))
    if snapshot_at is None:
        raise ValueError("raw Overpass evidence is missing snapshot_at")
    if snapshot_at != cutoff.astimezone(UTC):
        raise ValueError(
            f"raw Overpass snapshot {snapshot_at.isoformat()} does not match cutoff "
            f"{cutoff.astimezone(UTC).isoformat()}"
        )
    retrieved_at = str(raw.get("retrieved_at") or datetime.now(UTC).isoformat())
    raw_sha = hashlib.sha256(
        json.dumps(raw.get("elements", []), ensure_ascii=False, sort_keys=True).encode()
    ).hexdigest()
    projects: list[dict[str, Any]] = []
    dropped: list[dict[str, str]] = []
    for element in raw.get("elements", []):
        if element.get("type") != "node":
            continue
        tags = element.get("tags") or {}
        name = str(tags.get("name") or "").strip()
        lon, lat = element.get("lon"), element.get("lat")
        if not name or lon is None or lat is None:
            dropped.append({"id": str(element.get("id")), "reason": "missing name or position"})
            continue
        edited = _parse_osm_timestamp(element.get("timestamp"))
        if edited is None:
            dropped.append({"id": str(element.get("id")), "reason": "missing timestamp"})
            continue
        if edited > cutoff:
            dropped.append(
                {"id": str(element.get("id")), "reason": f"edited after cutoff ({edited.date()})"}
            )
            continue
        lines = str(tags.get("line") or tags.get("route_ref") or "").strip()
        display = f"{name}站" if not name.endswith("站") else name
        if lines:
            display = f"{display}（{lines}）"
        node_id = int(element["id"])
        projects.append(
            {
                "name": display[:200],
                "project_type": "transport",
                "status": "current",
                "coordinates": {"lon": float(lon), "lat": float(lat)},
                "effective_from": edited.isoformat(),
                "source_date": edited.isoformat(),
                "source": "openstreetmap",
                "source_record_id": f"node/{node_id}",
                "confidence": confidence,
                "provenance": {
                    "url": f"https://www.openstreetmap.org/node/{node_id}",
                    "publisher": "OpenStreetMap contributors",
                    "published_at": edited.isoformat(),
                    "retrieved_at": retrieved_at,
                    "license": "ODbL 1.0",
                    "osm_version": element.get("version"),
                    "osm_tags": {k: v for k, v in tags.items() if k in _KEPT_TAGS},
                    "overpass_query_sha256": raw.get("query_sha256"),
                    "raw_elements_sha256": raw_sha,
                    "builder_version": BUILDER_VERSION,
                    "datum": "WGS84",
                },
            }
        )
    summary = {
        "elements": len(raw.get("elements", [])),
        "projects": len(projects),
        "dropped": dropped,
        "cutoff": cutoff.isoformat(),
    }
    return projects, summary


_KEPT_TAGS = frozenset({"name", "name:en", "line", "route_ref", "network", "operator", "station"})


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = parser.add_subparsers(dest="command", required=True)
    fetch = sub.add_parser("fetch")
    fetch.add_argument("--raw", type=Path, required=True)
    fetch.add_argument("--cutoff", required=True)
    build = sub.add_parser("build")
    build.add_argument("--raw", type=Path, required=True)
    build.add_argument("--cutoff", required=True)
    build.add_argument("--output", type=Path, required=True)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.command == "fetch":
        try:
            cutoff = parse_cutoff(args.cutoff)
        except ValueError as exc:
            print(json.dumps({"status": "error", "error": str(exc)}))
            return 2
        if args.raw.exists():
            print(json.dumps({"status": "exists", "raw": str(args.raw)}))
            return 0
        try:
            raw = fetch_overpass(overpass_query(cutoff), snapshot_at=cutoff)
        except ValueError as exc:
            print(json.dumps({"status": "error", "error": str(exc)}, ensure_ascii=False))
            return 2
        args.raw.parent.mkdir(parents=True, exist_ok=True)
        args.raw.write_text(json.dumps(raw, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        print(json.dumps({"status": "fetched", "elements": len(raw["elements"])}))
        return 0

    try:
        cutoff = parse_cutoff(args.cutoff)
    except ValueError as exc:
        print(json.dumps({"status": "error", "error": str(exc)}))
        return 2
    raw = json.loads(args.raw.read_text(encoding="utf-8"))
    try:
        projects, summary = build_projects(raw, cutoff=cutoff)
    except ValueError as exc:
        print(json.dumps({"status": "error", "error": str(exc)}))
        return 2
    document = {
        "data_mode": "sample",
        "cutoff": cutoff.isoformat(),
        "future_projects": projects,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    header = (
        "# Generated by scripts/build_transport_evidence.py — existing subway stations from\n"
        f"# OpenStreetMap (ODbL). Raw answer: {args.raw.name}. Do not edit by hand.\n"
    )
    args.output.write_text(
        header + yaml.safe_dump(document, allow_unicode=True, sort_keys=False), encoding="utf-8"
    )
    print(json.dumps({"status": "ok", "output": str(args.output), **summary}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

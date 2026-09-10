#!/usr/bin/env python3
"""Mock partner API for exercising ``AuthorizedAPIAdapter`` over real HTTP.

It serves a canonical listing feed (default: calibration Set A) through the same contract the
adapter expects — ``page`` / ``page_size`` pagination, ``total`` / ``total_pages`` metadata,
``city`` / ``district`` / ``submarket`` / ``status`` filters — behind Bearer authentication, and
can inject faults per page so the retry, rate-limit, and authentication paths are covered
without a partner credential.

Fault syntax (repeatable ``--fault``): ``<page>:<kind>:<times>`` where kind is an HTTP status
(``429``, ``500``, ``503`` …), ``timeout`` (sleep past the client timeout), ``empty`` (return
an empty page), or ``short`` (drop half the page). ``times`` is how many requests for that page
fail before it recovers.

Usage::

    python scripts/mock_authorized_api.py --port 9000 --token mock_token \
        --feed data/raw/calibration_set_a/market_pool.canonical.json \
        --fault 2:429:1 --fault 3:500:2

Then point the collector at it::

    SHR_COLLECTOR_SOURCE=authorized_api
    SHR_COLLECTOR_ENDPOINT=http://127.0.0.1:9000/v1/listings
    SHR_COLLECTOR_AUTH_MODE=BEARER_TOKEN
    SHR_COLLECTOR_BEARER_TOKEN=mock_token
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import JSONResponse

sys.path.insert(0, str(Path(__file__).parent.parent))

DEFAULT_FEED = Path("data/raw/calibration_set_a/market_pool.canonical.json")


@dataclass
class Fault:
    page: int
    kind: str
    remaining: int

    @classmethod
    def parse(cls, text: str) -> Fault:
        try:
            page, kind, times = text.split(":")
            return cls(int(page), kind.strip().lower(), int(times))
        except ValueError as exc:
            raise argparse.ArgumentTypeError(f"bad fault spec {text!r}") from exc


@dataclass
class MockState:
    items: list[dict[str, Any]]
    token: str | None
    faults: dict[int, Fault] = field(default_factory=dict)
    timeout_sleep_seconds: float = 5.0
    request_log: list[dict[str, Any]] = field(default_factory=list)

    def take_fault(self, page: int) -> Fault | None:
        fault = self.faults.get(page)
        if fault is None or fault.remaining <= 0:
            return None
        fault.remaining -= 1
        return fault


def _matches(
    item: Mapping[str, Any],
    *,
    city: str | None,
    district: str | None,
    submarket: str | None,
    query: str | None,
) -> bool:
    item_city = str(item.get("city") or "shanghai")
    if city and item_city.casefold() != city.casefold():
        return False
    if district and str(item.get("district", "")) != district:
        return False
    if submarket and str(item.get("submarket", "")) != submarket:
        return False
    if query:
        haystack = " ".join(
            str(item.get(field) or "") for field in ("title", "community", "address", "submarket")
        )
        if query.casefold() not in haystack.casefold():
            return False
    return True


def create_app(state: MockState) -> FastAPI:
    app = FastAPI(title="HouseRadar mock authorized API", version="1.0")

    @app.middleware("http")
    async def log_requests(request: Request, call_next: Any) -> Any:
        response = await call_next(request)
        if request.url.path == "/v1/listings":
            params = request.query_params
            state.request_log.append(
                {
                    "page": int(params.get("page", "1")),
                    "page_size": int(params.get("page_size", "100")),
                    "district": params.get("district"),
                    "submarket": params.get("submarket"),
                    "city": params.get("city"),
                    "query": params.get("query"),
                    "status_code": response.status_code,
                }
            )
        return response

    async def require_token(request: Request) -> None:
        if state.token is None:
            return
        header = request.headers.get("authorization", "")
        if header != f"Bearer {state.token}":
            raise HTTPException(status_code=401, detail="invalid bearer token")

    @app.get("/health")
    async def health() -> dict[str, Any]:
        return {"status": "ok", "items": len(state.items)}

    @app.get("/v1/listings", dependencies=[Depends(require_token)])
    async def listings(
        page: int = Query(1, ge=1),
        page_size: int = Query(100, ge=1, le=500),
        city: str | None = None,
        district: str | None = None,
        submarket: str | None = None,
        query: str | None = None,
        status: str | None = None,
    ) -> JSONResponse:
        fault = state.take_fault(page)
        if fault is not None:
            if fault.kind == "timeout":
                await asyncio.sleep(state.timeout_sleep_seconds)
            elif fault.kind.isdigit():
                return JSONResponse(
                    status_code=int(fault.kind), content={"error": f"injected {fault.kind}"}
                )
        selected = [
            item
            for item in state.items
            if _matches(
                item,
                city=city,
                district=district,
                submarket=submarket,
                query=query,
            )
            and (status is None or str(item.get("status", "active")) == status)
        ]
        total = len(selected)
        total_pages = max(1, -(-total // page_size))
        start = (page - 1) * page_size
        page_items = selected[start : start + page_size]
        if fault is not None and fault.kind == "empty":
            page_items = []
        elif fault is not None and fault.kind == "short":
            page_items = page_items[: max(1, len(page_items) // 2)]
        return JSONResponse(
            {
                "items": page_items,
                "page": page,
                "page_size": page_size,
                "total": total,
                "total_pages": total_pages,
                "city": city or "shanghai",
            }
        )

    return app


def load_feed(path: Path) -> list[dict[str, Any]]:
    envelope = json.loads(path.read_text(encoding="utf-8"))
    items = envelope.get("items") if isinstance(envelope, dict) else envelope
    if not isinstance(items, list):
        raise ValueError("feed must contain an items array")
    return [dict(item) for item in items]


def build_state(
    items: Sequence[Mapping[str, Any]], *, token: str | None, faults: Sequence[Fault] = ()
) -> MockState:
    return MockState(
        items=[dict(item) for item in items],
        token=token,
        faults={fault.page: Fault(fault.page, fault.kind, fault.remaining) for fault in faults},
    )


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=9000)
    parser.add_argument("--feed", type=Path, default=DEFAULT_FEED)
    parser.add_argument("--token", default="mock_token", help="use '' to disable auth")
    parser.add_argument("--fault", type=Fault.parse, action="append", default=[])
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    import uvicorn

    args = parse_args(argv)
    state = build_state(load_feed(args.feed), token=args.token or None, faults=args.fault)
    print(
        json.dumps(
            {
                "items": len(state.items),
                "auth": "bearer" if state.token else "none",
                "faults": [f"{f.page}:{f.kind}:{f.remaining}" for f in state.faults.values()],
                "endpoint": f"http://{args.host}:{args.port}/v1/listings",
            }
        )
    )
    uvicorn.run(create_app(state), host=args.host, port=args.port, log_level="info")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Human-gated, persistent Selenium session for the private monitoring pilot.

No CAPTCHA solver, cookie export, stealth settings, source retry loop, or model run.
changedetection.io reads /snapshot; only READY sessions may navigate automatically.
"""

from __future__ import annotations

import asyncio
import json
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import urlsplit

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import HTMLResponse, JSONResponse

from scripts.antibot_detection import DETECTION_SCRIPT, summarize_detection
from scripts.prepare_public_monitor_pilot import prepare

TARGET_URL = "https://shanghai.anjuke.com/sale/xuhui/m13473/"
PAGE_EVIDENCE_SCRIPT = (
    "const antibot = "
    + DETECTION_SCRIPT
    + ";\n"
    + """
return {
  antibot,
  url: location.href,
  title: document.title,
  loaded_at: new Date(performance.timeOrigin).toISOString(),
  text: (document.body?.innerText || '').slice(0, 2000),
  cards: Array.from(document.querySelectorAll('a.property-ex'))
    .filter(a => a.innerText.includes('元/㎡')).slice(0, 100)
    .map(a => ({raw_text: a.innerText.replace(/\\s+/g, ' ').trim(),
                source_url: a.href.split('?')[0]}))
};
"""
)


def atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.chmod(0o600)
    temporary.replace(path)


class BrowserUnavailable(Exception):
    pass


class Browser(Protocol):
    async def initialize(self) -> None: ...
    async def navigate(self, url: str) -> None: ...
    async def evidence(self) -> dict[str, Any]: ...


class Analyzer(Protocol):
    def run(self, feed: dict[str, Any]) -> dict[str, Any]: ...
    def read(self, feed: dict[str, Any]) -> dict[str, Any] | None: ...


class SeleniumSession:
    """Small W3C WebDriver client; reattaches to the SAME live session after restart."""

    def __init__(
        self,
        endpoint: str,
        state_dir: Path,
        *,
        profile_dir: str = "/home/seluser/homeradar-profile",
    ) -> None:
        self.endpoint = endpoint.rstrip("/")
        self.profile_dir = profile_dir
        self.path = state_dir / "webdriver.json"
        self.session_id: str | None = None
        if self.path.exists():
            self.session_id = json.loads(self.path.read_text())["id"]

    async def command(self, method: str, path: str, body: Any = None) -> Any:
        try:
            async with httpx.AsyncClient(timeout=40, trust_env=False) as client:
                response = await client.request(method, self.endpoint + path, json=body)
            value = response.json().get("value")
            if response.is_error or (isinstance(value, dict) and value.get("error")):
                raise BrowserUnavailable("browser command failed; manual reattachment required")
            return value
        except (httpx.HTTPError, ValueError) as exc:
            raise BrowserUnavailable("browser unavailable; no automatic retry") from exc

    def session_path(self, suffix: str) -> str:
        if not self.session_id:
            raise BrowserUnavailable("open the dedicated browser first")
        return f"/session/{self.session_id}/{suffix}"

    async def initialize(self) -> None:
        if self.session_id:
            try:
                await self.command("GET", self.session_path("url"))
                return
            except BrowserUnavailable:
                # Initialization is an explicit human action, never a scheduled recovery.
                self.session_id = None
        value = await self.command(
            "POST",
            "/session",
            {
                "capabilities": {
                    "alwaysMatch": {
                        "browserName": "chrome",
                        "goog:chromeOptions": {
                            "args": [
                                f"--user-data-dir={self.profile_dir}",
                                "--start-maximized",
                            ]
                        },
                    }
                }
            },
        )
        self.session_id = str(value["sessionId"])
        atomic_json(self.path, {"id": self.session_id})
        await self.command(
            "POST",
            self.session_path("timeouts"),
            {
                "pageLoad": 30000,
                "script": 10000,
                "implicit": 0,
            },
        )

    async def navigate(self, url: str) -> None:
        await self.command("POST", self.session_path("url"), {"url": url})

    async def evidence(self) -> dict[str, Any]:
        value = await self.command(
            "POST",
            self.session_path("execute/sync"),
            {
                "script": PAGE_EVIDENCE_SCRIPT,
                "args": [],
            },
        )
        if not isinstance(value, dict):
            raise BrowserUnavailable("browser returned no property evidence")
        return value


def pause_reason(page: dict[str, Any], target_url: str, min_cards: int) -> str | None:
    detection = summarize_detection(page)
    if detection["requires_human"]:
        names = "、".join(row["name"] for row in detection["detections"] if row["requires_human"])
        return f"检测到 {names} 验证界面，请在专用浏览器完成后恢复。"
    actual, expected = urlsplit(str(page.get("url", ""))), urlsplit(target_url)
    visible = str(page.get("title", "")) + " " + str(page.get("text", ""))
    if any(
        word in (actual.path + actual.query).lower()
        for word in ("captcha", "antispam", "antibot", "login", "verify")
    ):
        return "网站要求人工验证或登录，请在专用浏览器中完成。"
    if any(
        word in visible.lower()
        for word in (
            "安全验证",
            "请完成验证",
            "滑动验证",
            "请输入验证码",
            "访问过于频繁",
            "verify you are human",
            "verify that you are human",
            "access denied",
        )
    ):
        return "页面仍显示验证提示，请人工处理。"
    if (actual.scheme, actual.netloc, actual.path.rstrip("/")) != (
        expected.scheme,
        expected.netloc,
        expected.path.rstrip("/"),
    ):
        return "浏览器不在指定筛选页；尚未允许采集。"
    if len(page.get("cards", [])) < min_cards:
        return "没有读到足够的房源卡片；不会把空页面当作下架。"
    return None


class Handoff:
    def __init__(
        self,
        browser: Browser,
        state_dir: Path,
        export_dir: Path,
        *,
        target_url: str = TARGET_URL,
        interval_seconds: int = 21600,
        min_cards: int = 10,
        analyzer: Analyzer | None = None,
    ) -> None:
        if interval_seconds < 60:
            raise ValueError("interval must be at least 60 seconds")
        self.browser, self.state_dir, self.export_dir = browser, state_dir, export_dir
        self.analyzer = analyzer
        self.target_url, self.interval_seconds, self.min_cards = (
            target_url,
            interval_seconds,
            min_cards,
        )
        self.lock = asyncio.Lock()
        self.manual_requested = True
        self.state: dict[str, Any] = {
            "status": "PAUSED",
            "reason": "尚未允许采集。请先打开专用浏览器。",
            "last_success_at": None,
            "last_attempt_at": None,
            "records": 0,
            "browser_initialized": False,
            "last_human_open_at": None,
            "antibot": {"version": "1", "detections": [], "requires_human": False},
        }
        self.latest: dict[str, Any] | None = None
        saved = self.state_dir / "status.json"
        if saved.exists():
            self.state.update(json.loads(saved.read_text()))
        self.state.update(status="PAUSED", reason="服务启动后默认暂停，请人工确认再恢复。")
        feed = self.export_dir / "latest.canonical.json"
        if feed.exists():
            self.latest = json.loads(feed.read_text())
        self.save()

    def save(self) -> None:
        atomic_json(self.state_dir / "status.json", self.state)

    async def update_analysis(self) -> None:
        if self.latest is None or self.analyzer is None:
            return
        self.state["analysis_status"] = "running"
        try:
            report = await asyncio.to_thread(self.analyzer.run, self.latest)
            self.state.update(
                analysis_status="complete",
                analysis_generated_at=report["generated_at"],
                analysis_summary=report["summary"],
            )
        except Exception:
            # Preserve collection evidence when analysis fails. The scheduler retries locally.
            self.state["analysis_status"] = "failed"
        self.save()

    def pause(
        self, reason: str = "已暂停；浏览器交给人工操作。", *, needs_human: bool = False
    ) -> None:
        self.manual_requested = True
        self.state.update(status="NEEDS_HUMAN" if needs_human else "PAUSED", reason=reason)
        self.save()

    async def open_browser(self, *, navigate: bool) -> dict[str, Any]:
        self.pause()
        async with self.lock:
            if navigate and self.state.get("last_human_open_at"):
                elapsed = datetime.now(UTC) - datetime.fromisoformat(
                    self.state["last_human_open_at"]
                )
                if elapsed.total_seconds() < 60:
                    raise HTTPException(429, "请在已打开的浏览器中操作，暂不重复请求来源页。")
            try:
                await self.browser.initialize()
                self.state["browser_initialized"] = True
                if navigate:
                    self.state["last_human_open_at"] = datetime.now(UTC).isoformat()
                    self.save()
                    await self.browser.navigate(self.target_url)
                    page = await self.browser.evidence()
                    self.state["antibot"] = summarize_detection(page)
                    reason = pause_reason(page, self.target_url, self.min_cards)
                    self.pause(
                        reason or "页面可读；请确认后点击“验证完成并恢复”。", needs_human=True
                    )
                else:
                    self.pause("专用浏览器已准备好，可人工操作；尚未访问房源网站。")
            except BrowserUnavailable:
                self.pause("浏览器连接失败，请检查服务后人工重试。", needs_human=True)
            return dict(self.state)

    async def accept_page(self) -> None:
        page = await self.browser.evidence()
        self.state["antibot"] = summarize_detection(page)
        reason = pause_reason(page, self.target_url, self.min_cards)
        if reason:
            self.pause(reason, needs_human=True)
            raise HTTPException(409, reason)
        checked_at = datetime.now(UTC)
        try:
            loaded_at = datetime.fromisoformat(str(page["loaded_at"]))
            if loaded_at.utcoffset() is None or not (
                -30 <= (checked_at - loaded_at).total_seconds() <= 3600
            ):
                raise ValueError("stale page")
        except (KeyError, ValueError, TypeError):
            self.pause(
                "页面加载已超过一小时或时间不明，请在专用浏览器刷新后再恢复。", needs_human=True
            )
            raise HTTPException(409, self.state["reason"]) from None
        stamp = loaded_at.isoformat()
        records = [
            {
                **card,
                "observed_at": stamp,
                "district_expected": "徐汇",
                "search_page": self.target_url,
            }
            for card in page["cards"]
        ]
        try:
            previous = self.latest or {"items": []}
            # Rechecking the same DOM must not invent a new observation or erase a price change.
            repeated = bool(self.latest and stamp == self.state.get("last_success_at"))
            feed, changes = prepare(records, {"items": []} if repeated else previous)
        except (ValueError, KeyError, TypeError):
            self.pause("房源字段解析未通过，已暂停且保留上次成功数据。", needs_human=True)
            raise HTTPException(409, self.state["reason"]) from None
        feed["metadata"].update(
            collection_method="persistent_browser_human_gated", unattended_monitoring_enabled=True
        )
        for item in feed["items"]:
            item["provenance"]["method"] = "persistent_browser_human_gated"
        if self.manual_requested:
            raise HTTPException(409, "人工接管中，本次结果没有发布。")
        if repeated and self.latest:
            if feed["items"] != self.latest["items"]:
                self.pause("页面内容变化但观测时间未更新，请刷新后恢复。", needs_human=True)
                raise HTTPException(409, self.state["reason"])
            self.state.update(status="READY", reason="当前页面已记录；继续低频检查。")
            self.save()
            return
        feed["metadata"].update(changes=changes, antibot=self.state["antibot"])
        stamp_path = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
        atomic_json(self.export_dir / "observations" / f"{stamp_path}.raw.json", records)
        atomic_json(self.export_dir / "observations" / f"{stamp_path}.canonical.json", feed)
        atomic_json(self.export_dir / "latest.canonical.json", feed)
        self.latest = feed
        self.state.update(
            status="READY",
            reason="已确认可读；低频检查可用。",
            last_success_at=stamp,
            records=len(records),
        )
        self.save()

    async def resume(self) -> dict[str, Any]:
        async with self.lock:
            self.manual_requested = False
            try:
                # Inspect only: never navigate away while the human is solving a challenge.
                await self.accept_page()
            except BrowserUnavailable:
                self.pause("原浏览器会话不可用，请人工重新连接。", needs_human=True)
                raise HTTPException(409, self.state["reason"]) from None
            self.state["last_attempt_at"] = datetime.now(UTC).isoformat()
            await self.update_analysis()
            self.save()
            return dict(self.state)

    async def snapshot(self) -> list[dict[str, Any]]:
        async with self.lock:
            if self.manual_requested or self.state["status"] != "READY":
                raise HTTPException(503, self.state["reason"])
            last = self.state.get("last_attempt_at")
            due = not last or datetime.now(UTC) >= (
                datetime.fromisoformat(last) + timedelta(seconds=self.interval_seconds)
            )
            if due:
                self.state["last_attempt_at"] = datetime.now(UTC).isoformat()
                self.save()
                try:
                    await self.browser.navigate(self.target_url)
                    await self.accept_page()
                    await self.update_analysis()
                except BrowserUnavailable:
                    self.pause("浏览器访问失败，已停止自动重试。", needs_human=True)
                    raise HTTPException(503, self.state["reason"]) from None
                except HTTPException as exc:
                    raise HTTPException(503, self.state["reason"]) from exc
            if self.manual_requested or self.state["status"] != "READY" or self.latest is None:
                raise HTTPException(503, self.state["reason"])
            # Stable representation prevents timestamps, ordering and tracking IDs from alerting.
            fields = (
                "listing_id",
                "url",
                "district",
                "community",
                "price_wan",
                "area_sqm",
                "bedrooms",
                "floor",
                "year_built",
                "orientation",
                "elevator",
            )
            return [
                {key: item.get(key) for key in fields}
                for item in sorted(self.latest["items"], key=lambda value: value["listing_id"])
            ]


def create_app(handoff: Handoff) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        async def monitor() -> None:
            # Analyze already persisted evidence at startup without visiting the source.
            async with handoff.lock:
                await handoff.update_analysis()
            while True:
                await asyncio.sleep(60)
                if handoff.state["status"] == "READY" and not handoff.manual_requested:
                    try:
                        await handoff.snapshot()
                    except HTTPException:
                        pass  # The controller already preserved the last good observation.
                    except Exception:
                        handoff.pause(
                            "监控发生内部错误，已暂停，请检查本机服务。", needs_human=True
                        )
                if handoff.latest and handoff.state.get("analysis_status") == "failed":
                    async with handoff.lock:
                        await handoff.update_analysis()

        task = asyncio.create_task(monitor())
        try:
            yield
        finally:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task

    app = FastAPI(title="HouseRadar 房源监控", docs_url=None, redoc_url=None, lifespan=lifespan)
    app.add_middleware(
        TrustedHostMiddleware, allowed_hosts=["localhost", "127.0.0.1", "handoff", "testserver"]
    )

    @app.get("/", response_class=HTMLResponse)
    async def index() -> str:
        return Path(__file__).with_name("browser_handoff.html").read_text(encoding="utf-8")

    @app.get("/status")
    async def status() -> dict[str, Any]:
        last_attempt = handoff.state.get("last_attempt_at")
        return {
            **handoff.state,
            "source_url": handoff.target_url,
            "interval_seconds": handoff.interval_seconds,
            "data_mode": "sample",
            "usage": "private_research_only",
            "production_database_imported": False,
            "browser_mode": getattr(handoff.browser, "mode", "selenium"),
            "next_check_at": (
                datetime.fromisoformat(last_attempt) + timedelta(seconds=handoff.interval_seconds)
            ).isoformat()
            if last_attempt and handoff.state["status"] == "READY"
            else None,
        }

    @app.post("/action/{action}")
    async def action(action: str, request: Request) -> dict[str, Any]:
        origin = request.headers.get("origin")
        if request.headers.get("x-handoff-action") != "1" or (
            origin and origin.rstrip("/") != str(request.base_url).rstrip("/")
        ):
            raise HTTPException(403, "只接受本机接管页面的操作。")
        if action == "pause":
            handoff.pause()
            return dict(handoff.state)
        if action in {"initialize", "open"}:
            return await handoff.open_browser(navigate=action == "open")
        if action == "resume":
            return await handoff.resume()
        raise HTTPException(404)

    @app.get("/snapshot")
    async def snapshot() -> JSONResponse:
        return JSONResponse(await handoff.snapshot(), headers={"Cache-Control": "no-store"})

    @app.get("/observations")
    async def observations() -> JSONResponse:
        # Historical data remains inspectable during a challenge, explicitly marked unavailable.
        stamp = handoff.state.get("last_success_at")
        age = (datetime.now(UTC) - datetime.fromisoformat(stamp)) if stamp else None
        return JSONResponse(
            {
                "status": handoff.state["status"],
                "observed_at": stamp,
                "stale": age is None or age > timedelta(hours=36),
                "current": handoff.state["status"] == "READY"
                and age is not None
                and timedelta(0) <= age <= timedelta(hours=36),
                "data_mode": "sample",
                "items": handoff.latest["items"] if handoff.latest else [],
                "changes": handoff.latest.get("metadata", {}).get("changes", [])
                if handoff.latest
                else [],
            },
            headers={"Cache-Control": "no-store"},
        )

    @app.get("/analysis")
    async def analysis() -> JSONResponse:
        if handoff.latest is None or handoff.analyzer is None:
            raise HTTPException(404, "尚无可分析的房源观测。")
        try:
            report = await asyncio.to_thread(handoff.analyzer.read, handoff.latest)
        except (OSError, ValueError):
            raise HTTPException(503, "分析结果暂不可读。") from None
        if report is None:
            raise HTTPException(503, "本次房源正在分析，请稍后查看。")
        observed = datetime.fromisoformat(report["observed_at"])
        age = datetime.now(UTC) - observed
        return JSONResponse(
            {
                **report,
                "current": handoff.state["status"] == "READY"
                and timedelta(0) <= age <= timedelta(hours=36),
                "stale": age > timedelta(hours=36),
            },
            headers={"Cache-Control": "no-store"},
        )

    @app.get("/health")
    async def health() -> JSONResponse:
        if handoff.state["status"] != "READY":
            raise HTTPException(
                503, {"status": "needs_attention", "reason": handoff.state["reason"]}
            )
        response = await analysis()
        report = json.loads(bytes(response.body))
        if not report["current"] or handoff.state.get("analysis_status") != "complete":
            raise HTTPException(503, {"status": "unhealthy", "reason": "观测过期或分析未完成。"})
        return JSONResponse(
            {
                "status": "healthy",
                "usage": "private_research_only",
                "observed_at": report["observed_at"],
                "summary": report["summary"],
                "recommendation_status": report["recommendation_status"],
            },
            headers={"Cache-Control": "no-store"},
        )

    @app.get("/feed")
    async def feed() -> JSONResponse:
        # No navigation from the export endpoint and no successful empty/stale/error feed.
        if handoff.state["status"] != "READY" or handoff.latest is None:
            raise HTTPException(503, handoff.state["reason"])
        observed = datetime.fromisoformat(str(handoff.state["last_success_at"]))
        if datetime.now(UTC) - observed > timedelta(hours=36):
            raise HTTPException(503, "上次成功观测已过期。")
        return JSONResponse(handoff.latest, headers={"Cache-Control": "no-store"})

    from scripts.recommendation_routes import register_recommendation_routes

    register_recommendation_routes(app, handoff)
    return app


def main() -> None:
    import uvicorn

    from scripts.research_pipeline import ResearchPipeline

    state_dir = Path(os.environ.get("SHR_HANDOFF_STATE_DIR", "/state"))
    browser = SeleniumSession(os.environ.get("SHR_WEBDRIVER_URL", "http://browser:4444"), state_dir)
    export_dir = Path(os.environ.get("SHR_HANDOFF_EXPORT_DIR", "/exports"))
    handoff = Handoff(browser, state_dir, export_dir, analyzer=ResearchPipeline(export_dir))
    uvicorn.run(create_app(handoff), host="0.0.0.0", port=8000, access_log=False)


if __name__ == "__main__":
    main()

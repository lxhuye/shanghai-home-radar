from __future__ import annotations

import asyncio
import copy
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi import HTTPException

from scripts.browser_handoff import (
    TARGET_URL,
    BrowserUnavailable,
    Handoff,
    SeleniumSession,
    create_app,
    pause_reason,
)


def listing_page() -> dict[str, Any]:
    return {
        "loaded_at": datetime.now(UTC).isoformat(),
        "url": TARGET_URL,
        "title": "测试房源",
        "text": "徐汇二手房",
        "cards": [
            {
                "raw_text": "测试房源 2 室 1 厅 1 卫 60㎡ 南 中层(共6层) "
                "1901年建造 测试小区 徐汇长桥测试路 满五年 280 万 46667元/㎡",
                "source_url": "https://shanghai.anjuke.com/prop/view/S123",
            }
        ],
    }


class FakeBrowser:
    def __init__(self) -> None:
        self.page = listing_page()
        self.navigations: list[str] = []
        self.initializations = 0
        self.fail = False

    async def initialize(self) -> None:
        self.initializations += 1

    async def navigate(self, url: str) -> None:
        self.navigations.append(url)
        if self.fail:
            raise BrowserUnavailable()

    async def evidence(self) -> dict[str, Any]:
        if self.fail:
            raise BrowserUnavailable()
        return copy.deepcopy(self.page)


def controller(tmp_path: Path, browser: FakeBrowser) -> Handoff:
    return Handoff(browser, tmp_path / "state", tmp_path / "exports", min_cards=1)


@pytest.mark.asyncio
async def test_start_paused_and_initialization_never_visits_source(tmp_path: Path) -> None:
    browser = FakeBrowser()
    handoff = controller(tmp_path, browser)
    with pytest.raises(HTTPException) as error:
        await handoff.snapshot()
    assert error.value.status_code == 503
    await handoff.open_browser(navigate=False)
    assert browser.initializations == 1
    assert browser.navigations == []
    assert handoff.state["status"] == "PAUSED"


@pytest.mark.asyncio
async def test_open_does_not_resume_even_when_cards_are_visible(tmp_path: Path) -> None:
    browser = FakeBrowser()
    handoff = controller(tmp_path, browser)
    await handoff.open_browser(navigate=True)
    assert browser.navigations == [TARGET_URL]
    assert handoff.state["status"] == "NEEDS_HUMAN"
    assert handoff.latest is None


@pytest.mark.asyncio
async def test_resume_inspects_same_page_and_preserves_partial_research_contract(
    tmp_path: Path,
) -> None:
    browser = FakeBrowser()
    handoff = controller(tmp_path, browser)
    await handoff.resume()
    first = await handoff.snapshot()
    second = await handoff.snapshot()
    assert first == second
    assert browser.navigations == []
    assert browser.initializations == 0
    assert handoff.state["status"] == "READY"
    assert handoff.latest is not None
    assert handoff.latest["completeness"] == "partial"
    assert handoff.latest["metadata"]["data_mode"] == "sample"
    assert handoff.latest["items"][0]["year_built"] is None
    assert "observed_at" not in first[0]


@pytest.mark.asyncio
async def test_challenge_pauses_once_without_overwriting_last_success(tmp_path: Path) -> None:
    browser = FakeBrowser()
    handoff = controller(tmp_path, browser)
    await handoff.resume()
    original = (tmp_path / "exports/latest.canonical.json").read_bytes()
    handoff.state["last_attempt_at"] = (datetime.now(UTC) - timedelta(days=1)).isoformat()
    browser.page = {
        "url": "https://www.anjuke.com/esfcommon-captcha-wy?serial=private",
        "title": "验证",
        "text": "请完成验证",
        "cards": [],
    }
    for _ in range(3):
        with pytest.raises(HTTPException) as error:
            await handoff.snapshot()
        assert error.value.status_code == 503
    assert browser.navigations == [TARGET_URL]
    assert handoff.state["status"] == "NEEDS_HUMAN"
    assert "private" not in json.dumps(handoff.state)
    assert (tmp_path / "exports/latest.canonical.json").read_bytes() == original


@pytest.mark.asyncio
async def test_resume_on_challenge_never_navigates_or_claims_success(tmp_path: Path) -> None:
    browser = FakeBrowser()
    browser.page["title"] = "安全验证"
    handoff = controller(tmp_path, browser)
    with pytest.raises(HTTPException) as error:
        await handoff.resume()
    assert error.value.status_code == 409
    assert browser.navigations == []
    assert handoff.state["status"] == "NEEDS_HUMAN"


@pytest.mark.asyncio
async def test_browser_lost_never_recreates_session_during_scheduled_check(tmp_path: Path) -> None:
    browser = FakeBrowser()
    handoff = controller(tmp_path, browser)
    await handoff.resume()
    browser.fail = True
    handoff.state["last_attempt_at"] = None
    with pytest.raises(HTTPException):
        await handoff.snapshot()
    assert browser.initializations == 0
    assert handoff.state["status"] == "NEEDS_HUMAN"


@pytest.mark.asyncio
async def test_controller_restart_retains_data_but_requires_human_resume(tmp_path: Path) -> None:
    browser = FakeBrowser()
    handoff = controller(tmp_path, browser)
    await handoff.resume()
    restored = controller(tmp_path, browser)
    assert restored.state["status"] == "PAUSED"
    assert restored.latest == handoff.latest
    with pytest.raises(HTTPException):
        await restored.snapshot()
    assert browser.navigations == []


@pytest.mark.asyncio
async def test_parse_failure_keeps_last_good_data(tmp_path: Path) -> None:
    browser = FakeBrowser()
    handoff = controller(tmp_path, browser)
    await handoff.resume()
    old = copy.deepcopy(handoff.latest)
    browser.page["cards"][0]["raw_text"] = "unrecognized card"
    with pytest.raises(HTTPException):
        await handoff.resume()
    assert handoff.latest == old
    assert handoff.state["status"] == "NEEDS_HUMAN"


@pytest.mark.parametrize(
    "alteration",
    [
        {"url": "https://example.com/"},
        {"cards": []},
        {"title": "安全验证"},
    ],
)
def test_gate_rejects_wrong_page_empty_page_and_visible_challenge(
    alteration: dict[str, Any],
) -> None:
    page = {**listing_page(), **alteration}
    assert pause_reason(page, TARGET_URL, 1) is not None


@pytest.mark.asyncio
async def test_api_csrf_guard_and_stale_feed(tmp_path: Path) -> None:
    browser = FakeBrowser()
    handoff = controller(tmp_path, browser)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=create_app(handoff)), base_url="http://testserver"
    ) as client:
        assert (await client.post("/action/open")).status_code == 403
        assert (
            await client.post(
                "/action/open",
                headers={
                    "X-Handoff-Action": "1",
                    "Origin": "https://attacker.test",
                },
            )
        ).status_code == 403
        assert browser.navigations == []
        assert (await client.get("/feed")).status_code == 503
        assert (
            await client.post("/action/resume", headers={"X-Handoff-Action": "1"})
        ).status_code == 200
        assert (await client.get("/feed")).status_code == 200
        handoff.state["last_success_at"] = (datetime.now(UTC) - timedelta(hours=37)).isoformat()
        assert (await client.get("/feed")).status_code == 503


@pytest.mark.asyncio
async def test_user_pause_wins_over_inflight_capture(tmp_path: Path) -> None:
    started, release = asyncio.Event(), asyncio.Event()

    class SlowBrowser(FakeBrowser):
        async def navigate(self, url: str) -> None:
            started.set()
            await release.wait()

    browser = SlowBrowser()
    handoff = controller(tmp_path, browser)
    await handoff.resume()
    old = handoff.latest
    handoff.state["last_attempt_at"] = None
    task = asyncio.create_task(handoff.snapshot())
    await started.wait()
    handoff.pause()
    release.set()
    with pytest.raises(HTTPException):
        await task
    assert handoff.state["status"] == "PAUSED"
    assert handoff.latest is old


@pytest.mark.asyncio
async def test_old_browser_page_cannot_be_relabelled_as_fresh(tmp_path: Path) -> None:
    browser = FakeBrowser()
    browser.page["loaded_at"] = (datetime.now(UTC) - timedelta(hours=2)).isoformat()
    handoff = controller(tmp_path, browser)
    with pytest.raises(HTTPException):
        await handoff.resume()
    assert handoff.latest is None
    assert handoff.state["status"] == "NEEDS_HUMAN"
    assert browser.navigations == []


@pytest.mark.asyncio
async def test_selenium_reattaches_live_session_without_new_context(tmp_path: Path) -> None:
    class ProtocolStub(SeleniumSession):
        calls: list[tuple[str, str, Any]] = []

        async def command(self, method: str, path: str, body: Any = None) -> Any:
            self.calls.append((method, path, body))
            if path == "/session":
                return {"sessionId": "fixture-id"}
            return "about:blank"

    first = ProtocolStub("http://browser:4444", tmp_path)
    await first.initialize()
    second = ProtocolStub("http://browser:4444", tmp_path)
    await second.initialize()
    assert second.session_id == first.session_id
    assert sum(path == "/session" for _, path, _ in first.calls) == 1
    assert (tmp_path / "webdriver.json").stat().st_mode & 0o777 == 0o600


@pytest.mark.asyncio
async def test_price_changes_survive_repeated_resume_and_restart(tmp_path: Path) -> None:
    browser = FakeBrowser()
    browser.page["loaded_at"] = (datetime.now(UTC) - timedelta(minutes=5)).isoformat()
    handoff = controller(tmp_path, browser)
    await handoff.resume()
    browser.page["loaded_at"] = datetime.now(UTC).isoformat()
    browser.page["cards"][0]["raw_text"] = browser.page["cards"][0]["raw_text"].replace(
        "280 万", "270 万"
    )
    await handoff.resume()
    changes = handoff.latest["metadata"]["changes"]
    assert changes[0]["event"] == "ASKING_PRICE_DECREASED"
    assert float(changes[0]["delta_wan"]) == -10
    before = (tmp_path / "exports/latest.canonical.json").read_bytes()
    await handoff.resume()
    assert (tmp_path / "exports/latest.canonical.json").read_bytes() == before
    restored = controller(tmp_path, browser)
    assert restored.latest["metadata"]["changes"] == changes
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=create_app(restored)), base_url="http://testserver"
    ) as client:
        history = (await client.get("/observations")).json()
        assert history["current"] is False
        assert history["changes"] == changes
        assert browser.navigations == []


@pytest.mark.asyncio
async def test_detected_widget_pauses_and_resumes_without_navigation(tmp_path: Path) -> None:
    browser = FakeBrowser()
    handoff = controller(tmp_path, browser)
    await handoff.resume()
    old = handoff.latest
    browser.page["antibot"] = {
        "detections": [{"id": "netease", "evidence": "visible_widget", "requires_human": True}]
    }
    with pytest.raises(HTTPException):
        await handoff.resume()
    assert handoff.latest is old
    assert handoff.state["antibot"]["requires_human"] is True
    browser.page["antibot"] = {"detections": []}
    await handoff.resume()
    assert handoff.state["status"] == "READY"
    assert handoff.state["antibot"]["requires_human"] is False
    assert browser.navigations == []


@pytest.mark.asyncio
async def test_capture_automatically_analyzes_and_serves_matching_batch(tmp_path: Path) -> None:
    from scripts.research_pipeline import ResearchPipeline

    browser = FakeBrowser()
    export_dir = tmp_path / "exports"
    handoff = Handoff(
        browser,
        tmp_path / "state",
        export_dir,
        min_cards=1,
        analyzer=ResearchPipeline(export_dir),
    )
    await handoff.resume()
    assert handoff.state["analysis_status"] == "complete"
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=create_app(handoff)), base_url="http://testserver"
    ) as client:
        first = (await client.get("/analysis")).json()
        assert first["current"] is True
        assert first["summary"]["observed"] == 1
        assert first["summary"]["without_comparables"] == 1
        assert (await client.get("/health")).status_code == 200
        await handoff.resume()
        assert (await client.get("/analysis")).json() == first
        handoff.pause()
        paused = (await client.get("/analysis")).json()
        assert paused["current"] is False
        assert paused["input_fingerprint"] == first["input_fingerprint"]
        assert (await client.get("/health")).status_code == 503
        assert browser.navigations == []

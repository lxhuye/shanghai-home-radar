"""Opt-in real-browser checks against a local fixture, never a property website."""

from __future__ import annotations

import json
import os
import threading
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
from fastapi import HTTPException

pytest.importorskip("playwright")

from scripts.browser_handoff import Handoff  # noqa: E402
from scripts.local_browser_handoff import LocalBrowser  # noqa: E402
from scripts.research_pipeline import ResearchPipeline  # noqa: E402

pytestmark = pytest.mark.skipif(
    not os.environ.get("SHR_TEST_BROWSER_EXECUTABLE"),
    reason="set SHR_TEST_BROWSER_EXECUTABLE to run local browser end-to-end checks",
)


@pytest.mark.asyncio
async def test_real_browser_detects_pauses_recovers_and_records_price_cut(tmp_path: Path) -> None:
    fixture = {"challenge": False, "price": 280, "requests": 0}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            fixture["requests"] += 1
            overlay = (
                '<div class="yidun_panel">Fixture challenge</div>' if fixture["challenge"] else ""
            )
            body = f"""<!doctype html><html><body>{overlay}
            <a class="property-ex" href="https://shanghai.anjuke.com/prop/view/S123">
            测试房源 2 室 1 厅 1 卫 60㎡ 南 中层(共6层)
            1901年建造 测试小区 徐汇长桥测试路 满五年 {fixture["price"]} 万 46667元/㎡
            </a></body></html>""".encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format: str, *args: object) -> None:
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    browser = LocalBrowser(
        tmp_path / "profile",
        executable_path=os.environ["SHR_TEST_BROWSER_EXECUTABLE"],
        headless=True,
    )
    target = f"http://127.0.0.1:{server.server_port}/listings"
    handoff = Handoff(
        browser,
        tmp_path / "state",
        tmp_path / "exports",
        target_url=target,
        min_cards=1,
        analyzer=ResearchPipeline(tmp_path / "exports"),
    )
    try:
        await handoff.open_browser(navigate=True)
        await handoff.resume()
        assert handoff.state["analysis_status"] == "complete"
        assert handoff.latest["metadata"]["changes"][0]["event"] == "FIRST_SEEN_IN_PILOT"
        assert browser.page is not None
        # Invisible widgets and loaded SDK resources do not block readable listings.
        await browser.page.evaluate("""() => {
          const div = document.createElement('div'); div.className='yidun_panel';
          div.style.display='none'; document.body.append(div);
          const script = document.createElement('script');
          script.type='application/json'; script.src='https://c.dun.163.com/sdk.js?secret=private';
          document.body.append(script);
        }""")
        await handoff.resume()
        assert handoff.state["antibot"]["detections"][0]["requires_human"] is False
        assert "private" not in json.dumps(handoff.state)

        fixture["challenge"] = True
        handoff.state["last_attempt_at"] = (datetime.now(UTC) - timedelta(days=1)).isoformat()
        with pytest.raises(HTTPException):
            await handoff.snapshot()
        assert handoff.state["status"] == "NEEDS_HUMAN"
        assert handoff.state["antibot"]["detections"][0]["id"] == "netease"
        attempts = fixture["requests"]
        for _ in range(3):
            with pytest.raises(HTTPException):
                await handoff.snapshot()
        assert fixture["requests"] == attempts

        # Test fixture resolution represents a human completing the site's challenge.
        fixture.update(challenge=False, price=270)
        await browser.page.reload(wait_until="domcontentloaded")
        await handoff.resume()
        assert handoff.state["status"] == "READY"
        assert handoff.latest["metadata"]["changes"][0]["event"] == "ASKING_PRICE_DECREASED"
        assert float(handoff.latest["metadata"]["changes"][0]["delta_wan"]) == -10
        assert handoff.state["analysis_summary"]["price_cuts"] == 1
        await handoff.resume()
        assert handoff.latest["metadata"]["changes"][0]["event"] == "ASKING_PRICE_DECREASED"
    finally:
        await browser.close()
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)

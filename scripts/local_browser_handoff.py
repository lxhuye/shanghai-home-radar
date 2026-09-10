"""Run the same monitoring workflow with a dedicated local Playwright browser.

Install the project's [browser] extra, then run python -m scripts.local_browser_handoff.
The browser opens only after an explicit panel action. Its profile is separate from Chrome.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from playwright.async_api import BrowserContext, Page, Playwright, async_playwright
from playwright.async_api import Error as PlaywrightError

from scripts.browser_handoff import PAGE_EVIDENCE_SCRIPT, BrowserUnavailable, Handoff, create_app


class LocalBrowser:
    mode = "local"

    def __init__(
        self, profile: Path, *, executable_path: str | None = None, headless: bool = False
    ) -> None:
        self.profile = profile
        self.executable_path = executable_path
        self.headless = headless
        self.runtime: Playwright | None = None
        self.context: BrowserContext | None = None
        self.page: Page | None = None

    async def initialize(self) -> None:
        try:
            if self.page and not self.page.is_closed():
                await self.page.bring_to_front()
                return
            await self.close()
            self.profile.mkdir(parents=True, exist_ok=True, mode=0o700)
            self.runtime = await async_playwright().start()
            self.context = await self.runtime.chromium.launch_persistent_context(
                str(self.profile),
                executable_path=self.executable_path,
                channel=None if self.executable_path else "chrome",
                headless=self.headless,
                viewport={"width": 1440, "height": 1000},
            )
            self.page = (
                self.context.pages[0] if self.context.pages else await self.context.new_page()
            )
            self.page.set_default_timeout(10000)
            await self.page.bring_to_front()
        except PlaywrightError as exc:
            await self.close()
            raise BrowserUnavailable("dedicated local browser unavailable") from exc

    async def navigate(self, url: str) -> None:
        if self.page is None or self.page.is_closed():
            raise BrowserUnavailable("open the dedicated local browser first")
        try:
            await self.page.goto(url, wait_until="domcontentloaded", timeout=30000)
        except PlaywrightError as exc:
            raise BrowserUnavailable("navigation failed; no automatic retry") from exc

    async def evidence(self) -> dict[str, Any]:
        if self.page is None or self.page.is_closed():
            raise BrowserUnavailable("dedicated local browser closed")
        try:
            result = await self.page.evaluate("() => {" + PAGE_EVIDENCE_SCRIPT + "}")
        except PlaywrightError as exc:
            raise BrowserUnavailable("page inspection failed") from exc
        if not isinstance(result, dict):
            raise BrowserUnavailable("page inspection returned no evidence")
        return result

    async def close(self) -> None:
        try:
            if self.context:
                await self.context.close()
        finally:
            if self.runtime:
                await self.runtime.stop()
            self.context = self.page = self.runtime = None


def main() -> None:
    import uvicorn

    from scripts.research_pipeline import ResearchPipeline

    root = Path(__file__).resolve().parents[1]
    private = root / "data/local_browser"
    browser = LocalBrowser(
        private / "profile", executable_path=os.environ.get("SHR_BROWSER_EXECUTABLE")
    )
    export_dir = root / "data/exports/browser_handoff"
    handoff = Handoff(
        browser,
        private / "state",
        export_dir,
        analyzer=ResearchPipeline(export_dir, root / "config"),
    )
    app = create_app(handoff)
    original_lifespan = app.router.lifespan_context

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        try:
            async with original_lifespan(app):
                yield
        finally:
            await browser.close()

    app.router.lifespan_context = lifespan
    uvicorn.run(app, host="127.0.0.1", port=5057, access_log=False)


if __name__ == "__main__":
    main()

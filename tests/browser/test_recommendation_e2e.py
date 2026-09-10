"""Real Chromium UI contract checks using isolated, in-memory HTTP responses."""

from __future__ import annotations

import copy
import json
import os
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("playwright")
from playwright.async_api import async_playwright, expect  # noqa: E402

HTML = Path("scripts/browser_handoff.html").read_text()

pytestmark = pytest.mark.skipif(
    not os.environ.get("SHR_TEST_BROWSER_EXECUTABLE"),
    reason="set SHR_TEST_BROWSER_EXECUTABLE for local browser checks",
)


@pytest.mark.asyncio
async def test_personal_profile_evidence_and_blind_review_contract() -> None:
    observed_at = "2026-09-08T07:12:25+00:00"
    profile: dict[str, Any] = {
        "budget_min_wan": 250,
        "budget_max_wan": 300,
        "districts": ["徐汇"],
        "bedrooms_min": None,
        "bedrooms_max": None,
        "elevator_required": None,
        "allowed_floor_labels": [],
        "commute_destination": None,
        "commute_max_minutes": None,
        "commute_not_required": False,
        "confirmed": False,
    }
    base = {
        "observed_at": observed_at,
        "area_sqm": 60,
        "price_wan": 280,
        "missing_evidence": [],
    }
    items = [
        {**base, "listing_id": "fixture-a", "community": "测试甲小区"},
        {**base, "listing_id": "fixture-b", "community": "测试乙小区"},
    ]
    saved_evidence: list[dict[str, Any]] = []
    saved_runs: list[dict[str, Any]] = []
    errors: list[str] = []

    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(
            executable_path=os.environ["SHR_TEST_BROWSER_EXECUTABLE"], headless=True
        )
        page = await browser.new_page()
        page.on("pageerror", lambda error: errors.append(str(error)))

        async def route_request(route: Any) -> None:
            request = route.request
            path = request.url.removeprefix("http://house-radar.test")
            if path == "/":
                await route.fulfill(body=HTML, content_type="text/html; charset=utf-8")
                return
            if request.method in {"PUT", "POST"}:
                assert request.headers.get("x-handoff-action") == "1"
            response: Any
            if path == "/status":
                response = {"status": "READY", "records": 2, "reason": "本地测试"}
            elif path == "/observations":
                response = {
                    "items": items,
                    "changes": [],
                    "observed_at": observed_at,
                    "current": True,
                }
            elif path == "/analysis":
                response = {
                    "items": items,
                    "observed_at": observed_at,
                    "generated_at": observed_at,
                    "current": True,
                    "input_fingerprint": "fixture-report",
                    "digest": "本地测试",
                }
            elif path == "/buyer-profile":
                if request.method == "PUT":
                    profile.update(request.post_data_json)
                response = profile
            elif path == "/recommendations":
                response = {
                    "profile": profile,
                    "observed_at": observed_at,
                    "current": True,
                    "summary": {
                        "observed": 2,
                        "matched": 1,
                        "excluded": 0,
                        "needs_evidence": 1,
                        "profile_incomplete": 0,
                        "recommended": 0,
                    },
                    "readiness": {"blockers": [], "validation_status": "uncalibrated"},
                    "items": [
                        {
                            **item,
                            "fit": {
                                "status": "MATCH" if index == 0 else "NEEDS_EVIDENCE",
                                "reasons": [],
                                "missing": [] if index == 0 else ["需要核实电梯"],
                            },
                        }
                        for index, item in enumerate(items)
                    ],
                }
            elif path == "/property-evidence":
                saved_evidence.append(request.post_data_json)
                response = {"saved": True}
            elif path == "/validation-runs":
                if request.method == "POST":
                    saved_runs.append(request.post_data_json)
                    response = {
                        **request.post_data_json,
                        "batch_id": "fixture-run",
                        "status": "LABELING",
                        "labeled_count": 0,
                        "sample_size": 50,
                        "items": [
                            {
                                "listing_id": "fixture-a",
                                "listing": copy.deepcopy(items[0]),
                                "label": None,
                            }
                        ],
                    }
                else:
                    response = {"items": []}
            else:
                await route.fulfill(status=404, body="{}", content_type="application/json")
                return
            await route.fulfill(body=json.dumps(response), content_type="application/json")

        await page.route("http://house-radar.test/**", route_request)
        try:
            await page.goto("http://house-radar.test/")
            await expect(page.locator("#listings tr")).to_have_count(2)
            form = page.locator("#profile-form")
            confirmation = form.locator('[name="confirmed"]')
            await expect(confirmation).not_to_be_checked()
            await expect(form.locator('[name="elevator_required"]')).to_have_value("")
            await form.locator('[name="commute_destination"]').fill("测试公司")
            await form.locator('[name="commute_max_minutes"]').fill("40")
            await confirmation.check()
            await form.locator('[name="bedrooms_min"]').fill("2")
            await expect(confirmation).not_to_be_checked()
            # A monitor refresh must not wipe an unsaved destination or choose a requirement.
            await page.evaluate("refresh()")
            await expect(form.locator('[name="commute_destination"]')).to_have_value("测试公司")
            await confirmation.check()
            await form.get_by_role("button", name="保存购房条件").click()
            await expect(page.locator("#profile-message")).to_contain_text("条件已保存")
            assert profile["confirmed"] is True
            assert profile["commute_max_minutes"] == 40
            assert profile["elevator_required"] is None
            await page.locator("#filter").select_option("matched")
            await expect(page.locator("#listings tr")).to_have_count(1)
            await expect(page.locator("#listings")).to_contain_text("测试甲小区")
            await page.locator("#filter").select_option("needs_evidence")
            await expect(page.locator("#listings")).to_contain_text("测试乙小区")
            await page.get_by_role("button", name="查看依据", exact=True).click()
            dialog = page.locator("#detail")
            await expect(dialog).to_contain_text("需要核实电梯")
            await dialog.get_by_text("补充这套房的核实证据", exact=True).click()
            evidence_form = dialog.locator("form")
            await evidence_form.locator('[name="value"]').fill("有")
            await evidence_form.locator('[name="source_url"]').fill("https://example.com/proof")
            await evidence_form.locator('[name="observed_at"]').fill("2026-09-08T14:00")
            await evidence_form.locator('[name="verified_by"]').fill("测试核实人")
            await evidence_form.get_by_role("button", name="保存核实证据").click()
            await expect(evidence_form.locator('[role="status"]')).to_contain_text("证据已保存")
            assert saved_evidence[-1]["value"] is True
            assert saved_evidence[-1]["listing_id"] == "fixture-b"
            await evidence_form.locator('[name="field"]').select_option("commute_minutes")
            await evidence_form.locator('[name="value"]').fill("35")
            await evidence_form.locator('[name="destination"]').fill("测试公司")
            await evidence_form.get_by_role("button", name="保存核实证据").click()
            await expect(evidence_form.get_by_role("button")).to_be_enabled()
            assert saved_evidence[-1]["value"] == {"destination": "测试公司", "minutes": 35}
            await page.locator("#close-detail").click()
            await page.get_by_text("独立复核与盲评", exact=True).click()
            create = page.locator("#validation-create")
            await expect(create.locator('[name="independent_review"]')).not_to_be_checked()
            await create.locator('[name="name"]').fill("练习复核")
            await create.locator('[name="reviewer"]').fill("测试评审")
            await create.get_by_role("button").click()
            await expect(page.locator("#validation-workspace")).to_contain_text("练习复核")
            assert saved_runs[-1]["independent_review"] is False
            await expect(page.locator("#validation-workspace")).not_to_contain_text("模型判断")
            await expect(page.locator("#validation-workspace")).not_to_contain_text("模型对照")
            await expect(page.locator("#validation-workspace")).not_to_contain_text("前五名")
            assert errors == []
        finally:
            await browser.close()

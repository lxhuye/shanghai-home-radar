#!/usr/bin/env python3
"""Fail-closed readiness gate for unattended LIVE operation."""

from __future__ import annotations

import argparse
import asyncio
import hmac
import json
import re
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

# Keep the operator command usable before an editable package install.
_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))
for _source in (
    "packages/models/src",
    "packages/shared/src",
    "services/collector/src",
):
    sys.path.insert(0, str(_ROOT / _source))

from home_radar_collector.registry import build_configured_adapter  # noqa: E402
from home_radar_models.enums import CrawlCompleteness  # noqa: E402
from home_radar_shared.config import Settings, get_settings  # noqa: E402

from scripts.daily_scheduler import parse_schedule_time  # noqa: E402
from scripts.release_fingerprint import (  # noqa: E402
    ReleaseFingerprintError,
    collect_release_files,
    source_tree_hash,
)

READY_RECOMMENDATIONS = {"P6_READY", "P6_READY_WITH_LIMITATIONS"}
AUTHORIZED_SOURCES = {"authorized_api", "partner_csv"}


def check(
    code: str,
    passed: bool,
    detail: str,
    action: str | None = None,
) -> dict[str, Any]:
    value: dict[str, Any] = {"code": code, "passed": passed, "detail": detail}
    if not passed and action:
        value["action"] = action
    return value


def assess_static_readiness(
    settings: Settings,
    *,
    project_root: Path,
    actual_source_tree_hash: str | None = None,
) -> dict[str, Any]:
    checks: list[dict[str, Any]] = []
    checks.append(
        check(
            "live_data_mode",
            settings.market_data_mode == "live",
            f"configured mode is {settings.market_data_mode}",
            "set SHR_MARKET_DATA_MODE=live only after the source contract is accepted",
        )
    )
    checks.append(
        check(
            "authorized_listing_source",
            settings.collector_source in AUTHORIZED_SOURCES,
            f"configured source is {settings.collector_source}",
            "configure authorized_api or partner_csv",
        )
    )

    live_settings = settings.model_copy(update={"market_data_mode": "live"})
    try:
        build_configured_adapter(live_settings)
        adapter_valid = True
        adapter_detail = "collector configuration satisfies the LIVE adapter boundary"
    except (TypeError, ValueError) as exc:
        adapter_valid = False
        adapter_detail = f"collector configuration rejected: {type(exc).__name__}"
    checks.append(
        check(
            "live_adapter_configuration",
            adapter_valid,
            adapter_detail,
            "fix the source endpoint, scope, or authentication configuration",
        )
    )

    source_input_ready, source_detail = _source_input_readiness(settings, project_root)
    checks.append(
        check(
            "source_input_available",
            source_input_ready,
            source_detail,
            "provide an HTTPS authorized API or a finalized CSV and companion manifest",
        )
    )
    checks.append(
        check(
            "result_delivery_configured",
            bool(settings.daily_result_webhook_url),
            (
                "daily result webhook is configured"
                if settings.daily_result_webhook_url
                else "daily result webhook is not configured"
            ),
            "configure SHR_DAILY_RESULT_WEBHOOK_URL and complete a test delivery",
        )
    )

    recommendation = read_calibration_recommendation(
        project_root / "P57_CALIBRATION_SET_A_REPORT.md"
    )
    checks.append(
        check(
            "blind_calibration_gate",
            recommendation in READY_RECOMMENDATIONS,
            f"P5.7 recommendation is {recommendation or 'missing'}",
            "freeze human labels, run the frozen model, reveal, and finalize P5.7",
        )
    )

    schedule_ready, schedule_detail = _schedule_readiness(settings)
    checks.append(
        check(
            "daily_schedule_valid",
            schedule_ready,
            schedule_detail,
            "set a valid HH:MM schedule and IANA timezone",
        )
    )
    fingerprint_ready, fingerprint_detail = _release_fingerprint_readiness(
        settings,
        project_root=project_root,
        actual_source_tree_hash=actual_source_tree_hash,
    )
    checks.append(
        check(
            "release_fingerprint_configured",
            fingerprint_ready,
            fingerprint_detail,
            "generate .env.release with scripts/release_fingerprint.py and rebuild",
        )
    )
    checks.append(
        check(
            "source_probe",
            False,
            "read-only source probe has not run",
            "run scripts/live_readiness.py --probe-source",
        )
    )

    report = {
        "generated_at": datetime.now(UTC).isoformat(),
        "status": "BLOCKED",
        "ready_for_unattended_live": False,
        "blockers": [],
        "checks": checks,
    }
    _recalculate_status(report)
    return report


async def probe_source(settings: Settings) -> dict[str, Any]:
    """Read and normalize the configured source without creating a crawl run."""
    if settings.market_data_mode != "live":
        return check(
            "source_probe",
            False,
            "source probe requires live data mode",
            "set LIVE only after reviewing the source contract",
        )
    try:
        adapter = build_configured_adapter(settings)
        result = await adapter.fetch()
        normalized_ids: set[str] = set()
        normalized_urls: set[str] = set()
        for raw_item in result.raw_items:
            observation = adapter.normalize(raw_item)
            if observation.source_listing_id in normalized_ids:
                raise ValueError("duplicate source listing identifier")
            source_url = str(observation.source_url)
            if source_url in normalized_urls:
                raise ValueError("duplicate source listing URL")
            normalized_ids.add(observation.source_listing_id)
            normalized_urls.add(source_url)
        passed = (
            result.completeness is CrawlCompleteness.COMPLETE
            and bool(result.raw_items)
            and len(normalized_ids) == len(result.raw_items)
        )
        return check(
            "source_probe",
            passed,
            (
                f"completeness={result.completeness.value}, "
                f"items={len(result.raw_items)}, normalized={len(normalized_ids)}"
            ),
            "resolve completeness or row-normalization failures before LIVE collection",
        )
    except Exception as exc:
        return check(
            "source_probe",
            False,
            f"source probe failed: {type(exc).__name__}",
            "verify endpoint access, credentials, pagination, and the canonical row contract",
        )


def apply_source_probe(report: dict[str, Any], source_probe: dict[str, Any]) -> None:
    checks = report.get("checks")
    if not isinstance(checks, list):
        raise ValueError("readiness report has no checks")
    report["checks"] = [
        item for item in checks if not isinstance(item, dict) or item.get("code") != "source_probe"
    ]
    report["checks"].append(source_probe)
    _recalculate_status(report)


def read_calibration_recommendation(path: Path) -> str | None:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError):
        return None
    for index, line in enumerate(lines):
        if line.strip().lower() != "## recommendation":
            continue
        for candidate in lines[index + 1 :]:
            value = candidate.strip().strip("`")
            if value:
                return value
    return None


def _source_input_readiness(settings: Settings, project_root: Path) -> tuple[bool, str]:
    if settings.collector_source == "authorized_api":
        parsed = urlsplit(settings.collector_endpoint)
        passed = parsed.scheme == "https" and bool(parsed.hostname)
        return passed, (
            "authorized API uses HTTPS" if passed else "authorized API HTTPS endpoint is missing"
        )
    if settings.collector_source == "partner_csv":
        csv_path = Path(settings.collector_endpoint)
        if not csv_path.is_absolute():
            csv_path = project_root / csv_path
        manifest_path = Path(f"{csv_path}.manifest.json")
        passed = csv_path.is_file() and manifest_path.is_file()
        return passed, (
            "partner CSV and manifest are present"
            if passed
            else "partner CSV or companion manifest is missing"
        )
    return False, "configured source is not an authorized LIVE listing source"


def _schedule_readiness(settings: Settings) -> tuple[bool, str]:
    try:
        parse_schedule_time(settings.daily_schedule_time)
        ZoneInfo(settings.daily_timezone)
    except (ValueError, ZoneInfoNotFoundError):
        return False, "daily schedule or timezone is invalid"
    return True, f"daily schedule is {settings.daily_schedule_time} {settings.daily_timezone}"


def _release_fingerprint_readiness(
    settings: Settings,
    *,
    project_root: Path,
    actual_source_tree_hash: str | None,
) -> tuple[bool, str]:
    commit = settings.source_commit.strip()
    configured_hash = settings.source_tree_hash.strip().lower()
    if not re.fullmatch(r"(?:[0-9a-fA-F]{40}|[0-9a-fA-F]{64})", commit):
        return False, "release commit is missing or malformed"
    if not re.fullmatch(r"[0-9a-f]{64}", configured_hash):
        return False, "release source-tree hash is missing or malformed"
    try:
        computed_hash = actual_source_tree_hash or source_tree_hash(
            project_root,
            collect_release_files(project_root),
        )
    except (OSError, ReleaseFingerprintError):
        return False, "deployed source tree could not be fingerprinted"
    if not hmac.compare_digest(configured_hash, computed_hash.lower()):
        return False, "configured source-tree hash does not match deployed files"
    return True, "release commit and deployed source-tree hash are verified"


def _recalculate_status(report: dict[str, Any]) -> None:
    checks = report.get("checks")
    if not isinstance(checks, list):
        raise ValueError("readiness report has no checks")
    blockers = [
        str(item["code"])
        for item in checks
        if isinstance(item, dict) and not bool(item.get("passed"))
    ]
    report["blockers"] = blockers
    report["ready_for_unattended_live"] = not blockers
    report["status"] = (
        "READY" if not blockers else "PROBE_REQUIRED" if blockers == ["source_probe"] else "BLOCKED"
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--probe-source",
        action="store_true",
        help="perform a read-only fetch and normalize every returned listing",
    )
    parser.add_argument("--project-root", type=Path, default=Path.cwd())
    return parser


def main() -> int:
    args = build_parser().parse_args()
    settings = get_settings()
    report = assess_static_readiness(settings, project_root=args.project_root.resolve())
    if args.probe_source:
        source_probe = asyncio.run(probe_source(settings))
        apply_source_probe(report, source_probe)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["ready_for_unattended_live"] else 2


if __name__ == "__main__":
    raise SystemExit(main())

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from home_radar_models.enums import CrawlCompleteness
from home_radar_shared.config import Settings

from scripts import live_readiness


def settings(**overrides: object) -> Settings:
    defaults: dict[str, object] = {
        "market_data_mode": "live",
        "collector_source": "partner_csv",
        "collector_endpoint": "data/inbox/listings.csv",
        "collector_auth_mode": "NONE",
        "daily_result_webhook_url": "https://notify.example.invalid/daily",
        "source_commit": "a" * 40,
        "source_tree_hash": "b" * 64,
    }
    defaults.update(overrides)
    return Settings(_env_file=None, **defaults)  # type: ignore[call-arg]


def test_static_gate_requires_source_probe_for_complete_partner_csv_setup(
    tmp_path: Path,
) -> None:
    inbox = tmp_path / "data" / "inbox"
    inbox.mkdir(parents=True)
    csv_path = inbox / "listings.csv"
    csv_path.write_text("placeholder", encoding="utf-8")
    Path(f"{csv_path}.manifest.json").write_text("{}", encoding="utf-8")
    (tmp_path / "P57_CALIBRATION_SET_A_REPORT.md").write_text(
        "# Result\n\n## Recommendation\n\nP6_READY_WITH_LIMITATIONS\n",
        encoding="utf-8",
    )

    report = live_readiness.assess_static_readiness(
        settings(),
        project_root=tmp_path,
        actual_source_tree_hash="b" * 64,
    )

    assert report["status"] == "PROBE_REQUIRED"
    assert report["ready_for_unattended_live"] is False
    assert report["blockers"] == ["source_probe"]


def test_static_gate_lists_every_current_external_blocker(tmp_path: Path) -> None:
    report = live_readiness.assess_static_readiness(
        settings(
            market_data_mode="sample",
            collector_source="sample_json",
            collector_endpoint="data/sample/listings.json",
            daily_result_webhook_url=None,
            source_commit="unknown",
            source_tree_hash="unknown",
        ),
        project_root=tmp_path,
    )

    assert report["status"] == "BLOCKED"
    assert set(report["blockers"]) == {
        "live_data_mode",
        "authorized_listing_source",
        "live_adapter_configuration",
        "source_input_available",
        "result_delivery_configured",
        "blind_calibration_gate",
        "release_fingerprint_configured",
        "source_probe",
    }


def test_successful_source_probe_completes_otherwise_ready_report(tmp_path: Path) -> None:
    inbox = tmp_path / "data" / "inbox"
    inbox.mkdir(parents=True)
    csv_path = inbox / "listings.csv"
    csv_path.write_text("placeholder", encoding="utf-8")
    Path(f"{csv_path}.manifest.json").write_text("{}", encoding="utf-8")
    (tmp_path / "P57_CALIBRATION_SET_A_REPORT.md").write_text(
        "# Result\n\n## Recommendation\n\nP6_READY\n",
        encoding="utf-8",
    )
    report = live_readiness.assess_static_readiness(
        settings(),
        project_root=tmp_path,
        actual_source_tree_hash="b" * 64,
    )

    live_readiness.apply_source_probe(
        report,
        {
            "code": "source_probe",
            "passed": True,
            "detail": "completeness=complete, items=500, normalized=500",
        },
    )

    assert report["status"] == "READY"
    assert report["ready_for_unattended_live"] is True
    assert report["blockers"] == []


def test_static_gate_rejects_fingerprint_that_does_not_match_deployed_files(
    tmp_path: Path,
) -> None:
    report = live_readiness.assess_static_readiness(
        settings(),
        project_root=tmp_path,
        actual_source_tree_hash="c" * 64,
    )

    fingerprint = next(
        item for item in report["checks"] if item["code"] == "release_fingerprint_configured"
    )
    assert fingerprint["passed"] is False
    assert fingerprint["detail"] == ("configured source-tree hash does not match deployed files")
    assert "release_fingerprint_configured" in report["blockers"]


def test_failed_source_probe_keeps_report_blocked(tmp_path: Path) -> None:
    report = live_readiness.assess_static_readiness(settings(), project_root=tmp_path)

    live_readiness.apply_source_probe(
        report,
        {
            "code": "source_probe",
            "passed": False,
            "detail": "source probe failed: RuntimeError",
        },
    )

    assert report["status"] == "BLOCKED"
    assert report["ready_for_unattended_live"] is False
    assert "source_probe" in report["blockers"]


def test_calibration_recommendation_is_read_only_from_named_section(tmp_path: Path) -> None:
    path = tmp_path / "report.md"
    path.write_text(
        "Mention P6_READY elsewhere.\n\n## Recommendation\n\n`MORE_CALIBRATION_REQUIRED`\n",
        encoding="utf-8",
    )

    assert live_readiness.read_calibration_recommendation(path) == "MORE_CALIBRATION_REQUIRED"


@pytest.mark.asyncio
async def test_source_probe_reads_and_normalizes_without_writes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw_items = [{"listing_id": "one"}, {"listing_id": "two"}]

    class Adapter:
        async def fetch(self) -> object:
            return SimpleNamespace(
                completeness=CrawlCompleteness.COMPLETE,
                raw_items=raw_items,
            )

        def normalize(self, raw_item: dict[str, str]) -> object:
            return SimpleNamespace(
                source_listing_id=raw_item["listing_id"],
                source_url=f"https://example.invalid/{raw_item['listing_id']}",
            )

    monkeypatch.setattr(live_readiness, "build_configured_adapter", lambda _settings: Adapter())

    result = await live_readiness.probe_source(settings())

    assert result == {
        "code": "source_probe",
        "passed": True,
        "detail": "completeness=complete, items=2, normalized=2",
    }


@pytest.mark.asyncio
async def test_source_probe_fails_closed_without_exposing_exception_text(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        live_readiness,
        "build_configured_adapter",
        lambda _settings: (_ for _ in ()).throw(RuntimeError("secret provider detail")),
    )

    result = await live_readiness.probe_source(settings())

    assert result["passed"] is False
    assert result["detail"] == "source probe failed: RuntimeError"
    assert "secret" not in result["detail"]


@pytest.mark.asyncio
async def test_source_probe_refuses_non_live_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    called = False

    def build(_settings: Settings) -> object:
        nonlocal called
        called = True
        return object()

    monkeypatch.setattr(live_readiness, "build_configured_adapter", build)

    result = await live_readiness.probe_source(settings(market_data_mode="sample"))

    assert result["passed"] is False
    assert called is False

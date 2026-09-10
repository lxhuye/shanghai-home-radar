from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.export_open_source import export


def test_release_excludes_private_data_history_and_sync_copies(tmp_path: Path) -> None:
    source = tmp_path / "source"
    public = ("README.md", ".env.example", "services/a.py", "data/sample/p4_valuation_cases.json")
    private = (
        ".env",
        ".git/config",
        "data/raw/feed.json",
        "data/exports/labels.csv",
        "data/evidence/sample/browser_geocodes.json",
        "data/local_browser/profile/state.json",
        "apps/web/node_modules/library/index.js",
        "config/forecasting 2.yaml",
        "services/engine/contracts 2.py",
        "P57_CALIBRATION_SET_A_REPORT.md",
        "research/xuhui/README.md",
    )
    for name in (*public, *private):
        path = source / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("fixture")
    destination = tmp_path / "release"
    manifest = export(source, destination)
    assert set(manifest) == set(public)
    assert json.loads((tmp_path / "release.manifest.json").read_text()) == manifest
    assert all(not (destination / name).exists() for name in private)
    with pytest.raises(ValueError, match="never overwrite"):
        export(source, destination)


def test_release_rejects_symlink_into_private_file(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    secret = tmp_path / "private.txt"
    secret.write_text("private fixture")
    (source / "README.md").symlink_to(secret)
    with pytest.raises(ValueError, match="symlink"):
        export(source, tmp_path / "release")

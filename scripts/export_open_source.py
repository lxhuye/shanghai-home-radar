"""Build a fresh, allowlisted source snapshot without private data or Git history.

The destination must not exist. A manifest is written beside it, not into the public tree.
This is an export boundary, not a secrets scanner; review and scan before publishing.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path

ROOT_FILES = (
    ".dockerignore",
    ".env.example",
    ".gitignore",
    "README.md",
    "CONTRIBUTING.md",
    "SECURITY.md",
    "LICENSE",
    "pyproject.toml",
    "alembic.ini",
    "docker-compose.yml",
    "docker-compose.monitor.yml",
)
TREES = ("apps/web", "services", "packages", "scripts", "tests", "config", "infra", "docs")
SUFFIXES = {
    ".py",
    ".pyi",
    ".md",
    ".json",
    ".yaml",
    ".yml",
    ".toml",
    ".ini",
    ".mako",
    ".Dockerfile",
    ".conf",
    ".sh",
    ".html",
    ".ts",
    ".tsx",
    ".mjs",
    ".css",
}
SKIP_PARTS = {
    "node_modules",
    ".next",
    "out",
    "build",
    "dist",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    ".venv",
    ".git",
    "test-results",
    "playwright-report",
}
SKIP_FILES = {"docs/DAILY_AUTONOMY_GAP_AUDIT.md"}
FIXTURES = (
    "data/sample/example_source_listings.json",
    "data/sample/p4_valuation_cases.json",
    "data/sample/p5_future_cases.json",
    "data/evidence/demo/minimal_synthetic.yaml",
)


def source_files(root: Path) -> list[Path]:
    files = {root / name for name in (*ROOT_FILES, *FIXTURES) if (root / name).is_file()}
    for name in (*TREES, ".github/workflows"):
        for path in (root / name).rglob("*"):
            relative = path.relative_to(root)
            if SKIP_PARTS.intersection(relative.parts) or relative.as_posix() in SKIP_FILES:
                continue
            if re.search(r" [0-9]+\.[^.]+$", path.name):
                continue  # Local synchronization-conflict copies are not source modules.
            if path.name.startswith(".") and path.name != ".gitignore":
                continue
            if path.suffix not in SUFFIXES and path.name != ".gitignore":
                continue
            if path.is_file():
                files.add(path)
    for path in files:
        if path.is_symlink() or root not in path.resolve().parents:
            raise ValueError(f"refusing symlink or out-of-tree file: {path.relative_to(root)}")
    return sorted(files)


def export(root: Path, destination: Path) -> dict[str, str]:
    root = root.resolve()
    destination = destination.absolute()
    if destination.exists():
        raise ValueError("destination must not exist; never overwrite an earlier release")
    if destination == root or destination in root.parents:
        raise ValueError("destination cannot contain the source tree")
    files = source_files(root)
    destination.mkdir(parents=True)
    manifest: dict[str, str] = {}
    for source in files:
        relative = source.relative_to(root)
        content = source.read_bytes()
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
        target.chmod(0o755 if source.stat().st_mode & 0o111 else 0o644)
        manifest[relative.as_posix()] = hashlib.sha256(content).hexdigest()
    manifest_path = destination.with_name(f"{destination.name}.manifest.json")
    if manifest_path.exists():
        raise ValueError("manifest already exists; use a fresh destination")
    manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n")
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    manifest = export(args.source, args.output)
    print(json.dumps({"files": len(manifest), "license_present": "LICENSE" in manifest}))


if __name__ == "__main__":
    main()

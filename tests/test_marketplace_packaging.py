#!/usr/bin/env python3
"""Validate independently installable plugins in both marketplaces."""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PLUGINS = ROOT / "plugins"
SOURCE_SKILLS = ROOT / "skills"
CATALOG = ROOT / "release" / "catalog.json"
EXPECTED_PLUGINS = {
    "agent-finish-line",
    "advisor-review",
    "katok-reply-reuse",
    "learnus-course-copilot",
    "quant-stock-polling-trader",
    "quant-stock-technical",
    "stock-scenario-story",
    "yonsei-central-student-governance-counsel",
}


def load_json(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(value, dict), f"{path} must contain a JSON object"
    return value


def relative_files(root: Path) -> set[Path]:
    return {
        path.relative_to(root)
        for path in root.rglob("*")
        if path.is_file() and "__pycache__" not in path.parts
    }


def main() -> None:
    catalog = load_json(CATALOG)
    release_entries = {entry["name"]: entry for entry in catalog["skills"]}
    excluded_entries = {
        entry["name"]: entry for entry in catalog["excludedSkills"]
    }
    assert catalog["schemaVersion"] == 1
    assert set(release_entries) == EXPECTED_PLUGINS
    assert not excluded_entries
    assert {
        path.name for path in SOURCE_SKILLS.iterdir() if path.is_dir()
    } == EXPECTED_PLUGINS
    expected_versions = {
        name: entry["version"] for name, entry in release_entries.items()
    }

    codex_market = load_json(ROOT / ".agents/plugins/marketplace.json")
    claude_market = load_json(ROOT / ".claude-plugin/marketplace.json")
    assert codex_market["name"] == claude_market["name"] == "mrcha-skills"

    codex_entries = {entry["name"]: entry for entry in codex_market["plugins"]}
    claude_entries = {entry["name"]: entry for entry in claude_market["plugins"]}
    assert set(codex_entries) == EXPECTED_PLUGINS
    assert set(claude_entries) == EXPECTED_PLUGINS
    assert len(codex_market["plugins"]) == len(claude_market["plugins"]) == 8
    assert not (PLUGINS / "mrcha-skills").exists(), (
        "aggregate plugin must not remain installable"
    )
    assert not (SOURCE_SKILLS / "yonsei-club-argument-counsel").exists()
    assert not (PLUGINS / "yonsei-club-argument-counsel").exists()

    for plugin_name in EXPECTED_PLUGINS:
        plugin = PLUGINS / plugin_name
        codex_plugin = load_json(plugin / ".codex-plugin/plugin.json")
        claude_plugin = load_json(plugin / ".claude-plugin/plugin.json")
        assert (
            codex_entries[plugin_name]["source"]["path"] == f"./plugins/{plugin_name}"
        )
        assert claude_entries[plugin_name]["source"] == f"./plugins/{plugin_name}"
        assert codex_entries[plugin_name]["policy"] == {
            "installation": "AVAILABLE",
            "authentication": "ON_INSTALL",
        }
        assert codex_plugin["name"] == claude_plugin["name"] == plugin_name
        assert (
            codex_plugin["version"]
            == claude_plugin["version"]
            == expected_versions[plugin_name]
        )
        assert claude_entries[plugin_name]["version"] == expected_versions[plugin_name]
        assert codex_plugin["skills"] == claude_plugin["skills"] == "./skills/"

        source = SOURCE_SKILLS / plugin_name
        packaged = plugin / "skills" / plugin_name
        assert packaged.is_dir()
        assert not packaged.is_symlink(), f"{packaged} must be self-contained"
        packaged_skill_names = {
            path.name for path in (plugin / "skills").iterdir() if path.is_dir()
        }
        assert packaged_skill_names == {plugin_name}
        source_files = relative_files(source)
        packaged_files = relative_files(packaged)
        assert source_files == packaged_files, f"{plugin_name} file inventory drift"
        for relative in source_files:
            assert (source / relative).read_bytes() == (
                packaged / relative
            ).read_bytes(), f"{plugin_name}/{relative} content drift"

    print("catalog-backed eight-plugin marketplace packaging: PASS")


if __name__ == "__main__":
    main()

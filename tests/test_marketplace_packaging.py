#!/usr/bin/env python3
"""Validate independently installable plugins in both marketplaces."""

from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PLUGINS = ROOT / "plugins"
SOURCE_SKILLS = ROOT / "skills"
EXPECTED_PLUGINS = {
    "advisor-review",
    "quant-stock-technical",
    "stock-scenario-story",
}
EXPECTED_VERSIONS = {
    "advisor-review": "0.1.1",
    "quant-stock-technical": "0.1.0",
    "stock-scenario-story": "0.1.0",
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
    codex_market = load_json(ROOT / ".agents/plugins/marketplace.json")
    claude_market = load_json(ROOT / ".claude-plugin/marketplace.json")
    assert codex_market["name"] == claude_market["name"] == "mrcha-skills"

    codex_entries = {entry["name"]: entry for entry in codex_market["plugins"]}
    claude_entries = {entry["name"]: entry for entry in claude_market["plugins"]}
    assert set(codex_entries) == EXPECTED_PLUGINS
    assert set(claude_entries) == EXPECTED_PLUGINS
    assert len(codex_market["plugins"]) == len(claude_market["plugins"]) == 3
    assert not (PLUGINS / "mrcha-skills").exists(), "aggregate plugin must not remain installable"

    for plugin_name in EXPECTED_PLUGINS:
        plugin = PLUGINS / plugin_name
        codex_plugin = load_json(plugin / ".codex-plugin/plugin.json")
        claude_plugin = load_json(plugin / ".claude-plugin/plugin.json")
        assert codex_entries[plugin_name]["source"]["path"] == f"./plugins/{plugin_name}"
        assert claude_entries[plugin_name]["source"] == f"./plugins/{plugin_name}"
        assert codex_entries[plugin_name]["policy"] == {
            "installation": "AVAILABLE",
            "authentication": "ON_INSTALL",
        }
        assert codex_plugin["name"] == claude_plugin["name"] == plugin_name
        assert (
            codex_plugin["version"]
            == claude_plugin["version"]
            == EXPECTED_VERSIONS[plugin_name]
        )
        assert claude_entries[plugin_name]["version"] == EXPECTED_VERSIONS[plugin_name]
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
            assert (source / relative).read_bytes() == (packaged / relative).read_bytes(), (
                f"{plugin_name}/{relative} content drift"
            )

    print("three-plugin dual marketplace packaging: PASS")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Validate the dual-marketplace bundle and its copied skill payload."""

from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "plugins/mrcha-skills"
SOURCE_SKILLS = ROOT / "skills"
PACKAGED_SKILLS = PLUGIN / "skills"
EXPECTED_SKILLS = {
    "advisor-review",
    "quant-stock-technical",
    "stock-scenario-story",
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
    codex_plugin = load_json(PLUGIN / ".codex-plugin/plugin.json")
    claude_plugin = load_json(PLUGIN / ".claude-plugin/plugin.json")

    assert codex_market["name"] == claude_market["name"] == "mrcha-skills"
    assert codex_market["plugins"][0]["name"] == "mrcha-skills"
    assert claude_market["plugins"][0]["name"] == "mrcha-skills"
    assert codex_market["plugins"][0]["source"]["path"] == "./plugins/mrcha-skills"
    assert claude_market["plugins"][0]["source"] == "./plugins/mrcha-skills"
    assert codex_plugin["name"] == claude_plugin["name"] == "mrcha-skills"
    assert codex_plugin["version"] == claude_plugin["version"] == "0.1.2"
    assert codex_plugin["skills"] == claude_plugin["skills"] == "./skills/"

    packaged_names = {path.name for path in PACKAGED_SKILLS.iterdir() if path.is_dir()}
    assert packaged_names == EXPECTED_SKILLS

    for skill_name in EXPECTED_SKILLS:
        source = SOURCE_SKILLS / skill_name
        packaged = PACKAGED_SKILLS / skill_name
        assert not packaged.is_symlink(), f"{packaged} must be self-contained"
        source_files = relative_files(source)
        packaged_files = relative_files(packaged)
        assert source_files == packaged_files, f"{skill_name} file inventory drift"
        for relative in source_files:
            assert (source / relative).read_bytes() == (packaged / relative).read_bytes(), (
                f"{skill_name}/{relative} content drift"
            )

    print("dual marketplace packaging: PASS")


if __name__ == "__main__":
    main()

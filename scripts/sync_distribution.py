#!/usr/bin/env python3
"""Synchronize generated plugin copies and versions from release/catalog.json."""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path
from typing import Any

from build_release_packages import (
    DEFAULT_CATALOG,
    ROOT,
    PackagingError,
    load_catalog,
    load_json,
    trees_equal,
)


def write_json_if_changed(path: Path, value: dict[str, Any]) -> bool:
    new_text = json.dumps(value, ensure_ascii=False, indent=2) + "\n"
    if path.read_text(encoding="utf-8") == new_text:
        return False
    path.write_text(new_text, encoding="utf-8")
    return True


def sync_tree(source: Path, target: Path) -> bool:
    if target.exists() and trees_equal(source, target):
        return False
    if target.exists():
        if target.is_symlink() or not target.is_dir():
            raise PackagingError(f"refusing to replace non-directory target: {target}")
        shutil.rmtree(target)
    shutil.copytree(source, target, copy_function=shutil.copy2)
    return True


def selected_entries(
    catalog: dict[str, Any], requested: list[str] | None
) -> list[dict[str, Any]]:
    entries = {entry["name"]: entry for entry in catalog["skills"]}
    if not requested:
        return [entries[name] for name in sorted(entries)]
    unknown = set(requested) - set(entries)
    if unknown:
        raise PackagingError(f"unknown release skill(s): {sorted(unknown)}")
    return [entries[name] for name in sorted(set(requested))]


def check_entry(
    entry: dict[str, Any],
    claude_entries: dict[str, dict[str, Any]],
) -> list[str]:
    name = entry["name"]
    version = entry["version"]
    plugin = ROOT / "plugins" / name
    errors: list[str] = []
    for label, path in (
        ("Codex", plugin / ".codex-plugin" / "plugin.json"),
        ("Claude", plugin / ".claude-plugin" / "plugin.json"),
    ):
        manifest = load_json(path)
        if manifest.get("name") != name:
            errors.append(f"{name}: {label} manifest name drift")
        if manifest.get("version") != version:
            errors.append(f"{name}: {label} manifest version drift")
    if claude_entries[name].get("version") != version:
        errors.append(f"{name}: Claude marketplace version drift")
    if not trees_equal(ROOT / "skills" / name, plugin / "skills" / name):
        errors.append(f"{name}: plugin skill copy drift")
    return errors


def write_entry(
    entry: dict[str, Any],
    claude_entries: dict[str, dict[str, Any]],
) -> list[str]:
    name = entry["name"]
    version = entry["version"]
    plugin = ROOT / "plugins" / name
    changed: list[str] = []
    for path in (
        plugin / ".codex-plugin" / "plugin.json",
        plugin / ".claude-plugin" / "plugin.json",
    ):
        manifest = load_json(path)
        if manifest.get("name") != name:
            raise PackagingError(f"{path} name must be {name!r}")
        manifest["version"] = version
        if write_json_if_changed(path, manifest):
            changed.append(str(path.relative_to(ROOT)))
    claude_entries[name]["version"] = version
    target = plugin / "skills" / name
    if sync_tree(ROOT / "skills" / name, target):
        changed.append(str(target.relative_to(ROOT)))
    return changed


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument(
        "--check",
        action="store_true",
        help="report drift without changing files",
    )
    mode.add_argument(
        "--write",
        action="store_true",
        help="update generated copies and manifest versions",
    )
    parser.add_argument(
        "--catalog",
        type=Path,
        default=DEFAULT_CATALOG,
        help="release catalog path",
    )
    parser.add_argument(
        "--skill",
        action="append",
        help="limit synchronization to one skill; repeat to select more",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    try:
        catalog = load_catalog(args.catalog)
        entries = selected_entries(catalog, args.skill)
        claude_market_path = ROOT / ".claude-plugin" / "marketplace.json"
        claude_market = load_json(claude_market_path)
        claude_entries = {
            entry["name"]: entry for entry in claude_market.get("plugins", [])
        }
        missing = {entry["name"] for entry in entries} - set(claude_entries)
        if missing:
            raise PackagingError(
                f"Claude marketplace is missing entries: {sorted(missing)}"
            )

        if args.check:
            errors = [
                error
                for entry in entries
                for error in check_entry(entry, claude_entries)
            ]
            if errors:
                for error in errors:
                    print(error, file=sys.stderr)
                print("distribution sync: FAIL", file=sys.stderr)
                return 1
            print(f"distribution sync: PASS ({len(entries)} skill(s))")
            return 0

        before_market = json.dumps(claude_market, ensure_ascii=False, sort_keys=True)
        changed = [
            item
            for entry in entries
            for item in write_entry(entry, claude_entries)
        ]
        after_market = json.dumps(claude_market, ensure_ascii=False, sort_keys=True)
        if before_market != after_market and write_json_if_changed(
            claude_market_path, claude_market
        ):
            changed.append(str(claude_market_path.relative_to(ROOT)))
        for path in changed:
            print(f"updated {path}")
        if not changed:
            print("distribution sync: already current")
        print(f"distribution sync: PASS ({len(entries)} skill(s))")
        return 0
    except (PackagingError, OSError) as exc:
        print(f"distribution sync: FAIL: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

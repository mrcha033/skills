#!/usr/bin/env python3
"""Validate the npm package surface used by OpenCode."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    package = json.loads((ROOT / "package.json").read_text(encoding="utf-8"))
    assert package["name"] == "mrcha-skills"
    assert package["version"] == json.loads(
        (ROOT / "release/catalog.json").read_text(encoding="utf-8")
    )["distributionVersion"]
    assert package["main"] == package["exports"] == "./index.js"
    assert "files" not in package

    result = subprocess.run(
        ["npm", "pack", "--dry-run", "--json", "--ignore-scripts"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    report = json.loads(result.stdout)
    assert set(report) == {package["name"]}
    files = {entry["path"] for entry in report[package["name"]]["files"]}
    assert {"package.json", "index.js", "README.md"} <= files
    for skill in (ROOT / "skills").iterdir():
        if skill.is_dir():
            assert f"skills/{skill.name}/SKILL.md" in files
    assert not any(filename.startswith("plugins/") for filename in files)
    assert not any("__pycache__" in filename or filename.endswith(".pyc") for filename in files)

    subprocess.run(
        [
            "node",
            "--input-type=module",
            "-e",
            "const plugin = (await import('./index.js')).default; "
            "const config = {}; "
            "const hooks = await plugin(); "
            "hooks.config(config); "
            "if (config.skills.paths.length !== 1 || "
            "!config.skills.paths[0].endsWith('/skills')) process.exit(1);",
        ],
        cwd=ROOT,
        check=True,
    )

    print("OpenCode npm package surface: PASS")


if __name__ == "__main__":
    main()

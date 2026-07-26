#!/usr/bin/env python3
"""Validate deterministic multi-surface release assets."""

from __future__ import annotations

import hashlib
import json
import sys
import tempfile
import zipfile
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from build_release_packages import (  # noqa: E402
    FIXED_ZIP_TIME,
    PackagingError,
    build_packages,
    load_catalog,
    relative_files,
    select_skills,
    validate_distribution,
)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def archive_files(path: Path) -> dict[str, tuple[bytes, int]]:
    with zipfile.ZipFile(path) as archive:
        result: dict[str, tuple[bytes, int]] = {}
        for info in archive.infolist():
            pure = PurePosixPath(info.filename)
            assert not pure.is_absolute()
            assert ".." not in pure.parts
            assert info.date_time == FIXED_ZIP_TIME
            assert info.compress_type == zipfile.ZIP_STORED
            assert not info.is_dir()
            mode = (info.external_attr >> 16) & 0o777
            assert mode in {0o644, 0o755}
            result[info.filename] = (archive.read(info), mode)
        return result


def expected_tree(root: Path, prefix: str = "") -> dict[str, tuple[bytes, int]]:
    return {
        (PurePosixPath(prefix) / relative.as_posix()).as_posix(): (
            (root / relative).read_bytes(),
            0o755 if (root / relative).stat().st_mode & 0o111 else 0o644,
        )
        for relative in relative_files(root)
    }


def main() -> None:
    catalog = load_catalog()
    validate_distribution(catalog)
    entries = select_skills(catalog, requested=None, tag=None)
    expected_tag = (
        f"{catalog['releaseTagPrefix']}{catalog['distributionVersion']}"
    )
    assert select_skills(catalog, requested=None, tag=expected_tag) == entries
    try:
        select_skills(catalog, requested=None, tag="skills-v999.0.0")
    except PackagingError:
        pass
    else:
        raise AssertionError("mismatched release tag must fail")

    with tempfile.TemporaryDirectory() as temporary:
        base = Path(temporary)
        first = base / "first"
        second = base / "second"
        stale = base / "stale"
        stale.mkdir()
        (stale / "old-package.zip").write_bytes(b"stale")
        try:
            build_packages(catalog, entries, stale)
        except PackagingError:
            pass
        else:
            raise AssertionError("non-empty output directory must fail")
        build_packages(catalog, entries, first)
        build_packages(catalog, entries, second)

        first_files = {
            path.name: path.read_bytes()
            for path in first.iterdir()
            if path.is_file()
        }
        second_files = {
            path.name: path.read_bytes()
            for path in second.iterdir()
            if path.is_file()
        }
        assert first_files == second_files, "release output is not deterministic"
        assert len(first_files) == len(entries) * 3 + 2

        release_manifest = json.loads(
            (first / "release-manifest.json").read_text(encoding="utf-8")
        )
        assert release_manifest["distributionVersion"] == catalog[
            "distributionVersion"
        ]
        assert release_manifest["releaseTag"] == expected_tag
        manifest_entries = {
            entry["name"]: entry for entry in release_manifest["packages"]
        }
        assert set(manifest_entries) == {entry["name"] for entry in entries}

        for entry in entries:
            name = entry["name"]
            version = entry["version"]
            standalone_zip = first / f"{name}-{version}.zip"
            standalone_skill = first / f"{name}-{version}.skill"
            plugin_zip = first / f"{name}-plugin-{version}.zip"
            assert standalone_zip.read_bytes() == standalone_skill.read_bytes()
            assert archive_files(standalone_zip) == expected_tree(
                ROOT / "skills" / name, name
            )
            plugin_contents = archive_files(plugin_zip)
            assert plugin_contents == expected_tree(ROOT / "plugins" / name)
            assert ".codex-plugin/plugin.json" in plugin_contents
            assert ".claude-plugin/plugin.json" in plugin_contents
            assert f"skills/{name}/SKILL.md" in plugin_contents

            manifest_entry = manifest_entries[name]
            assert manifest_entry["version"] == version
            assert manifest_entry["runtime"] == entry["runtime"]
            artifacts = manifest_entry["artifacts"]
            for format_name, path in (
                ("standaloneZip", standalone_zip),
                ("standaloneSkill", standalone_skill),
                ("pluginZip", plugin_zip),
            ):
                assert artifacts[format_name] == {
                    "file": path.name,
                    "bytes": path.stat().st_size,
                    "sha256": sha256(path),
                }

        checksums = {}
        for line in (first / "SHA256SUMS").read_text(encoding="utf-8").splitlines():
            digest, filename = line.split("  ", 1)
            checksums[filename] = digest
        checksum_targets = set(first_files) - {"SHA256SUMS"}
        assert set(checksums) == checksum_targets
        for filename, digest in checksums.items():
            assert digest == sha256(first / filename)

    print(
        f"deterministic release packaging: PASS "
        f"({len(entries)} skills, {len(entries) * 3} archives)"
    )


if __name__ == "__main__":
    main()

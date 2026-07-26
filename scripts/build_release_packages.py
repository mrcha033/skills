#!/usr/bin/env python3
"""Build deterministic standalone skill and dual-plugin release archives."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import stat
import sys
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CATALOG = ROOT / "release" / "catalog.json"
SEMVER = re.compile(
    r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)"
    r"(?:-([0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?"
    r"(?:\+([0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?$"
)
SAFE_NAME = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
RUNTIME_TARGETS = {"codex", "claudeCode", "chatgptWeb", "claudeWeb"}
RUNTIME_STATUSES = {"supported", "conditional", "unsupported"}
FIXED_ZIP_TIME = (1980, 1, 1, 0, 0, 0)
JUNK_NAMES = {".DS_Store"}
JUNK_PARTS = {"__pycache__"}
JUNK_SUFFIXES = {".pyc", ".pyo"}


class PackagingError(RuntimeError):
    """Raised when the catalog or package tree violates the release contract."""


def load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PackagingError(f"cannot read JSON object {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise PackagingError(f"{path} must contain a JSON object")
    return value


def load_catalog(path: Path = DEFAULT_CATALOG) -> dict[str, Any]:
    catalog = load_json(path)
    if catalog.get("schemaVersion") != 1:
        raise PackagingError("release catalog schemaVersion must be 1")
    distribution_version = catalog.get("distributionVersion")
    if not isinstance(distribution_version, str) or not SEMVER.fullmatch(
        distribution_version
    ):
        raise PackagingError("distributionVersion must be valid SemVer")
    prefix = catalog.get("releaseTagPrefix")
    if not isinstance(prefix, str) or not prefix or "/" in prefix:
        raise PackagingError("releaseTagPrefix must be a non-empty tag-safe string")

    formats = catalog.get("formats")
    required_formats = {"standaloneZip", "standaloneSkill", "pluginZip"}
    if not isinstance(formats, dict) or set(formats) != required_formats:
        raise PackagingError(
            f"formats must contain exactly {sorted(required_formats)}"
        )
    for format_name, value in formats.items():
        if not isinstance(value, dict) or not isinstance(value.get("filename"), str):
            raise PackagingError(f"{format_name}.filename must be a string")
        template = value["filename"]
        if "{name}" not in template or "{version}" not in template:
            raise PackagingError(
                f"{format_name}.filename must contain {{name}} and {{version}}"
            )

    skills = catalog.get("skills")
    excluded = catalog.get("excludedSkills")
    if not isinstance(skills, list) or not skills:
        raise PackagingError("skills must be a non-empty array")
    if not isinstance(excluded, list):
        raise PackagingError("excludedSkills must be an array")

    release_names: set[str] = set()
    for entry in skills:
        if not isinstance(entry, dict):
            raise PackagingError("each skills entry must be an object")
        name = entry.get("name")
        version = entry.get("version")
        if not isinstance(name, str) or not SAFE_NAME.fullmatch(name):
            raise PackagingError(f"invalid skill name: {name!r}")
        if name in release_names:
            raise PackagingError(f"duplicate release skill: {name}")
        release_names.add(name)
        if not isinstance(version, str) or not SEMVER.fullmatch(version):
            raise PackagingError(f"{name} version must be valid SemVer")
        runtime = entry.get("runtime")
        if not isinstance(runtime, dict) or set(runtime) != RUNTIME_TARGETS:
            raise PackagingError(
                f"{name} runtime must contain exactly {sorted(RUNTIME_TARGETS)}"
            )
        for target, status_value in runtime.items():
            if status_value not in RUNTIME_STATUSES:
                raise PackagingError(
                    f"{name} runtime.{target} has invalid status {status_value!r}"
                )
        requirements = entry.get("requirements")
        if not isinstance(requirements, list) or not all(
            isinstance(item, str) and item for item in requirements
        ):
            raise PackagingError(f"{name} requirements must be non-empty strings")

    excluded_names: set[str] = set()
    for entry in excluded:
        if not isinstance(entry, dict):
            raise PackagingError("each excludedSkills entry must be an object")
        name = entry.get("name")
        reason = entry.get("reason")
        if not isinstance(name, str) or not SAFE_NAME.fullmatch(name):
            raise PackagingError(f"invalid excluded skill name: {name!r}")
        if not isinstance(reason, str) or not reason:
            raise PackagingError(f"{name} exclusion must have a reason")
        if name in release_names or name in excluded_names:
            raise PackagingError(f"duplicate catalog skill: {name}")
        excluded_names.add(name)

    source_names = {
        path.name
        for path in (ROOT / "skills").iterdir()
        if path.is_dir() and not path.is_symlink()
    }
    catalog_names = release_names | excluded_names
    if source_names != catalog_names:
        raise PackagingError(
            "catalog/source skill mismatch: "
            f"missing={sorted(source_names - catalog_names)}, "
            f"unknown={sorted(catalog_names - source_names)}"
        )
    plugin_names = {
        path.name
        for path in (ROOT / "plugins").iterdir()
        if path.is_dir() and not path.is_symlink()
    }
    if plugin_names != release_names:
        raise PackagingError(
            "release/plugin mismatch: "
            f"missing={sorted(release_names - plugin_names)}, "
            f"unknown={sorted(plugin_names - release_names)}"
        )
    return catalog


def is_junk(path: Path) -> bool:
    return (
        path.name in JUNK_NAMES
        or any(part in JUNK_PARTS for part in path.parts)
        or path.suffix in JUNK_SUFFIXES
    )


def relative_files(root: Path) -> list[Path]:
    if not root.is_dir() or root.is_symlink():
        raise PackagingError(f"package root must be a real directory: {root}")
    files: list[Path] = []
    for path in root.rglob("*"):
        if path.is_symlink():
            raise PackagingError(f"symlinks are not allowed in release packages: {path}")
        if path.is_file() and not is_junk(path.relative_to(root)):
            files.append(path.relative_to(root))
    return sorted(files, key=lambda item: item.as_posix())


def trees_equal(left: Path, right: Path) -> bool:
    left_files = relative_files(left)
    right_files = relative_files(right)
    return left_files == right_files and all(
        (left / relative).read_bytes() == (right / relative).read_bytes()
        and bool((left / relative).stat().st_mode & 0o111)
        == bool((right / relative).stat().st_mode & 0o111)
        for relative in left_files
    )


def validate_distribution(catalog: dict[str, Any]) -> None:
    skills = {entry["name"]: entry for entry in catalog["skills"]}
    codex_market = load_json(ROOT / ".agents" / "plugins" / "marketplace.json")
    claude_market = load_json(ROOT / ".claude-plugin" / "marketplace.json")
    codex_entries = {
        entry["name"]: entry for entry in codex_market.get("plugins", [])
    }
    claude_entries = {
        entry["name"]: entry for entry in claude_market.get("plugins", [])
    }
    if set(codex_entries) != set(skills) or set(claude_entries) != set(skills):
        raise PackagingError("marketplace entries must match release catalog skills")

    for name, entry in skills.items():
        version = entry["version"]
        plugin = ROOT / "plugins" / name
        codex_manifest = load_json(plugin / ".codex-plugin" / "plugin.json")
        claude_manifest = load_json(plugin / ".claude-plugin" / "plugin.json")
        for label, manifest in (
            ("Codex", codex_manifest),
            ("Claude", claude_manifest),
        ):
            if manifest.get("name") != name:
                raise PackagingError(f"{name} {label} manifest name drift")
            if manifest.get("version") != version:
                raise PackagingError(f"{name} {label} manifest version drift")
            if manifest.get("skills") != "./skills/":
                raise PackagingError(f"{name} {label} manifest skills path drift")
        if claude_entries[name].get("version") != version:
            raise PackagingError(f"{name} Claude marketplace version drift")
        if claude_entries[name].get("source") != f"./plugins/{name}":
            raise PackagingError(f"{name} Claude marketplace source drift")
        codex_source = codex_entries[name].get("source")
        if codex_source != {"source": "local", "path": f"./plugins/{name}"}:
            raise PackagingError(f"{name} Codex marketplace source drift")
        source = ROOT / "skills" / name
        packaged = plugin / "skills" / name
        if not trees_equal(source, packaged):
            raise PackagingError(
                f"{name} plugin skill drift; run sync_distribution.py --write"
            )


def archive_name(template: str, name: str, version: str) -> str:
    result = template.format(name=name, version=version)
    if Path(result).name != result or result in {"", ".", ".."}:
        raise PackagingError(f"unsafe artifact filename: {result!r}")
    return result


def zip_info(archive_path: str, executable: bool) -> zipfile.ZipInfo:
    pure = PurePosixPath(archive_path)
    if pure.is_absolute() or ".." in pure.parts or not pure.parts:
        raise PackagingError(f"unsafe archive member: {archive_path!r}")
    info = zipfile.ZipInfo(pure.as_posix(), date_time=FIXED_ZIP_TIME)
    info.create_system = 3
    mode = 0o755 if executable else 0o644
    info.external_attr = (stat.S_IFREG | mode) << 16
    info.compress_type = zipfile.ZIP_STORED
    return info


def write_archive(destination: Path, source: Path, prefix: PurePosixPath) -> None:
    with zipfile.ZipFile(
        destination, mode="w", compression=zipfile.ZIP_STORED
    ) as archive:
        for relative in relative_files(source):
            path = source / relative
            member = prefix / PurePosixPath(relative.as_posix())
            executable = bool(path.stat().st_mode & 0o111)
            archive.writestr(zip_info(member.as_posix(), executable), path.read_bytes())


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def artifact_record(path: Path) -> dict[str, Any]:
    return {
        "file": path.name,
        "bytes": path.stat().st_size,
        "sha256": sha256(path),
    }


def select_skills(
    catalog: dict[str, Any], requested: list[str] | None, tag: str | None
) -> list[dict[str, Any]]:
    entries = {entry["name"]: entry for entry in catalog["skills"]}
    if tag is not None:
        expected = (
            f"{catalog['releaseTagPrefix']}{catalog['distributionVersion']}"
        )
        if tag != expected:
            raise PackagingError(
                f"release tag {tag!r} must exactly match catalog tag {expected!r}"
            )
        if requested:
            raise PackagingError("--tag and --skill cannot be combined")
        return [entries[name] for name in sorted(entries)]
    if not requested:
        return [entries[name] for name in sorted(entries)]
    unknown = set(requested) - set(entries)
    if unknown:
        raise PackagingError(f"unknown release skill(s): {sorted(unknown)}")
    return [entries[name] for name in sorted(set(requested))]


def build_packages(
    catalog: dict[str, Any], entries: list[dict[str, Any]], output: Path
) -> list[Path]:
    if output.exists() and not output.is_dir():
        raise PackagingError(f"output path is not a directory: {output}")
    if output.exists() and any(output.iterdir()):
        raise PackagingError(
            f"output directory must be empty to prevent stale assets: {output}"
        )
    output.mkdir(parents=True, exist_ok=True)
    built: list[Path] = []
    manifest_packages: list[dict[str, Any]] = []
    formats = catalog["formats"]

    for entry in entries:
        name = entry["name"]
        version = entry["version"]
        standalone_zip = output / archive_name(
            formats["standaloneZip"]["filename"], name, version
        )
        standalone_skill = output / archive_name(
            formats["standaloneSkill"]["filename"], name, version
        )
        plugin_zip = output / archive_name(
            formats["pluginZip"]["filename"], name, version
        )
        write_archive(
            standalone_zip, ROOT / "skills" / name, PurePosixPath(name)
        )
        standalone_skill.write_bytes(standalone_zip.read_bytes())
        write_archive(plugin_zip, ROOT / "plugins" / name, PurePosixPath())
        package_artifacts = {
            "standaloneZip": artifact_record(standalone_zip),
            "standaloneSkill": artifact_record(standalone_skill),
            "pluginZip": artifact_record(plugin_zip),
        }
        if (
            package_artifacts["standaloneZip"]["sha256"]
            != package_artifacts["standaloneSkill"]["sha256"]
        ):
            raise PackagingError(f"{name} .zip and .skill payloads differ")
        built.extend((standalone_zip, standalone_skill, plugin_zip))
        manifest_packages.append(
            {
                "name": name,
                "version": version,
                "runtime": entry["runtime"],
                "requirements": entry["requirements"],
                "notes": entry["notes"],
                "artifacts": package_artifacts,
            }
        )

    release_manifest = output / "release-manifest.json"
    release_manifest.write_text(
        json.dumps(
            {
                "schemaVersion": 1,
                "distributionVersion": catalog["distributionVersion"],
                "releaseTag": (
                    f"{catalog['releaseTagPrefix']}"
                    f"{catalog['distributionVersion']}"
                ),
                "guaranteeScope": catalog["guaranteeScope"],
                "packages": manifest_packages,
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    checksum_targets = sorted([*built, release_manifest], key=lambda path: path.name)
    checksum_file = output / "SHA256SUMS"
    checksum_file.write_text(
        "".join(f"{sha256(path)}  {path.name}\n" for path in checksum_targets),
        encoding="utf-8",
    )
    built.extend((release_manifest, checksum_file))
    return built


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--catalog",
        type=Path,
        default=DEFAULT_CATALOG,
        help="release catalog path",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=ROOT / "dist",
        help="directory for generated release assets",
    )
    parser.add_argument(
        "--skill",
        action="append",
        help="build one named release skill; repeat to select more",
    )
    parser.add_argument(
        "--tag",
        help="validate an aggregate release tag and build every release skill",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    try:
        catalog = load_catalog(args.catalog)
        validate_distribution(catalog)
        entries = select_skills(catalog, args.skill, args.tag)
        built = build_packages(catalog, entries, args.output_dir)
    except PackagingError as exc:
        print(f"release packaging: FAIL: {exc}", file=sys.stderr)
        return 1
    for path in sorted(built, key=lambda item: item.name):
        print(f"{sha256(path)}  {path}")
    print(
        f"release packaging: PASS ({len(entries)} skill(s), "
        f"distribution {catalog['distributionVersion']})"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

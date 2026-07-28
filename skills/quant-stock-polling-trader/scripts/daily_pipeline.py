#!/usr/bin/env python3
"""Run the deterministic daily pre-open, snapshot, and shadow-entry pipeline."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sqlite3
import stat
import subprocess
import sys
import tempfile
import time
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import market_calendar
from execution_core import (
    ENTRY_WINDOWS,
    TERMINAL_STATES,
    BlockedError,
    canonical_json,
    normalized_execution_policy,
    normalized_risk_policy,
    sha256_file,
)

CONFIG_SCHEMA = "qta-daily-shadow-config/v1"
RECEIPT_SCHEMA = "qta-daily-shadow-receipt/v1"
PROVENANCE_SCHEMA = "qta-runtime-provenance/v1"
CONFIG_FIELDS = {
    "schema",
    "runtime_root",
    "broker",
    "mode",
    "history_start_date",
    "minimum_sessions",
    "request_interval_ms",
    "catalog_coverage_contract",
    "selector",
    "risk_policy_path",
    "execution_policy",
    "manual_exposure_component_paths",
    "required_manual_exposure_brokers",
    "manual_component_max_age_seconds",
    "account_aliases",
    "prepare_complete_seconds_before_open",
    "approved_technical_version",
    "approved_technical_tree_sha256",
    "approved_trader_version",
    "approved_trader_tree_sha256",
}
EXECUTION_DEFAULT_FIELDS = {
    "snapshot_max_age_seconds",
    "poll_interval_seconds",
    "quote_max_age_seconds",
    "max_spread_bps",
    "max_gap_bps",
    "trigger_mode",
    "order_ttl_seconds",
    "order_type",
    "time_in_force",
    "allow_partial_fill",
    "cancel_remainder_at_window_end",
}
TERMINAL_OR_MANUAL = TERMINAL_STATES
RECONCILE_ATTEMPTS = 20
RECONCILE_INTERVAL_SECONDS = 15


class PipelineBlockedError(BlockedError):
    """Raised when a daily gate cannot be completed without guessing."""


def atomic_write(path: Path, payload: bytes, mode: int = 0o600) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent)
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        os.chmod(path, mode)
    finally:
        if temporary.exists():
            temporary.unlink()


def atomic_write_json(path: Path, value: Any) -> None:
    atomic_write(
        path,
        (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode(
            "utf-8"
        ),
    )


def load_json(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PipelineBlockedError(f"{label} must be readable JSON: {path}") from exc
    if not isinstance(value, dict):
        raise PipelineBlockedError(f"{label} must be one JSON object")
    return value


def exact_fields(value: dict[str, Any], expected: set[str], label: str) -> None:
    if set(value) != expected:
        raise PipelineBlockedError(
            f"{label} fields mismatch; missing={sorted(expected - set(value))}, "
            f"extra={sorted(set(value) - expected)}"
        )


def absolute_path(value: Any, label: str) -> Path:
    path = Path(str(value)).expanduser()
    if not path.is_absolute():
        raise PipelineBlockedError(f"{label} must be absolute")
    if any(character in str(path) for character in ("\n", "\r", "\0")):
        raise PipelineBlockedError(f"{label} contains an invalid character")
    return path


def regular_nonsymlink(path: Path, label: str) -> None:
    try:
        metadata = path.lstat()
    except FileNotFoundError as exc:
        raise PipelineBlockedError(f"{label} does not exist: {path}") from exc
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
        raise PipelineBlockedError(f"{label} must be a regular non-symlink file")


def validate_sha256(value: Any, label: str) -> str:
    text = str(value)
    if len(text) != 64 or any(character not in "0123456789abcdef" for character in text):
        raise PipelineBlockedError(f"{label} must be a lowercase SHA-256")
    return text


def parse_iso_date(value: Any, label: str) -> date:
    if not isinstance(value, str):
        raise PipelineBlockedError(f"{label} must be YYYY-MM-DD")
    try:
        parsed = date.fromisoformat(value)
    except ValueError as exc:
        raise PipelineBlockedError(f"{label} must be YYYY-MM-DD") from exc
    if parsed.isoformat() != value:
        raise PipelineBlockedError(f"{label} must be YYYY-MM-DD")
    return parsed


def normalize_config(raw: Any, path: Path) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise PipelineBlockedError("daily config must be one JSON object")
    exact_fields(raw, CONFIG_FIELDS, "daily config")
    if raw["schema"] != CONFIG_SCHEMA:
        raise PipelineBlockedError(f"daily config.schema must be {CONFIG_SCHEMA}")
    runtime = absolute_path(raw["runtime_root"], "runtime_root")
    if runtime.exists() and (runtime.is_symlink() or not runtime.is_dir()):
        raise PipelineBlockedError("runtime_root must be a non-symlink directory")
    if raw["broker"] != "kis-live" or raw["mode"] != "shadow":
        raise PipelineBlockedError("daily pipeline requires kis-live/shadow")
    history_start = parse_iso_date(raw["history_start_date"], "history_start_date")
    minimum_sessions = raw["minimum_sessions"]
    if (
        isinstance(minimum_sessions, bool)
        or not isinstance(minimum_sessions, int)
        or minimum_sessions < 756
    ):
        raise PipelineBlockedError("minimum_sessions must be an integer >= 756")
    interval = raw["request_interval_ms"]
    if isinstance(interval, bool) or not isinstance(interval, int) or interval < 100:
        raise PipelineBlockedError("request_interval_ms must be an integer >= 100")
    coverage = raw["catalog_coverage_contract"]
    if not isinstance(coverage, dict):
        raise PipelineBlockedError("catalog_coverage_contract must be an object")
    selector = raw["selector"]
    if not isinstance(selector, dict) or selector.get("selector_version") != (
        "qta-screen-1.1.0"
    ):
        raise PipelineBlockedError("selector must use qta-screen-1.1.0")
    risk_path = absolute_path(raw["risk_policy_path"], "risk_policy_path")
    regular_nonsymlink(risk_path, "risk_policy_path")
    risk = normalized_risk_policy(load_json(risk_path, "risk policy"))
    for field in (
        "allow_existing_additions",
        "allow_borrowed_cash",
        "allow_margin",
        "allow_short",
        "allow_auto_fx",
    ):
        if risk[field] is not False:
            raise PipelineBlockedError(f"risk policy {field} must be false")
    if risk["whole_shares_only"] is not True:
        raise PipelineBlockedError("risk policy whole_shares_only must be true")
    execution = raw["execution_policy"]
    if not isinstance(execution, dict):
        raise PipelineBlockedError("execution_policy must be an object")
    exact_fields(execution, EXECUTION_DEFAULT_FIELDS, "execution_policy")
    component_paths = raw["manual_exposure_component_paths"]
    if not isinstance(component_paths, list):
        raise PipelineBlockedError(
            "manual_exposure_component_paths must be an array"
        )
    normalized_component_paths: list[str] = []
    for index, item in enumerate(component_paths):
        if not isinstance(item, str) or not item:
            raise PipelineBlockedError(
                f"manual_exposure_component_paths[{index}] must be a string"
            )
        text = item
        substituted = text.replace("{session_date}", "2000-01-01")
        absolute_path(
            substituted,
            f"manual_exposure_component_paths[{index}]",
        )
        if text.count("{session_date}") > 1 or "{" in text.replace(
            "{session_date}", ""
        ):
            raise PipelineBlockedError(
                f"manual_exposure_component_paths[{index}] has an unknown template"
            )
        normalized_component_paths.append(text)
    required_brokers = raw["required_manual_exposure_brokers"]
    if (
        not isinstance(required_brokers, list)
        or sorted(required_brokers) != ["nh", "toss"]
        or len(required_brokers) != 2
    ):
        raise PipelineBlockedError(
            "required_manual_exposure_brokers must be exactly toss and nh"
        )
    max_age = raw["manual_component_max_age_seconds"]
    if (
        isinstance(max_age, bool)
        or not isinstance(max_age, int)
        or max_age <= 0
        or max_age > 86400
    ):
        raise PipelineBlockedError(
            "manual_component_max_age_seconds must be 1..86400"
        )
    aliases = raw["account_aliases"]
    if not isinstance(aliases, dict) or set(aliases) != {"KR", "US"}:
        raise PipelineBlockedError("account_aliases must contain exactly KR and US")
    if any(
        not isinstance(value, str) or not value.strip() or value != value.strip()
        for value in aliases.values()
    ):
        raise PipelineBlockedError("account aliases must be non-empty trimmed strings")
    deadline = raw["prepare_complete_seconds_before_open"]
    if (
        isinstance(deadline, bool)
        or not isinstance(deadline, int)
        or deadline < 600
        or deadline > 7200
    ):
        raise PipelineBlockedError(
            "prepare_complete_seconds_before_open must be 600..7200"
        )
    versions = {
        "technical": str(raw["approved_technical_version"]),
        "trader": str(raw["approved_trader_version"]),
    }
    if any(not value or value != value.strip() for value in versions.values()):
        raise PipelineBlockedError("approved versions must be non-empty")
    return {
        **raw,
        "runtime_root": runtime.resolve(strict=False),
        "history_start_date": history_start.isoformat(),
        "risk_policy_path": risk_path.resolve(),
        "risk_policy": risk,
        "manual_exposure_component_paths": normalized_component_paths,
        "required_manual_exposure_brokers": sorted(required_brokers),
        "approved_technical_tree_sha256": validate_sha256(
            raw["approved_technical_tree_sha256"],
            "approved_technical_tree_sha256",
        ),
        "approved_trader_tree_sha256": validate_sha256(
            raw["approved_trader_tree_sha256"],
            "approved_trader_tree_sha256",
        ),
        "config_path": path.resolve(),
    }


def load_config(path: Path) -> dict[str, Any]:
    regular_nonsymlink(path, "daily config")
    return normalize_config(load_json(path, "daily config"), path)


def source_tree_digest(root: Path) -> tuple[str, list[dict[str, Any]]]:
    if root.is_symlink() or not root.is_dir():
        raise PipelineBlockedError(f"skill root must be a non-symlink directory: {root}")
    entries: list[tuple[bytes, str, str]] = []
    ignored: list[dict[str, Any]] = []
    for path in root.rglob("*"):
        relative = path.relative_to(root)
        relative_text = "./" + relative.as_posix()
        metadata = path.lstat()
        if stat.S_ISLNK(metadata.st_mode):
            raise PipelineBlockedError(f"skill tree contains symlink: {relative_text}")
        if stat.S_ISDIR(metadata.st_mode):
            continue
        if not stat.S_ISREG(metadata.st_mode):
            raise PipelineBlockedError(
                f"skill tree contains special file: {relative_text}"
            )
        in_pycache = "__pycache__" in relative.parts
        if in_pycache and path.suffix == ".pyc":
            ignored.append(
                {
                    "path": relative_text,
                    "bytes": metadata.st_size,
                    "sha256": sha256_file(path),
                    "classification": "IGNORED_DERIVED_ARTIFACT",
                }
            )
            continue
        if path.suffix in {".pyc", ".pyo"}:
            raise PipelineBlockedError(
                f"bytecode exists outside an allowed __pycache__: {relative_text}"
            )
        entries.append(
            (
                relative_text.encode("utf-8"),
                sha256_file(path),
                relative_text,
            )
        )
    entries.sort(key=lambda item: item[0])
    listing = "".join(f"{digest}  {relative}\n" for _, digest, relative in entries)
    return hashlib.sha256(listing.encode("utf-8")).hexdigest(), sorted(
        ignored, key=lambda item: item["path"].encode("utf-8")
    )


def raw_tree_digest(root: Path) -> str:
    if root.is_symlink() or not root.is_dir():
        raise PipelineBlockedError(f"skill root must be a non-symlink directory: {root}")
    entries: list[tuple[bytes, str, str]] = []
    for path in root.rglob("*"):
        relative_text = "./" + path.relative_to(root).as_posix()
        metadata = path.lstat()
        if stat.S_ISLNK(metadata.st_mode):
            raise PipelineBlockedError(f"skill tree contains symlink: {relative_text}")
        if stat.S_ISDIR(metadata.st_mode):
            continue
        if not stat.S_ISREG(metadata.st_mode):
            raise PipelineBlockedError(
                f"skill tree contains special file: {relative_text}"
            )
        entries.append(
            (
                relative_text.encode("utf-8"),
                sha256_file(path),
                relative_text,
            )
        )
    entries.sort(key=lambda item: item[0])
    listing = "".join(f"{digest}  {relative}\n" for _, digest, relative in entries)
    return hashlib.sha256(listing.encode("utf-8")).hexdigest()


def plugin_manifest(skill_root: Path) -> tuple[Path, dict[str, Any]]:
    for parent in (skill_root.parent.parent, skill_root.parent.parent.parent):
        candidate = parent / ".claude-plugin" / "plugin.json"
        if candidate.is_file() and not candidate.is_symlink():
            return candidate, load_json(candidate, "plugin manifest")
    raise PipelineBlockedError(f"plugin manifest not found for {skill_root}")


def verify_one_skill(
    skill_root: Path,
    *,
    expected_name: str,
    expected_version: str,
    expected_digest: str,
) -> dict[str, Any]:
    manifest_path, manifest = plugin_manifest(skill_root)
    if manifest.get("name") != expected_name:
        raise PipelineBlockedError(
            f"installed plugin name mismatch for {expected_name}"
        )
    if manifest.get("version") != expected_version:
        raise PipelineBlockedError(
            f"{expected_name} version {manifest.get('version')} does not match "
            f"approved {expected_version}"
        )
    raw_digest = raw_tree_digest(skill_root)
    digest, ignored = source_tree_digest(skill_root)
    if digest != expected_digest:
        raise PipelineBlockedError(
            f"{expected_name} source tree digest {digest} does not match "
            f"approved {expected_digest}"
        )
    required = [
        skill_root / "SKILL.md",
        manifest_path,
    ]
    if expected_name == "quant-stock-technical":
        required.extend(
            skill_root / "scripts" / name
            for name in (
                "fetch_kis_kr_eod.py",
                "fetch_kis_us_eod.py",
                "build_universe_manifest.py",
                "screen_universe.py",
            )
        )
    else:
        required.extend(
            skill_root / "scripts" / name
            for name in (
                "market_calendar.py",
                "daily_pipeline.py",
                "account_snapshot.py",
                "plan_orders.py",
                "run_session.py",
                "reconcile.py",
                "systemd_units.py",
            )
        )
    for path in required:
        regular_nonsymlink(path, f"{expected_name} required file")
    return {
        "name": expected_name,
        "version": expected_version,
        "raw_tree_digest": raw_digest,
        "source_tree_digest": digest,
        "manifest_path": str(manifest_path.resolve()),
        "ignored_derived_artifacts": ignored,
    }


def verify_provenance(
    config: dict[str, Any],
    technical_root: Path,
    trader_root: Path,
) -> dict[str, Any]:
    approved_root = (config["runtime_root"] / "approved").resolve(strict=False)
    for root, label in (
        (technical_root, "technical_skill_root"),
        (trader_root, "trader_skill_root"),
    ):
        try:
            root.resolve().relative_to(approved_root)
        except ValueError as exc:
            raise PipelineBlockedError(
                f"{label} must be inside the isolated runtime approved directory"
            ) from exc
    technical = verify_one_skill(
        technical_root,
        expected_name="quant-stock-technical",
        expected_version=config["approved_technical_version"],
        expected_digest=config["approved_technical_tree_sha256"],
    )
    trader = verify_one_skill(
        trader_root,
        expected_name="quant-stock-polling-trader",
        expected_version=config["approved_trader_version"],
        expected_digest=config["approved_trader_tree_sha256"],
    )
    if technical["ignored_derived_artifacts"] or trader[
        "ignored_derived_artifacts"
    ]:
        raise PipelineBlockedError(
            "isolated staged skill roots must not contain derived bytecode"
        )
    without_hash = {
        "schema": PROVENANCE_SCHEMA,
        "status": "PASS",
        "technical": technical,
        "trader": trader,
        "python_flags": "-B -s",
        "pythonpath_unset": True,
        "pythonhome_unset": True,
        "dont_write_bytecode": True,
        "live_enabled": False,
        "api_mutation_count": 0,
    }
    return {
        **without_hash,
        "receipt_hash": hashlib.sha256(
            canonical_json(without_hash).encode("utf-8")
        ).hexdigest(),
    }


def subprocess_environment() -> dict[str, str]:
    environment = dict(os.environ)
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    environment.pop("PYTHONPATH", None)
    environment.pop("PYTHONHOME", None)
    return environment


def run_command(command: list[str], label: str) -> None:
    result = subprocess.run(
        command,
        check=False,
        capture_output=True,
        text=True,
        env=subprocess_environment(),
    )
    if result.returncode != 0:
        reason = result.stderr.strip() or result.stdout.strip() or (
            f"exit status {result.returncode}"
        )
        if len(reason) > 2000:
            reason = reason[-2000:]
        raise PipelineBlockedError(f"{label} failed: {reason}")


def python_command(script: Path, *arguments: str) -> list[str]:
    regular_nonsymlink(script, "pipeline script")
    return [sys.executable, "-B", "-s", str(script), *arguments]


def workflow_directory(config: dict[str, Any], market: str, session: str) -> Path:
    return config["runtime_root"] / "workflows" / market.lower() / session


def current_descriptor_path(config: dict[str, Any], market: str) -> Path:
    return config["runtime_root"] / "current" / f"{market.lower()}.json"


def terminal_receipt(
    *,
    stage: str,
    status: str,
    market: str,
    session_date: str,
    reason: str = "",
    details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    without_hash = {
        "schema": RECEIPT_SCHEMA,
        "stage": stage,
        "status": status,
        "market": market,
        "session_date": session_date,
        "reason": reason,
        "details": details or {},
        "recorded_at": datetime.now(timezone.utc).isoformat(),
        "live_enabled": False,
        "api_mutation_count": 0,
    }
    return {
        **without_hash,
        "receipt_hash": hashlib.sha256(
            canonical_json(without_hash).encode("utf-8")
        ).hexdigest(),
    }


def current_market_date(market: str) -> str:
    return datetime.now(
        ZoneInfo(ENTRY_WINDOWS[market]["timezone"])
    ).date().isoformat()


def read_source_snapshot(path: Path, as_of: str, label: str) -> dict[str, Any]:
    receipt = load_json(path, label)
    if receipt.get("as_of") != as_of:
        raise PipelineBlockedError(f"{label} as_of mismatch")
    if not isinstance(receipt.get("official_sources"), list) or not isinstance(
        receipt.get("broker_sources"), list
    ):
        raise PipelineBlockedError(f"{label} source arrays are missing")
    return receipt


def ensure_source_snapshot(
    *,
    market: str,
    as_of: str,
    directory: Path,
    technical_root: Path,
) -> dict[str, Any]:
    receipt_path = directory / "source-snapshot.json"
    if receipt_path.is_file() and not receipt_path.is_symlink():
        return read_source_snapshot(receipt_path, as_of, f"{market} source snapshot")
    script = technical_root / "scripts" / (
        "fetch_kis_kr_eod.py" if market == "KR" else "fetch_kis_us_eod.py"
    )
    run_command(
        python_command(
            script,
            "snapshot-sources",
            "--as-of",
            as_of,
            "--output-directory",
            str(directory),
        ),
        f"{market} official source snapshot",
    )
    return read_source_snapshot(receipt_path, as_of, f"{market} source snapshot")


def copy_cache_tree(source: Path, target: Path) -> None:
    if not source.is_dir() or source.is_symlink() or target.exists():
        return

    def link_or_copy(from_path: str, to_path: str) -> str:
        try:
            os.link(from_path, to_path)
            return to_path
        except OSError:
            return shutil.copy2(from_path, to_path)

    shutil.copytree(source, target, copy_function=link_or_copy)


def seed_eod_cache(
    runtime_root: Path,
    target_root: Path,
    analysis_date: str,
    component: str,
) -> None:
    if target_root.exists() and any(target_root.iterdir()):
        return
    candidates: list[tuple[date, Path]] = []
    eod_root = runtime_root / "eod"
    if not eod_root.is_dir():
        return
    target_date = date.fromisoformat(analysis_date)
    for catalog in eod_root.rglob("eod-catalog.csv"):
        if catalog.parent.name != component:
            continue
        try:
            relative_parts = catalog.relative_to(eod_root).parts
            candidate_date = date.fromisoformat(relative_parts[0])
        except (ValueError, IndexError):
            continue
        candidate_root = catalog.parent
        if candidate_date > target_date or candidate_root.resolve() == target_root.resolve():
            continue
        receipt_path = candidate_root / "eod-bundle-receipt.json"
        if (
            receipt_path.is_symlink()
            or not receipt_path.is_file()
            or catalog.is_symlink()
        ):
            continue
        try:
            receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if (
            not isinstance(receipt, dict)
            or receipt.get("status") != "READY"
            or receipt.get("analysis_date") != candidate_date.isoformat()
            or receipt.get("api_mutation_count") != 0
        ):
            continue
        candidates.append((candidate_date, candidate_root))
    if not candidates:
        return
    source_root = max(candidates, key=lambda item: (item[0], str(item[1])))[1]
    for name in ("stocks", "benchmarks"):
        copy_cache_tree(source_root / name, target_root / name)


def ensure_eod(
    *,
    config: dict[str, Any],
    technical_root: Path,
    workflow: Path,
    as_of: str,
    analysis_date: str,
    kr_sources: dict[str, Any],
    us_sources: dict[str, Any],
    provenance_id: str,
) -> tuple[Path, Path]:
    eod_base = config["runtime_root"] / "eod" / analysis_date / provenance_id[:16]
    kr_output = eod_base / "kr"
    us_output = eod_base / "us"
    kr_receipt_path = kr_output / "eod-bundle-receipt.json"
    us_receipt_path = us_output / "eod-bundle-receipt.json"
    jobs_directory = workflow / "jobs"
    jobs_directory.mkdir(parents=True, exist_ok=True)

    if not kr_receipt_path.is_file():
        seed_eod_cache(config["runtime_root"], kr_output, analysis_date, "kr")
        kr_job = {
            "schema": "qta-kis-kr-eod-job/v1",
            "as_of": as_of,
            "analysis_date": analysis_date,
            "environment": "live",
            "output_directory": str(kr_output.resolve()),
            "history_start_date": config["history_start_date"],
            "minimum_sessions": config["minimum_sessions"],
            "request_interval_ms": config["request_interval_ms"],
            "official_sources": kr_sources["official_sources"],
            "broker_sources": kr_sources["broker_sources"],
            "catalog_coverage_contract": config["catalog_coverage_contract"],
            "base_eod_catalog": "",
        }
        kr_job_path = jobs_directory / "kr-eod.json"
        atomic_write_json(kr_job_path, kr_job)
        run_command(
            python_command(
                technical_root / "scripts" / "fetch_kis_kr_eod.py",
                "collect",
                "--job",
                str(kr_job_path),
            ),
            "KIS Korean adjusted EOD",
        )
    kr_receipt = load_json(kr_receipt_path, "Korean EOD receipt")
    if (
        kr_receipt.get("status") != "READY"
        or kr_receipt.get("analysis_date") != analysis_date
        or kr_receipt.get("api_mutation_count") != 0
    ):
        raise PipelineBlockedError("Korean EOD receipt is not READY and read-only")

    if not us_receipt_path.is_file():
        seed_eod_cache(config["runtime_root"], us_output, analysis_date, "us")
        us_job = {
            "schema": "qta-kis-us-eod-job/v1",
            "as_of": as_of,
            "analysis_date": analysis_date,
            "environment": "live",
            "output_directory": str(us_output.resolve()),
            "history_start_date": config["history_start_date"],
            "minimum_sessions": config["minimum_sessions"],
            "request_interval_ms": config["request_interval_ms"],
            "official_sources": (
                kr_sources["official_sources"] + us_sources["official_sources"]
            ),
            "broker_sources": (
                kr_sources["broker_sources"] + us_sources["broker_sources"]
            ),
            "catalog_coverage_contract": config["catalog_coverage_contract"],
            "base_eod_catalog": str((kr_output / "eod-catalog.csv").resolve()),
        }
        us_job_path = jobs_directory / "us-eod.json"
        atomic_write_json(us_job_path, us_job)
        run_command(
            python_command(
                technical_root / "scripts" / "fetch_kis_us_eod.py",
                "collect",
                "--job",
                str(us_job_path),
            ),
            "KIS U.S. adjusted EOD",
        )
    us_receipt = load_json(us_receipt_path, "U.S. EOD receipt")
    if (
        us_receipt.get("status") != "READY"
        or us_receipt.get("analysis_date") != analysis_date
        or us_receipt.get("api_mutation_count") != 0
    ):
        raise PipelineBlockedError("U.S. EOD receipt is not READY and read-only")
    return kr_output, us_output


def derive_universe_build_spec(
    *,
    config: dict[str, Any],
    us_output: Path,
    workflow: Path,
) -> Path:
    receipt_path = us_output / "eod-bundle-receipt.json"
    regular_nonsymlink(receipt_path, "U.S. EOD receipt")
    receipt = load_json(receipt_path, "U.S. EOD receipt")
    binding = receipt.get("build_spec")
    if not isinstance(binding, dict):
        raise PipelineBlockedError("U.S. EOD receipt build_spec must be an object")
    exact_fields(binding, {"path", "sha256"}, "U.S. EOD receipt build_spec")
    source_path = absolute_path(binding["path"], "U.S. EOD build spec path")
    expected_path = (us_output / "universe-build-spec.json").resolve()
    regular_nonsymlink(source_path, "U.S. EOD build spec")
    if source_path.resolve() != expected_path:
        raise PipelineBlockedError(
            "U.S. EOD receipt build_spec path does not match its bundle"
        )
    expected_sha256 = validate_sha256(
        binding["sha256"],
        "U.S. EOD receipt build_spec.sha256",
    )
    if sha256_file(source_path) != expected_sha256:
        raise PipelineBlockedError("U.S. EOD build spec hash mismatch")
    derived = load_json(source_path, "U.S. EOD build spec")
    derived["catalog_coverage_contract"] = config["catalog_coverage_contract"]
    output_path = workflow / "universe-build-spec.json"
    atomic_write_json(output_path, derived)
    return output_path


def prepare(
    config: dict[str, Any],
    market: str,
    technical_root: Path,
    trader_root: Path,
) -> dict[str, Any]:
    session = current_market_date(market)
    workflow = workflow_directory(config, market, session)
    workflow.mkdir(parents=True, exist_ok=True)
    current_path = current_descriptor_path(config, market)
    if current_path.is_file() and not current_path.is_symlink():
        current = load_json(current_path, "current daily descriptor")
        if current.get("market") == market and current.get("session_date") == session:
            if current.get("status") in {"BLOCKED", "MANUAL_BLOCK"}:
                raise PipelineBlockedError(
                    str(
                        current.get("blocked_reason")
                        or "current market session is terminally blocked"
                    )
                )
            if current.get("status") == "MARKET_CLOSED":
                return load_json(
                    workflow / "prepare-receipt.json",
                    "market-closed prepare receipt",
                )
            if current.get("status") in {
                "PREPARED",
                "ARMED_SHADOW",
                "READY",
            }:
                return load_json(
                    workflow / "prepare-receipt.json",
                    "existing prepare receipt",
                )
    provenance = verify_provenance(config, technical_root, trader_root)
    atomic_write_json(workflow / "provenance.json", provenance)
    calendar = market_calendar.snapshot(
        market=market,
        session_date=session,
        output_directory=workflow / "calendar",
    )
    if calendar["status"] == "MARKET_CLOSED":
        receipt = terminal_receipt(
            stage="prepare",
            status="MARKET_CLOSED",
            market=market,
            session_date=session,
            details={"calendar_receipt": calendar},
        )
        descriptor = {
            "schema": "qta-daily-shadow-descriptor/v1",
            "status": "MARKET_CLOSED",
            "market": market,
            "session_date": session,
            "workflow_directory": str(workflow.resolve()),
            "prepare_receipt": str((workflow / "prepare-receipt.json").resolve()),
            "live_enabled": False,
            "api_mutation_count": 0,
        }
        atomic_write_json(workflow / "prepare-receipt.json", receipt)
        atomic_write_json(current_path, descriptor)
        return receipt
    if calendar["status"] != "READY":
        raise PipelineBlockedError("calendar snapshot is neither READY nor closed")
    market_session_path = Path(calendar["market_session_path"])
    market_session = load_json(market_session_path, "market session")
    regular_open = datetime.fromisoformat(market_session["regular_open"])
    deadline = regular_open - timedelta(
        seconds=config["prepare_complete_seconds_before_open"]
    )
    if datetime.now(timezone.utc) >= deadline.astimezone(timezone.utc):
        raise PipelineBlockedError(
            "prepare started too late to complete before the account snapshot gate"
        )
    analysis_date = market_session["previous_session_date"]
    if parse_iso_date(config["history_start_date"], "history_start_date") >= (
        parse_iso_date(analysis_date, "analysis_date")
    ):
        raise PipelineBlockedError("history_start_date must precede analysis_date")
    provenance_id = hashlib.sha256(
        (
            provenance["technical"]["source_tree_digest"]
            + provenance["trader"]["source_tree_digest"]
        ).encode("ascii")
    ).hexdigest()
    source_root = (
        config["runtime_root"] / "sources" / session / provenance_id[:16]
    )
    kr_sources = ensure_source_snapshot(
        market="KR",
        as_of=session,
        directory=source_root / "kr",
        technical_root=technical_root,
    )
    us_sources = ensure_source_snapshot(
        market="US",
        as_of=session,
        directory=source_root / "us",
        technical_root=technical_root,
    )
    _, us_output = ensure_eod(
        config=config,
        technical_root=technical_root,
        workflow=workflow,
        as_of=session,
        analysis_date=analysis_date,
        kr_sources=kr_sources,
        us_sources=us_sources,
        provenance_id=provenance_id,
    )
    if datetime.now(timezone.utc) >= deadline.astimezone(timezone.utc):
        raise PipelineBlockedError(
            "EOD preparation finished after the account snapshot deadline"
        )
    build_spec = derive_universe_build_spec(
        config=config,
        us_output=us_output,
        workflow=workflow,
    )
    manifest_path = workflow / "universe-manifest.json"
    run_command(
        python_command(
            technical_root / "scripts" / "build_universe_manifest.py",
            "--build-spec",
            str(build_spec),
            "--output",
            str(manifest_path),
        ),
        "four-exchange universe manifest",
    )
    manifest = load_json(manifest_path, "universe manifest")
    if manifest.get("build_status") != "READY":
        raise PipelineBlockedError("universe manifest build_status is not READY")
    selector_path = workflow / "selector.json"
    atomic_write_json(selector_path, config["selector"])
    screen_path = workflow / "screen.json"
    run_command(
        python_command(
            technical_root / "scripts" / "screen_universe.py",
            "--manifest",
            str(manifest_path),
            "--selector",
            str(selector_path),
            "--output",
            str(screen_path),
        ),
        "four-exchange screen",
    )
    screen = load_json(screen_path, "screen")
    if screen.get("screen_status") != "READY":
        raise PipelineBlockedError("qta-screen/v2 is not READY")
    final_technical_digest, _ = source_tree_digest(technical_root)
    final_trader_digest, _ = source_tree_digest(trader_root)
    if final_technical_digest != provenance["technical"]["source_tree_digest"] or (
        final_trader_digest != provenance["trader"]["source_tree_digest"]
    ):
        raise PipelineBlockedError("installed skill source changed during prepare")
    receipt = terminal_receipt(
        stage="prepare",
        status="READY",
        market=market,
        session_date=session,
        details={
            "analysis_date": analysis_date,
            "manifest_hash": manifest["manifest_hash"],
            "screen_hash": screen["screen_hash"],
            "calendar_session_hash": market_session["session_hash"],
            "provenance_receipt_hash": provenance["receipt_hash"],
        },
    )
    descriptor = {
        "schema": "qta-daily-shadow-descriptor/v1",
        "status": "PREPARED",
        "market": market,
        "session_date": session,
        "analysis_date": analysis_date,
        "workflow_directory": str(workflow.resolve()),
        "market_session_path": str(market_session_path.resolve()),
        "manifest_path": str(manifest_path.resolve()),
        "screen_path": str(screen_path.resolve()),
        "manifest_hash": manifest["manifest_hash"],
        "screen_hash": screen["screen_hash"],
        "provenance_receipt_hash": provenance["receipt_hash"],
        "live_enabled": False,
        "api_mutation_count": 0,
    }
    atomic_write_json(workflow / "prepare-receipt.json", receipt)
    atomic_write_json(current_path, descriptor)
    return receipt


def resolve_manual_components(
    config: dict[str, Any], session_date: str
) -> list[Path]:
    paths: list[Path] = []
    for value in config["manual_exposure_component_paths"]:
        path = Path(value.replace("{session_date}", session_date))
        regular_nonsymlink(path, "manual exposure component")
        paths.append(path.resolve())
    brokers: set[str] = set()
    for path in paths:
        component = load_json(path, "manual exposure component")
        broker = component.get("broker")
        if broker not in {"toss", "nh"}:
            raise PipelineBlockedError(
                f"manual exposure component broker is invalid: {path}"
            )
        if broker in brokers:
            raise PipelineBlockedError(
                f"manual exposure broker appears more than once: {broker}"
            )
        brokers.add(broker)
    missing = set(config["required_manual_exposure_brokers"]) - brokers
    if missing:
        raise PipelineBlockedError(
            f"fresh manual exposure components are missing for {sorted(missing)}"
        )
    return paths


def read_ledger_states(ledger_path: Path) -> list[dict[str, str]]:
    if not ledger_path.is_file() or ledger_path.is_symlink():
        return []
    uri = f"file:{ledger_path.as_posix()}?mode=ro"
    try:
        connection = sqlite3.connect(uri, uri=True)
        rows = connection.execute(
            "SELECT intent_id, plan_hash, state FROM intents ORDER BY intent_id"
        ).fetchall()
    except sqlite3.Error as exc:
        raise PipelineBlockedError(f"cannot inspect prior ledger: {ledger_path}") from exc
    finally:
        if "connection" in locals():
            connection.close()
    return [
        {"intent_id": str(row[0]), "plan_hash": str(row[1]), "state": str(row[2])}
        for row in rows
    ]


def state_market_directory(
    runtime_root: Path, identity_hash: str, market: str
) -> Path:
    identity_root = runtime_root / identity_hash
    if identity_root.exists() and (
        identity_root.is_symlink() or not identity_root.is_dir()
    ):
        raise PipelineBlockedError(
            "account identity state root must be a non-symlink directory"
        )
    lower = identity_root / market.lower()
    upper = identity_root / market
    for candidate in (lower, upper):
        if candidate.exists() and (
            candidate.is_symlink() or not candidate.is_dir()
        ):
            raise PipelineBlockedError(
                "market state root must be a non-symlink directory"
            )
    if lower.exists() and upper.exists() and lower.resolve() != upper.resolve():
        raise PipelineBlockedError(
            "both uppercase and lowercase market state roots exist"
        )
    if upper.exists():
        return upper
    return lower


def unresolved_state_directories(
    market_root: Path,
    *,
    exclude_date: str | None = None,
) -> list[tuple[Path, list[dict[str, str]]]]:
    if not market_root.exists():
        return []
    if market_root.is_symlink() or not market_root.is_dir():
        raise PipelineBlockedError("market state root must be a non-symlink directory")
    output: list[tuple[Path, list[dict[str, str]]]] = []
    for directory in sorted(market_root.iterdir(), key=lambda item: item.name):
        if (
            not directory.is_dir()
            or directory.is_symlink()
            or (exclude_date is not None and directory.name == exclude_date)
        ):
            continue
        states = read_ledger_states(directory / "ledger.sqlite3")
        unresolved = [
            item
            for item in states
            if item["state"] not in TERMINAL_OR_MANUAL
            or item["state"] == "MANUAL_BLOCK"
        ]
        if unresolved:
            output.append((directory, unresolved))
    return output


def block_unresolved_other_identities(
    *,
    runtime_root: Path,
    current_identity_hash: str,
    market: str,
) -> None:
    if not runtime_root.is_dir() or runtime_root.is_symlink():
        raise PipelineBlockedError("runtime root must be a non-symlink directory")
    for identity_root in sorted(runtime_root.iterdir(), key=lambda item: item.name):
        if (
            identity_root.name == current_identity_hash
            or len(identity_root.name) != 64
            or any(
                character not in "0123456789abcdef"
                for character in identity_root.name
            )
        ):
            continue
        if identity_root.is_symlink() or not identity_root.is_dir():
            raise PipelineBlockedError(
                "another account identity state root is not a regular directory"
            )
        roots = [
            candidate
            for candidate in (
                identity_root / market.lower(),
                identity_root / market,
            )
            if candidate.exists()
        ]
        if len(roots) > 1:
            raise PipelineBlockedError(
                "another identity has duplicate market state roots"
            )
        if roots and unresolved_state_directories(roots[0]):
            raise PipelineBlockedError(
                "unresolved state exists under another broker account identity"
            )


def prior_descriptor_for_state(
    config: dict[str, Any], market: str, state_directory: Path
) -> dict[str, Any]:
    session = state_directory.name
    descriptor_path = (
        config["runtime_root"]
        / "workflows"
        / market.lower()
        / session
        / "descriptor.json"
    )
    return load_json(descriptor_path, "prior workflow descriptor")


def reconcile_one(
    *,
    trader_root: Path,
    descriptor: dict[str, Any],
    output_path: Path,
) -> None:
    command = python_command(
        trader_root / "scripts" / "reconcile.py",
        "--plan",
        descriptor["plan_path"],
        "--arm",
        descriptor["arm_path"],
        "--broker",
        "kis-live",
        "--state-dir",
        descriptor["state_directory"],
        "--output",
        str(output_path),
    )
    result = subprocess.run(
        command,
        check=False,
        capture_output=True,
        text=True,
        env=subprocess_environment(),
    )
    if result.returncode not in {0, 2}:
        raise PipelineBlockedError(
            "reconciliation process failed outside its receipt contract"
        )


def mark_manual_block(state_directory: Path, reason: str) -> None:
    ledger_path = state_directory / "ledger.sqlite3"
    states = read_ledger_states(ledger_path)
    if not states:
        return
    from execution_core import Ledger

    ledger = Ledger(ledger_path)
    try:
        for item in states:
            state = ledger.get(item["intent_id"]).state
            if state in TERMINAL_OR_MANUAL:
                continue
            if state in {"ACKNOWLEDGED", "PARTIALLY_FILLED", "CANCEL_PENDING"}:
                ledger.transition(item["intent_id"], "UNKNOWN", {"reason": reason})
                state = "UNKNOWN"
            if state == "UNKNOWN":
                ledger.transition(item["intent_id"], "RECONCILING", {})
                state = "RECONCILING"
            if state in {"PLANNED", "WAIT_TRIGGER", "RESERVED", "RECONCILING"}:
                ledger.transition(item["intent_id"], "MANUAL_BLOCK", {"reason": reason})
            elif state == "SUBMITTING":
                ledger.transition(item["intent_id"], "UNKNOWN", {"reason": reason})
                ledger.transition(item["intent_id"], "RECONCILING", {})
                ledger.transition(item["intent_id"], "MANUAL_BLOCK", {"reason": reason})
    finally:
        ledger.close()


def reconcile_prior_states(
    *,
    config: dict[str, Any],
    trader_root: Path,
    market: str,
    identity_hash: str,
    current_session: str,
    receipt_directory: Path,
) -> None:
    market_root = state_market_directory(
        config["runtime_root"], identity_hash, market
    )
    unresolved = unresolved_state_directories(
        market_root, exclude_date=current_session
    )
    for state_directory, states in unresolved:
        if any(item["state"] == "MANUAL_BLOCK" for item in states):
            raise PipelineBlockedError(
                f"prior MANUAL_BLOCK requires explicit clearance: {state_directory}"
            )
        descriptor = prior_descriptor_for_state(config, market, state_directory)
        for attempt in range(1, RECONCILE_ATTEMPTS + 1):
            reconcile_one(
                trader_root=trader_root,
                descriptor=descriptor,
                output_path=(
                    receipt_directory
                    / f"prior-{state_directory.name}-reconcile-{attempt:02d}.json"
                ),
            )
            observed = read_ledger_states(state_directory / "ledger.sqlite3")
            if any(item["state"] == "MANUAL_BLOCK" for item in observed):
                raise PipelineBlockedError(
                    f"prior reconciliation produced MANUAL_BLOCK: {state_directory}"
                )
            remaining = [
                item for item in observed if item["state"] not in TERMINAL_OR_MANUAL
            ]
            if not remaining:
                break
            if attempt < RECONCILE_ATTEMPTS:
                time.sleep(RECONCILE_INTERVAL_SECONDS)
        else:
            mark_manual_block(
                state_directory,
                "read-only reconciliation exhausted 20 attempts",
            )
            raise PipelineBlockedError(
                f"prior state became MANUAL_BLOCK after reconciliation: "
                f"{state_directory}"
            )


def snapshot_and_plan(
    config: dict[str, Any],
    market: str,
    technical_root: Path,
    trader_root: Path,
) -> dict[str, Any]:
    session = current_market_date(market)
    descriptor_path = current_descriptor_path(config, market)
    descriptor = load_json(descriptor_path, "current daily descriptor")
    if descriptor.get("session_date") != session or descriptor.get("market") != market:
        raise PipelineBlockedError("current descriptor is not for this market session")
    if descriptor.get("status") in {"BLOCKED", "MANUAL_BLOCK"}:
        raise PipelineBlockedError(
            str(
                descriptor.get("blocked_reason")
                or "current market session is terminally blocked"
            )
        )
    if descriptor.get("status") == "MARKET_CLOSED":
        receipt = terminal_receipt(
            stage="snapshot",
            status="MARKET_CLOSED",
            market=market,
            session_date=session,
        )
        atomic_write_json(
            Path(descriptor["workflow_directory"]) / "snapshot-receipt.json",
            receipt,
        )
        return receipt
    if descriptor.get("status") == "ARMED_SHADOW":
        return load_json(
            Path(descriptor["workflow_directory"]) / "snapshot-receipt.json",
            "existing snapshot receipt",
        )
    if descriptor.get("status") != "PREPARED":
        raise PipelineBlockedError("daily prepare stage is not PREPARED")
    workflow = Path(descriptor["workflow_directory"])
    provenance = verify_provenance(config, technical_root, trader_root)
    if provenance["receipt_hash"] != descriptor["provenance_receipt_hash"]:
        raise PipelineBlockedError("runtime provenance differs from prepare")
    market_session = load_json(
        Path(descriptor["market_session_path"]), "market session"
    )
    regular_open = datetime.fromisoformat(market_session["regular_open"])
    now = datetime.now(timezone.utc)
    age_until_open = (regular_open.astimezone(timezone.utc) - now).total_seconds()
    snapshot_max_age = config["execution_policy"]["snapshot_max_age_seconds"]
    if age_until_open <= 0:
        raise PipelineBlockedError("account snapshot started after regular open")
    if age_until_open > snapshot_max_age:
        raise PipelineBlockedError(
            "account snapshot started too early for snapshot_max_age_seconds"
        )
    components = resolve_manual_components(config, session)
    account_directory = workflow / "account"
    account_directory.mkdir(parents=True, exist_ok=True)
    identity_path = account_directory / "account-identity.json"
    run_command(
        python_command(
            trader_root / "scripts" / "run_session.py",
            "account-identity",
            "--broker",
            "kis-live",
            "--environment",
            "shadow",
            "--output",
            str(identity_path),
        ),
        "broker account identity",
    )
    identity = load_json(identity_path, "account identity")
    identity_hash = identity.get("broker_account_identity_hash")
    validate_sha256(identity_hash, "broker account identity hash")
    block_unresolved_other_identities(
        runtime_root=config["runtime_root"],
        current_identity_hash=identity_hash,
        market=market,
    )
    reconcile_prior_states(
        config=config,
        trader_root=trader_root,
        market=market,
        identity_hash=identity_hash,
        current_session=session,
        receipt_directory=workflow / "reconciliation",
    )
    state_market_root = state_market_directory(
        config["runtime_root"], identity_hash, market
    )
    state_directory = state_market_root / session
    account_path = account_directory / "account.json"
    exposure_path = account_directory / "exposure.json"
    account_receipt_path = account_directory / "receipt.json"
    account_job = {
        "schema": "qta-account-snapshot-job/v1",
        "broker": "kis-live",
        "mode": "shadow",
        "market": market,
        "account_alias": config["account_aliases"][market],
        "universe_manifest": descriptor["manifest_path"],
        "fx_to_krw": "1" if market == "KR" else "KIS_PRESENT_BALANCE",
        "manual_exposure_components": [str(path) for path in components],
        "manual_component_max_age_seconds": config[
            "manual_component_max_age_seconds"
        ],
        "output_account_path": str(account_path.resolve()),
        "output_exposure_path": str(exposure_path.resolve()),
        "output_receipt_path": str(account_receipt_path.resolve()),
    }
    account_job_path = workflow / "jobs" / "account-snapshot.json"
    atomic_write_json(account_job_path, account_job)
    run_command(
        python_command(
            trader_root / "scripts" / "account_snapshot.py",
            "collect",
            "--job",
            str(account_job_path),
        ),
        "KIS and cross-broker account snapshot",
    )
    account_receipt = load_json(account_receipt_path, "account snapshot receipt")
    if (
        account_receipt.get("status") != "READY"
        or account_receipt.get("api_mutation_count") != 0
        or account_receipt.get("broker_account_identity_hash") != identity_hash
    ):
        raise PipelineBlockedError("account snapshot receipt is not READY/read-only")
    execution = {
        "schema": "qta-execution-policy/v1",
        "market": market,
        "timezone": market_session["timezone"],
        "entry_window_start": market_session["regular_open"],
        "entry_window_end": (
            datetime.fromisoformat(market_session["regular_open"])
            + timedelta(hours=1)
        ).isoformat(),
        "market_session": market_session,
        **config["execution_policy"],
    }
    execution = normalized_execution_policy(execution)
    execution_path = workflow / "execution-policy.json"
    atomic_write_json(execution_path, execution)
    plan_path = workflow / "order-plan.json"
    run_command(
        python_command(
            trader_root / "scripts" / "plan_orders.py",
            "--screen",
            descriptor["screen_path"],
            "--account",
            str(account_path),
            "--exposure",
            str(exposure_path),
            "--risk",
            str(config["risk_policy_path"]),
            "--execution",
            str(execution_path),
            "--output",
            str(plan_path),
        ),
        "deterministic order plan",
    )
    plan = load_json(plan_path, "order plan")
    if plan.get("plan_status") not in {"READY", "NO_ORDERS"}:
        raise PipelineBlockedError("order plan is not READY or NO_ORDERS")
    if plan.get("context", {}).get("broker_account_identity_hash") != identity_hash:
        raise PipelineBlockedError("plan account identity does not match runtime")
    arm_path = workflow / "trading-arm.json"
    run_command(
        python_command(
            trader_root / "scripts" / "run_session.py",
            "arm",
            "--plan",
            str(plan_path),
            "--broker",
            "kis-live",
            "--mode",
            "shadow",
            "--output",
            str(arm_path),
        ),
        "plan-bound trading arm",
    )
    arm = load_json(arm_path, "trading arm")
    if (
        arm.get("plan_hash") != plan.get("plan_hash")
        or arm.get("broker_account_identity_hash") != identity_hash
        or arm.get("mode") != "shadow"
    ):
        raise PipelineBlockedError("trading arm does not match plan/account/shadow")
    preview_path = workflow / "submit-preview.json"
    run_command(
        python_command(
            trader_root / "scripts" / "run_session.py",
            "preview",
            "--plan",
            str(plan_path),
            "--broker",
            "kis-live",
            "--output",
            str(preview_path),
        ),
        "venue and submit serialization preflight",
    )
    final_technical_digest, _ = source_tree_digest(technical_root)
    final_trader_digest, _ = source_tree_digest(trader_root)
    if (
        final_technical_digest != config["approved_technical_tree_sha256"]
        or final_trader_digest != config["approved_trader_tree_sha256"]
    ):
        raise PipelineBlockedError("installed skill source changed during snapshot")
    receipt = terminal_receipt(
        stage="snapshot",
        status="READY",
        market=market,
        session_date=session,
        details={
            "plan_hash": plan["plan_hash"],
            "arm_hash": arm["arm_hash"],
            "broker_account_identity_hash": identity_hash,
            "settled_cash": account_receipt["settled_cash"],
            "borrowed_buying_power_excluded": True,
            "fx_to_krw": account_receipt["fx_to_krw"],
            "state_directory": str(state_directory.resolve()),
        },
    )
    full_descriptor = {
        **descriptor,
        "status": "ARMED_SHADOW",
        "account_identity_path": str(identity_path.resolve()),
        "account_path": str(account_path.resolve()),
        "exposure_path": str(exposure_path.resolve()),
        "account_receipt_path": str(account_receipt_path.resolve()),
        "execution_policy_path": str(execution_path.resolve()),
        "plan_path": str(plan_path.resolve()),
        "plan_hash": plan["plan_hash"],
        "arm_path": str(arm_path.resolve()),
        "arm_hash": arm["arm_hash"],
        "broker_account_identity_hash": identity_hash,
        "state_directory": str(state_directory.resolve()),
        "session_receipt_path": str((workflow / "session-receipt.json").resolve()),
        "reconciliation_receipt_path": str(
            (workflow / "reconciliation-receipt.json").resolve()
        ),
        "live_enabled": False,
        "api_mutation_count": 0,
    }
    atomic_write_json(workflow / "snapshot-receipt.json", receipt)
    atomic_write_json(workflow / "descriptor.json", full_descriptor)
    atomic_write_json(descriptor_path, full_descriptor)
    return receipt


def reconcile_current(
    *,
    descriptor: dict[str, Any],
    trader_root: Path,
    output_path: Path,
) -> dict[str, Any]:
    state_directory = Path(descriptor["state_directory"])
    for attempt in range(1, RECONCILE_ATTEMPTS + 1):
        attempt_path = output_path.with_name(
            f"{output_path.stem}-attempt-{attempt:02d}.json"
        )
        reconcile_one(
            trader_root=trader_root,
            descriptor=descriptor,
            output_path=attempt_path,
        )
        states = read_ledger_states(state_directory / "ledger.sqlite3")
        remaining = [
            item for item in states if item["state"] not in TERMINAL_OR_MANUAL
        ]
        if not remaining:
            final = load_json(attempt_path, "reconciliation receipt")
            atomic_write_json(output_path, final)
            return final
        if attempt < RECONCILE_ATTEMPTS:
            time.sleep(RECONCILE_INTERVAL_SECONDS)
    mark_manual_block(
        state_directory,
        "read-only reconciliation exhausted 20 attempts",
    )
    states = read_ledger_states(state_directory / "ledger.sqlite3")
    manual = terminal_receipt(
        stage="reconcile",
        status="MANUAL_BLOCK",
        market=descriptor["market"],
        session_date=descriptor["session_date"],
        reason="UNKNOWN or CANCEL_PENDING remained after 20 read-only attempts",
        details={"intents": states, "emergency_alert": True},
    )
    atomic_write_json(output_path, manual)
    return manual


def build_final_report(
    *,
    config: dict[str, Any],
    descriptor: dict[str, Any],
    session_receipt: dict[str, Any],
    reconciliation: dict[str, Any],
) -> dict[str, Any]:
    plan = load_json(Path(descriptor["plan_path"]), "order plan")
    screen = load_json(Path(descriptor["screen_path"]), "screen")
    account_receipt = load_json(
        Path(descriptor["account_receipt_path"]), "account receipt"
    )
    states = read_ledger_states(
        Path(descriptor["state_directory"]) / "ledger.sqlite3"
    )
    selected = sum(
        len(screen["selected"][exchange])
        for exchange in (
            ("KOSPI", "KOSDAQ") if descriptor["market"] == "KR" else ("NYSE", "NASDAQ")
        )
    )
    state_counts = {
        state: sum(1 for item in states if item["state"] == state)
        for state in sorted({item["state"] for item in states})
    }
    metrics = session_receipt.get("polling_metrics", {})
    first_unmet = ""
    final_status = "READY"
    if state_counts.get("MANUAL_BLOCK", 0):
        final_status = "MANUAL_BLOCK"
        first_unmet = reconciliation.get(
            "reason", "one or more intents require manual clearance"
        )
    elif session_receipt.get("status") == "BLOCKED":
        final_status = "BLOCKED"
        first_unmet = session_receipt.get("reason", "session blocked")
    elif reconciliation.get("status") not in {"READY", None}:
        final_status = "BLOCKED"
        first_unmet = "reconciliation did not reach READY"
    submitted = int(metrics.get("submits_started", 0))
    if submitted != 0 or session_receipt.get("mutation_sent") is True:
        raise PipelineBlockedError("shadow session reported an API mutation")
    return {
        "schema": "qta-daily-shadow-final-report/v1",
        "status": final_status,
        "session_date": descriptor["session_date"],
        "market": descriptor["market"],
        "broker": "kis-live",
        "mode": "shadow",
        "manifest_hash": descriptor["manifest_hash"],
        "screen_hash": descriptor["screen_hash"],
        "plan_hash": descriptor["plan_hash"],
        "selected_count": selected,
        "planned_count": len(plan.get("intents", [])),
        "submitted_count": 0,
        "filled_count": state_counts.get("FILLED", 0),
        "cancelled_count": state_counts.get("CANCELLED", 0),
        "blocked_count": state_counts.get("MANUAL_BLOCK", 0),
        "cycles": int(metrics.get("cycles_completed", 0)),
        "skipped_cycles": int(metrics.get("cycles_skipped", 0)),
        "max_schedule_latency_ms": int(
            metrics.get("max_schedule_lateness_ms", 0)
        ),
        "max_quote_latency_ms": int(metrics.get("max_quote_latency_ms", 0)),
        "max_submit_latency_ms": int(metrics.get("max_submit_latency_ms", 0)),
        "settled_cash_used": format(
            Decimal(plan["settled_cash_start"])
            - Decimal(plan["settled_cash_unreserved"]),
            "f",
        ),
        "settled_cash_available": account_receipt["settled_cash"],
        "borrowed_cash_excluded": load_json(
            Path(descriptor["account_path"]), "account"
        )["borrowed_buying_power"],
        "intent_final_states": states,
        "first_unmet_gate": first_unmet,
        "api_mutation_count": 0,
        "live_enabled": False,
    }


def entry(
    config: dict[str, Any],
    market: str,
    technical_root: Path,
    trader_root: Path,
    max_cycles: int | None,
) -> dict[str, Any]:
    session = current_market_date(market)
    descriptor_path = current_descriptor_path(config, market)
    descriptor = load_json(descriptor_path, "current daily descriptor")
    if descriptor.get("session_date") != session or descriptor.get("market") != market:
        raise PipelineBlockedError("current descriptor is not for this market session")
    if descriptor.get("status") in {"BLOCKED", "MANUAL_BLOCK"}:
        raise PipelineBlockedError(
            str(
                descriptor.get("blocked_reason")
                or "current market session is terminally blocked"
            )
        )
    if descriptor.get("status") == "MARKET_CLOSED":
        receipt = terminal_receipt(
            stage="entry",
            status="MARKET_CLOSED",
            market=market,
            session_date=session,
        )
        atomic_write_json(
            Path(descriptor["workflow_directory"]) / "entry-receipt.json",
            receipt,
        )
        return receipt
    if descriptor.get("status") != "ARMED_SHADOW":
        raise PipelineBlockedError("daily snapshot/plan/arm stage is not ready")
    provenance = verify_provenance(config, technical_root, trader_root)
    if provenance["receipt_hash"] != descriptor["provenance_receipt_hash"]:
        raise PipelineBlockedError("runtime provenance differs from prepared plan")
    identity_hash = descriptor["broker_account_identity_hash"]
    validate_sha256(identity_hash, "descriptor broker identity")
    expected_state = (
        state_market_directory(config["runtime_root"], identity_hash, market)
        / session
    ).resolve()
    if Path(descriptor["state_directory"]).resolve() != expected_state:
        raise PipelineBlockedError(
            "state directory does not follow the account/market/session rule"
        )
    ledger_path = expected_state / "ledger.sqlite3"
    current_states = read_ledger_states(ledger_path)
    if ledger_path.exists():
        if any(item["state"] not in TERMINAL_OR_MANUAL for item in current_states):
            reconciliation = reconcile_current(
                descriptor=descriptor,
                trader_root=trader_root,
                output_path=Path(descriptor["reconciliation_receipt_path"]),
            )
            if reconciliation.get("status") != "READY":
                raise PipelineBlockedError(
                    "existing current-session ledger did not reconcile to terminal"
                )
        raise PipelineBlockedError(
            "current session was already started; automatic re-entry is forbidden"
        )
    attempt_path = Path(descriptor["workflow_directory"]) / "entry-attempt.json"
    if attempt_path.exists():
        raise PipelineBlockedError(
            "current session already has an entry-attempt marker"
        )
    atomic_write_json(
        attempt_path,
        {
            "schema": "qta-shadow-entry-attempt/v1",
            "market": market,
            "session_date": session,
            "plan_hash": descriptor["plan_hash"],
            "arm_hash": descriptor["arm_hash"],
            "started_at": datetime.now(timezone.utc).isoformat(),
            "live_enabled": False,
            "api_mutation_count": 0,
        },
    )
    command = python_command(
        trader_root / "scripts" / "run_session.py",
        "run",
        "--plan",
        descriptor["plan_path"],
        "--arm",
        descriptor["arm_path"],
        "--broker",
        "kis-live",
        "--mode",
        "shadow",
        "--state-dir",
        descriptor["state_directory"],
        "--output",
        descriptor["session_receipt_path"],
    )
    if max_cycles is not None:
        if max_cycles <= 0:
            raise PipelineBlockedError("max_cycles must be positive when supplied")
        command.extend(["--max-cycles", str(max_cycles)])
    result = subprocess.run(
        command,
        check=False,
        capture_output=True,
        text=True,
        env=subprocess_environment(),
    )
    session_receipt = load_json(
        Path(descriptor["session_receipt_path"]), "session receipt"
    )
    reconciliation = reconcile_current(
        descriptor=descriptor,
        trader_root=trader_root,
        output_path=Path(descriptor["reconciliation_receipt_path"]),
    )
    final_technical_digest, _ = source_tree_digest(technical_root)
    final_trader_digest, _ = source_tree_digest(trader_root)
    if (
        final_technical_digest != config["approved_technical_tree_sha256"]
        or final_trader_digest != config["approved_trader_tree_sha256"]
    ):
        raise PipelineBlockedError("installed skill source changed during entry")
    report = build_final_report(
        config=config,
        descriptor=descriptor,
        session_receipt=session_receipt,
        reconciliation=reconciliation,
    )
    workflow = Path(descriptor["workflow_directory"])
    atomic_write_json(workflow / "final-report.json", report)
    atomic_write_json(
        descriptor_path,
        {
            **descriptor,
            "status": report["status"],
            "final_report_path": str((workflow / "final-report.json").resolve()),
        },
    )
    if result.returncode != 0 or report["status"] not in {"READY"}:
        raise PipelineBlockedError(
            report["first_unmet_gate"] or "shadow session did not finish READY"
        )
    return report


def self_test() -> None:
    with tempfile.TemporaryDirectory(prefix="qta-daily-pipeline-") as temporary:
        root = Path(temporary)
        skill = root / "skill"
        skill.mkdir()
        atomic_write(skill / "SKILL.md", b"test\n")
        first, ignored = source_tree_digest(skill)
        first_raw = raw_tree_digest(skill)
        if ignored:
            raise AssertionError("unexpected ignored self-test files")
        pycache = skill / "__pycache__"
        pycache.mkdir()
        atomic_write(pycache / "derived.cpython-314.pyc", b"derived\n")
        second, ignored = source_tree_digest(skill)
        second_raw = raw_tree_digest(skill)
        if first != second or first_raw == second_raw or len(ignored) != 1:
            raise AssertionError("derived-bytecode isolation failed")
        receipt = terminal_receipt(
            stage="self-test",
            status="PASS",
            market="US",
            session_date="2026-07-28",
        )
        if receipt["api_mutation_count"] != 0 or receipt["live_enabled"]:
            raise AssertionError("shadow safety receipt failed")
    print(
        json.dumps(
            {
                "self_test": "PASS",
                "derived_bytecode_isolated": True,
                "live_enabled": False,
                "api_mutation_count": 0,
            },
            sort_keys=True,
        )
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    for name in ("prepare", "snapshot", "entry"):
        stage = subparsers.add_parser(name)
        stage.add_argument("--config", required=True)
        stage.add_argument("--market", choices=("KR", "US"), required=True)
        stage.add_argument("--technical-skill-root", required=True)
        stage.add_argument("--trader-skill-root", required=True)
        if name == "entry":
            stage.add_argument("--max-cycles", type=int)
    subparsers.add_parser("self-test")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.command == "self-test":
        self_test()
        return 0
    config_path = absolute_path(args.config, "config")
    technical_root = absolute_path(
        args.technical_skill_root, "technical_skill_root"
    )
    trader_root = absolute_path(args.trader_skill_root, "trader_skill_root")
    market = args.market
    session = current_market_date(market)
    try:
        config = load_config(config_path)
        config["runtime_root"].mkdir(parents=True, exist_ok=True, mode=0o700)
        if args.command == "prepare":
            output = prepare(config, market, technical_root, trader_root)
        elif args.command == "snapshot":
            output = snapshot_and_plan(
                config, market, technical_root, trader_root
            )
        else:
            output = entry(
                config,
                market,
                technical_root,
                trader_root,
                args.max_cycles,
            )
        print(json.dumps(output, ensure_ascii=False, sort_keys=True))
        return 0
    except (
        PipelineBlockedError,
        market_calendar.CalendarBlockedError,
        OSError,
        ValueError,
        json.JSONDecodeError,
    ) as exc:
        receipt = terminal_receipt(
            stage=args.command,
            status="BLOCKED",
            market=market,
            session_date=session,
            reason=str(exc),
        )
        try:
            workflow = workflow_directory(
                {"runtime_root": absolute_path(
                    load_json(config_path, "daily config")["runtime_root"],
                    "runtime_root",
                )},
                market,
                session,
            )
            blocked_path = workflow / f"{args.command}-blocked.json"
            stage_receipt_path = workflow / f"{args.command}-receipt.json"
            atomic_write_json(blocked_path, receipt)
            atomic_write_json(stage_receipt_path, receipt)
            descriptor_path = current_descriptor_path(
                {"runtime_root": workflow.parents[2]},
                market,
            )
            existing: dict[str, Any] = {}
            if descriptor_path.is_file() and not descriptor_path.is_symlink():
                candidate = load_json(descriptor_path, "current daily descriptor")
                if (
                    candidate.get("market") == market
                    and candidate.get("session_date") == session
                ):
                    existing = candidate
            final_status = (
                "MANUAL_BLOCK"
                if existing.get("status") == "MANUAL_BLOCK"
                else "BLOCKED"
            )
            blocked_descriptor = {
                **existing,
                "schema": "qta-daily-shadow-descriptor/v1",
                "status": final_status,
                "market": market,
                "session_date": session,
                "workflow_directory": str(workflow.resolve()),
                "blocked_stage": args.command,
                "blocked_reason": str(exc),
                "blocked_receipt_path": str(blocked_path.resolve()),
                "live_enabled": False,
                "api_mutation_count": 0,
            }
            atomic_write_json(
                workflow / "descriptor.json",
                blocked_descriptor,
            )
            atomic_write_json(descriptor_path, blocked_descriptor)
        except (OSError, ValueError, KeyError):
            pass
        print(json.dumps(receipt, ensure_ascii=False, sort_keys=True), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

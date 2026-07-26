#!/usr/bin/env python3
"""Report Secuway CLI/runtime state without reading or printing credentials."""

from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any


SCHEMA = "secuway-agent-status/v1"


def native_architecture(machine: str | None = None) -> str:
    value = (machine or platform.machine()).lower()
    if value in {"amd64", "x86_64"}:
        return "amd64"
    if value in {"arm64", "aarch64"}:
        return "arm64"
    return value or "unknown"


def cli_candidates(explicit: str | None) -> list[Path]:
    candidates: list[Path] = []
    if explicit:
        candidates.append(Path(explicit).expanduser())
    located = shutil.which("secuway")
    if located:
        candidates.append(Path(located))
    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        candidates.append(
            Path(local_app_data)
            / "mrcha-skills"
            / "secuway"
            / "bin"
            / "secuway.exe"
        )
    candidates.append(Path.home() / ".local" / "bin" / "secuway")
    output: list[Path] = []
    seen: set[str] = set()
    for candidate in candidates:
        key = str(candidate)
        if key not in seen:
            seen.add(key)
            output.append(candidate)
    return output


def find_cli(explicit: str | None) -> Path | None:
    for candidate in cli_candidates(explicit):
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return candidate.resolve()
    return None


def invoke(arguments: list[str], timeout: int = 20) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        arguments,
        check=False,
        capture_output=True,
        text=True,
        timeout=timeout,
        stdin=subprocess.DEVNULL,
    )


def safe_error(process: subprocess.CompletedProcess[str]) -> str:
    text = (process.stderr or process.stdout or "").strip().splitlines()
    if not text:
        return f"exit code {process.returncode}"
    return text[-1][:240]


def inspect(cli: Path, server: str) -> dict[str, Any]:
    result: dict[str, Any] = {
        "schema": SCHEMA,
        "status": "NOT_READY",
        "platform": sys.platform,
        "architecture": native_architecture(),
        "cli": str(cli),
        "cli_version": None,
        "authentication": None,
        "credential_store": None,
        "runtime": "NOT_READY",
        "runtime_error": None,
        "secrets_printed": False,
    }

    version = invoke([str(cli), "version"], timeout=10)
    if version.returncode != 0:
        result["runtime_error"] = f"version: {safe_error(version)}"
        return result
    result["cli_version"] = version.stdout.strip()

    status = invoke(
        [str(cli), "status", "--server", server, "--json"],
        timeout=20,
    )
    if status.returncode != 0:
        result["runtime_error"] = f"status: {safe_error(status)}"
        return result
    try:
        credential_status = json.loads(status.stdout)
    except json.JSONDecodeError:
        result["runtime_error"] = "status returned invalid JSON"
        return result
    if credential_status.get("schema") != "secuway-auth-status/v1":
        result["runtime_error"] = "status returned an unknown schema"
        return result
    if credential_status.get("secrets_printed") is not False:
        result["runtime_error"] = "status did not preserve the no-secrets contract"
        return result
    result["authentication"] = credential_status.get("status")
    result["credential_store"] = credential_status.get("credential_store")

    doctor = invoke([str(cli), "doctor"], timeout=30)
    if doctor.returncode == 0:
        result["runtime"] = "READY"
    else:
        result["runtime_error"] = f"doctor: {safe_error(doctor)}"

    if result["runtime"] == "READY":
        if result["authentication"] == "CACHED":
            result["status"] = "READY"
        else:
            result["status"] = "NEEDS_ENROLLMENT"
    return result


def self_test() -> None:
    assert native_architecture("x86_64") == "amd64"
    assert native_architecture("AMD64") == "amd64"
    assert native_architecture("aarch64") == "arm64"
    assert native_architecture("arm64") == "arm64"
    candidates = cli_candidates("/tmp/example-secuway")
    assert candidates[0] == Path("/tmp/example-secuway")
    assert len({str(path) for path in candidates}) == len(candidates)
    print("secuway status helper self-test: PASS")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cli", help="explicit secuway executable")
    parser.add_argument(
        "--server",
        default="https://ysvpn.yonsei.ac.kr",
        help="SecuwaySSL HTTPS gateway",
    )
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()

    if args.self_test:
        self_test()
        return 0

    cli = find_cli(args.cli)
    if cli is None:
        result: dict[str, Any] = {
            "schema": SCHEMA,
            "status": "NOT_INSTALLED",
            "platform": sys.platform,
            "architecture": native_architecture(),
            "cli": None,
            "authentication": None,
            "credential_store": None,
            "runtime": "NOT_READY",
            "runtime_error": "secuway executable not found",
            "secrets_printed": False,
        }
    else:
        result = inspect(cli, args.server)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

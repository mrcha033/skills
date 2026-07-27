#!/usr/bin/env python3
"""Generate and execute deterministic user-level systemd jobs for QTA."""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import re
import stat
import sys
from pathlib import Path
from typing import Any, Mapping


BUNDLE_SCHEMA = "qta-systemd-bundle/v1"
RECEIPT_SCHEMA = "qta-systemd-generation-receipt/v1"
TOP_FIELDS = {
    "schema",
    "unit_prefix",
    "python_executable",
    "technical_skill_root",
    "trader_skill_root",
    "environment_file",
    "runtime_directory",
    "jobs",
}
JOB_FIELDS = {
    "name",
    "kind",
    "market",
    "input_path",
    "plan_path",
    "arm_path",
    "state_directory",
    "output_path",
    "broker",
    "mode",
    "venue_map",
    "max_cycles",
    "timeout_start_seconds",
}
SCHEDULES = {
    ("eod", "KR"): "Mon..Fri *-*-* 07:00:00 Asia/Seoul",
    ("eod", "US"): "Mon..Fri *-*-* 18:00:00 America/New_York",
    ("snapshot", "KR"): "Mon..Fri *-*-* 08:50:00 Asia/Seoul",
    ("snapshot", "US"): "Mon..Fri *-*-* 09:20:00 America/New_York",
    ("entry", "KR"): "Mon..Fri *-*-* 08:59:00 Asia/Seoul",
    ("entry", "US"): "Mon..Fri *-*-* 09:29:00 America/New_York",
}
UNIT_NAME = re.compile(r"^[a-z0-9][a-z0-9-]{0,47}$")


class UnitBlockedError(ValueError):
    """Raised when a unit bundle would rely on an unsafe or ambiguous value."""


def canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def atomic_write(path: Path, payload: bytes, mode: int = 0o600) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    descriptor = os.open(
        temporary,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL,
        mode,
    )
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


def exact_fields(value: Mapping[str, Any], expected: set[str], label: str) -> None:
    if set(value) != expected:
        raise UnitBlockedError(
            f"{label} fields mismatch; missing={sorted(expected - set(value))}, "
            f"extra={sorted(set(value) - expected)}"
        )


def absolute_path(value: Any, label: str) -> Path:
    path = Path(str(value)).expanduser()
    if not path.is_absolute():
        raise UnitBlockedError(f"{label} must be absolute")
    if any(character in str(path) for character in ("\n", "\r", "\0")):
        raise UnitBlockedError(f"{label} contains an invalid character")
    return path


def regular_nonsymlink(path: Path, label: str, mode: int | None = None) -> None:
    try:
        metadata = path.lstat()
    except FileNotFoundError as exc:
        raise UnitBlockedError(f"{label} does not exist: {path}") from exc
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
        raise UnitBlockedError(f"{label} must be a regular non-symlink file")
    if mode is not None and stat.S_IMODE(metadata.st_mode) != mode:
        raise UnitBlockedError(f"{label} must have mode {mode:04o}")


def within(path: Path, root: Path, label: str) -> Path:
    resolved = path.resolve(strict=False)
    try:
        resolved.relative_to(root.resolve(strict=False))
    except ValueError as exc:
        raise UnitBlockedError(f"{label} must be inside runtime_directory") from exc
    return resolved


def normalize_bundle(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise UnitBlockedError("bundle must be one JSON object")
    exact_fields(raw, TOP_FIELDS, "bundle")
    if raw["schema"] != BUNDLE_SCHEMA:
        raise UnitBlockedError(f"bundle.schema must be {BUNDLE_SCHEMA}")
    prefix = str(raw["unit_prefix"])
    if not UNIT_NAME.fullmatch(prefix):
        raise UnitBlockedError("unit_prefix must be lowercase letters, digits, hyphens")
    python_path = absolute_path(raw["python_executable"], "python_executable")
    try:
        python = python_path.resolve(strict=True)
    except FileNotFoundError as exc:
        raise UnitBlockedError("python_executable does not exist") from exc
    regular_nonsymlink(python, "resolved python_executable")
    technical_root = absolute_path(raw["technical_skill_root"], "technical_skill_root")
    trader_root = absolute_path(raw["trader_skill_root"], "trader_skill_root")
    for root, label in (
        (technical_root, "technical_skill_root"),
        (trader_root, "trader_skill_root"),
    ):
        if not root.is_dir() or root.is_symlink():
            raise UnitBlockedError(f"{label} must be a non-symlink directory")
    required_scripts = (
        technical_root / "scripts" / "fetch_kis_kr_eod.py",
        technical_root / "scripts" / "fetch_kis_us_eod.py",
        trader_root / "scripts" / "account_snapshot.py",
        trader_root / "scripts" / "run_session.py",
        trader_root / "scripts" / "systemd_units.py",
    )
    for script in required_scripts:
        regular_nonsymlink(script, "required skill script")
    environment = absolute_path(raw["environment_file"], "environment_file")
    regular_nonsymlink(environment, "environment_file", 0o600)
    runtime = absolute_path(raw["runtime_directory"], "runtime_directory")
    if runtime.exists() and (not runtime.is_dir() or runtime.is_symlink()):
        raise UnitBlockedError("runtime_directory must be a non-symlink directory")
    jobs = raw["jobs"]
    if not isinstance(jobs, list) or not jobs:
        raise UnitBlockedError("jobs must be a non-empty array")
    normalized_jobs: list[dict[str, Any]] = []
    names: set[str] = set()
    pairs: set[tuple[str, str]] = set()
    for index, job in enumerate(jobs):
        label = f"jobs[{index}]"
        if not isinstance(job, dict):
            raise UnitBlockedError(f"{label} must be an object")
        exact_fields(job, JOB_FIELDS, label)
        name = str(job["name"])
        if not UNIT_NAME.fullmatch(name) or name in names:
            raise UnitBlockedError(f"{label}.name is invalid or duplicate")
        names.add(name)
        kind = str(job["kind"]).lower()
        market = str(job["market"]).upper()
        if (kind, market) not in SCHEDULES or (kind, market) in pairs:
            raise UnitBlockedError(f"{label} has an unsupported or duplicate job scope")
        pairs.add((kind, market))
        timeout = job["timeout_start_seconds"]
        if (
            isinstance(timeout, bool)
            or not isinstance(timeout, int)
            or timeout < 60
            or timeout > 86400
        ):
            raise UnitBlockedError(f"{label}.timeout_start_seconds must be 60..86400")
        max_cycles = job["max_cycles"]
        if (
            isinstance(max_cycles, bool)
            or not isinstance(max_cycles, int)
            or max_cycles < 0
        ):
            raise UnitBlockedError(f"{label}.max_cycles must be an integer >= 0")
        path_values = {
            field: (
                within(absolute_path(job[field], f"{label}.{field}"), runtime, label)
                if str(job[field])
                else None
            )
            for field in (
                "input_path",
                "plan_path",
                "arm_path",
                "state_directory",
                "output_path",
                "venue_map",
            )
        }
        broker = str(job["broker"])
        mode = str(job["mode"])
        if kind == "eod":
            if path_values["input_path"] is None:
                raise UnitBlockedError(f"{label}.input_path is required for EOD")
            if any(
                path_values[field] is not None
                for field in (
                    "plan_path",
                    "arm_path",
                    "state_directory",
                    "output_path",
                    "venue_map",
                )
            ):
                raise UnitBlockedError(f"{label} has entry-only paths on an EOD job")
            if broker or mode or max_cycles:
                raise UnitBlockedError(f"{label} has entry-only values on an EOD job")
        elif kind == "snapshot":
            if path_values["input_path"] is None:
                raise UnitBlockedError(
                    f"{label}.input_path is required for an account snapshot"
                )
            if any(
                path_values[field] is not None
                for field in (
                    "plan_path",
                    "arm_path",
                    "state_directory",
                    "output_path",
                    "venue_map",
                )
            ):
                raise UnitBlockedError(
                    f"{label} has entry-only paths on an account snapshot job"
                )
            if broker != "kis-live" or mode != "shadow":
                raise UnitBlockedError(
                    f"{label} account snapshot requires kis-live/shadow"
                )
            if max_cycles:
                raise UnitBlockedError(
                    f"{label}.max_cycles must be zero for an account snapshot"
                )
        else:
            required = ("plan_path", "arm_path", "state_directory", "output_path")
            if any(path_values[field] is None for field in required):
                raise UnitBlockedError(f"{label} is missing an entry path")
            if path_values["input_path"] is not None:
                raise UnitBlockedError(f"{label}.input_path must be empty for entry")
            if broker not in {"kis-paper", "kis-live"}:
                raise UnitBlockedError(f"{label}.broker must be kis-paper or kis-live")
            if mode not in {"paper", "shadow"}:
                raise UnitBlockedError(
                    f"{label}.mode must be paper or shadow; live is not generated"
                )
            if broker == "kis-paper" and mode != "paper":
                raise UnitBlockedError(f"{label} kis-paper requires paper mode")
            if broker == "kis-live" and mode != "shadow":
                raise UnitBlockedError(f"{label} kis-live requires shadow mode")
        normalized_jobs.append(
            {
                "name": name,
                "kind": kind,
                "market": market,
                "schedule": SCHEDULES[(kind, market)],
                "persistent": kind == "eod",
                "input_path": str(path_values["input_path"] or ""),
                "plan_path": str(path_values["plan_path"] or ""),
                "arm_path": str(path_values["arm_path"] or ""),
                "state_directory": str(path_values["state_directory"] or ""),
                "output_path": str(path_values["output_path"] or ""),
                "broker": broker,
                "mode": mode,
                "venue_map": str(path_values["venue_map"] or ""),
                "max_cycles": max_cycles,
                "timeout_start_seconds": timeout,
            }
        )
    normalized_jobs.sort(key=lambda item: item["name"])
    return {
        "schema": BUNDLE_SCHEMA,
        "unit_prefix": prefix,
        "python_executable": str(python.resolve()),
        "technical_skill_root": str(technical_root.resolve()),
        "trader_skill_root": str(trader_root.resolve()),
        "environment_file": str(environment.resolve()),
        "runtime_directory": str(runtime.resolve(strict=False)),
        "jobs": normalized_jobs,
    }


def execution_bundle(bundle: dict[str, Any]) -> dict[str, Any]:
    """Project a normalized bundle back to the exact executable v1 schema."""
    return {
        key: (
            [
                {field: job[field] for field in JOB_FIELDS}
                for job in bundle["jobs"]
            ]
            if key == "jobs"
            else bundle[key]
        )
        for key in TOP_FIELDS
    }


def systemd_quote(value: str) -> str:
    escaped = (
        value.replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("$", "\\$")
        .replace("%", "%%")
    )
    return f'"{escaped}"'


def systemd_path(value: str) -> str:
    """Escape one absolute path for directives that do not accept quoting."""
    escaped: list[str] = []
    for octet in value.encode("utf-8"):
        character = chr(octet)
        if character == "%":
            escaped.append("%%")
        elif 0x21 <= octet <= 0x7E and character not in {'"', "'", "\\"}:
            escaped.append(character)
        else:
            escaped.append(f"\\x{octet:02x}")
    return "".join(escaped)


def service_text(bundle: dict[str, Any], job: dict[str, Any], bundle_path: Path) -> str:
    root = Path(bundle["runtime_directory"])
    conditions = []
    for field in ("input_path", "plan_path", "arm_path", "venue_map"):
        if job[field]:
            conditions.append(f"ConditionPathExists={systemd_path(job[field])}")
    command = " ".join(
        systemd_quote(item)
        for item in (
            bundle["python_executable"],
            "-B",
            "-s",
            str(Path(bundle["trader_skill_root"]) / "scripts" / "systemd_units.py"),
            "execute",
            "--bundle",
            str(bundle_path),
            "--name",
            job["name"],
        )
    )
    return (
        "[Unit]\n"
        f"Description=QTA {job['kind']} {job['market']} ({job['name']})\n"
        "After=network-online.target\n"
        "Wants=network-online.target\n"
        + ("\n".join(conditions) + "\n" if conditions else "")
        + "\n[Service]\n"
        "Type=oneshot\n"
        f"WorkingDirectory={systemd_path(str(root))}\n"
        f"EnvironmentFile={systemd_path(bundle['environment_file'])}\n"
        'Environment="PYTHONDONTWRITEBYTECODE=1"\n'
        "UnsetEnvironment=PYTHONPATH PYTHONHOME\n"
        f"ExecStart={command}\n"
        f"TimeoutStartSec={job['timeout_start_seconds']}s\n"
        "UMask=0077\n"
        "NoNewPrivileges=yes\n"
        "PrivateTmp=yes\n"
        "PrivateDevices=yes\n"
        "ProtectSystem=strict\n"
        "ProtectHome=read-only\n"
        "ProtectKernelTunables=yes\n"
        "ProtectKernelModules=yes\n"
        "ProtectControlGroups=yes\n"
        "RestrictSUIDSGID=yes\n"
        "LockPersonality=yes\n"
        "MemoryDenyWriteExecute=yes\n"
        "RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6\n"
        f"ReadWritePaths={systemd_path(str(root))}\n"
        f"ReadOnlyPaths={systemd_path(bundle['technical_skill_root'])} "
        f"{systemd_path(bundle['trader_skill_root'])} "
        f"{systemd_path(bundle['environment_file'])}\n"
    )


def timer_text(prefix: str, job: dict[str, Any]) -> str:
    return (
        "[Unit]\n"
        f"Description=Schedule QTA {job['kind']} {job['market']} ({job['name']})\n"
        "\n[Timer]\n"
        f"OnCalendar={job['schedule']}\n"
        "AccuracySec=1s\n"
        "RandomizedDelaySec=0\n"
        f"Persistent={'yes' if job['persistent'] else 'no'}\n"
        f"Unit={prefix}-{job['name']}.service\n"
        "\n[Install]\n"
        "WantedBy=timers.target\n"
    )


def generate(bundle: dict[str, Any], output_directory: Path) -> dict[str, Any]:
    if output_directory.exists():
        if output_directory.is_symlink() or not output_directory.is_dir():
            raise UnitBlockedError(
                "output_directory must be a non-symlink directory"
            )
        if any(output_directory.iterdir()):
            raise UnitBlockedError(
                "output_directory must be empty to prevent stale unit files"
            )
    else:
        output_directory.mkdir(parents=True)
    bundle_path = output_directory / "systemd-bundle.json"
    runnable_bundle = execution_bundle(bundle)
    atomic_write(
        bundle_path,
        (json.dumps(runnable_bundle, indent=2, sort_keys=True) + "\n").encode(
            "utf-8"
        ),
    )
    files: list[dict[str, Any]] = []
    prefix = bundle["unit_prefix"]
    for job in bundle["jobs"]:
        service_path = output_directory / f"{prefix}-{job['name']}.service"
        timer_path = output_directory / f"{prefix}-{job['name']}.timer"
        atomic_write(service_path, service_text(bundle, job, bundle_path).encode())
        atomic_write(timer_path, timer_text(prefix, job).encode())
        for path in (service_path, timer_path):
            files.append(
                {
                    "path": str(path.resolve()),
                    "sha256": sha256_file(path),
                    "bytes": path.stat().st_size,
                }
            )
    files.sort(key=lambda item: item["path"])
    without_hash = {
        "schema": RECEIPT_SCHEMA,
        "status": "READY",
        "bundle_path": str(bundle_path.resolve()),
        "bundle_sha256": sha256_file(bundle_path),
        "unit_files": files,
        "activation_performed": False,
        "live_enabled": False,
        "api_mutation_count": 0,
    }
    receipt = {
        **without_hash,
        "receipt_hash": sha256_bytes(canonical_json(without_hash).encode("utf-8")),
    }
    atomic_write(
        output_directory / "generation-receipt.json",
        (json.dumps(receipt, indent=2, sort_keys=True) + "\n").encode(),
    )
    return receipt


def command_for(bundle: dict[str, Any], job: dict[str, Any]) -> list[str]:
    python = bundle["python_executable"]
    if job["kind"] == "eod":
        script_name = (
            "fetch_kis_kr_eod.py" if job["market"] == "KR" else "fetch_kis_us_eod.py"
        )
        return [
            python,
            "-B",
            "-s",
            str(Path(bundle["technical_skill_root"]) / "scripts" / script_name),
            "collect",
            "--job",
            job["input_path"],
        ]
    if job["kind"] == "snapshot":
        return [
            python,
            "-B",
            "-s",
            str(
                Path(bundle["trader_skill_root"])
                / "scripts"
                / "account_snapshot.py"
            ),
            "collect",
            "--job",
            job["input_path"],
        ]
    command = [
        python,
        "-B",
        "-s",
        str(Path(bundle["trader_skill_root"]) / "scripts" / "run_session.py"),
        "run",
        "--plan",
        job["plan_path"],
        "--arm",
        job["arm_path"],
        "--broker",
        job["broker"],
        "--mode",
        job["mode"],
        "--state-dir",
        job["state_directory"],
        "--output",
        job["output_path"],
    ]
    if job["venue_map"]:
        command.extend(["--venue-map", job["venue_map"]])
    if job["max_cycles"]:
        command.extend(["--max-cycles", str(job["max_cycles"])])
    return command


def execute(bundle: dict[str, Any], name: str) -> None:
    job = next((item for item in bundle["jobs"] if item["name"] == name), None)
    if job is None:
        raise UnitBlockedError(f"unknown job: {name}")
    runtime = Path(bundle["runtime_directory"])
    lock_directory = runtime / "locks"
    try:
        lock_directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        lock_metadata = lock_directory.lstat()
    except OSError as exc:
        raise UnitBlockedError("cannot create or inspect the lock directory") from exc
    if stat.S_ISLNK(lock_metadata.st_mode) or not stat.S_ISDIR(
        lock_metadata.st_mode
    ):
        raise UnitBlockedError("lock directory must be a non-symlink directory")
    lock_path = lock_directory / f"{job['market'].lower()}.lock"
    lock_flags = os.O_RDWR | os.O_CREAT
    if hasattr(os, "O_NOFOLLOW"):
        lock_flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(lock_path, lock_flags, 0o600)
    except OSError as exc:
        raise UnitBlockedError("cannot securely open the market lock") from exc
    if not stat.S_ISREG(os.fstat(descriptor).st_mode):
        os.close(descriptor)
        raise UnitBlockedError("market lock must be a regular file")
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError as exc:
        os.close(descriptor)
        raise UnitBlockedError(
            f"another {job['market']} QTA process owns the single-writer lock"
        ) from exc
    os.set_inheritable(descriptor, True)
    environment = dict(os.environ)
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    environment.pop("PYTHONPATH", None)
    environment.pop("PYTHONHOME", None)
    os.execve(command_for(bundle, job)[0], command_for(bundle, job), environment)


def self_test() -> None:
    import tempfile

    with tempfile.TemporaryDirectory(prefix="qta-systemd-") as temporary:
        root = Path(temporary)
        technical = root / "technical"
        trader = root / "trader"
        runtime = root / "runtime"
        output = root / "units"
        for path in (
            technical / "scripts",
            trader / "scripts",
            runtime,
        ):
            path.mkdir(parents=True)
        for path in (
            technical / "scripts" / "fetch_kis_kr_eod.py",
            technical / "scripts" / "fetch_kis_us_eod.py",
            trader / "scripts" / "run_session.py",
            trader / "scripts" / "account_snapshot.py",
            trader / "scripts" / "systemd_units.py",
            runtime / "kr-eod.json",
            runtime / "kr-account-snapshot.json",
            runtime / "us-plan.json",
            runtime / "us-arm.json",
        ):
            path.write_text("{}\n", encoding="utf-8")
        environment = root / "secrets.env"
        environment.write_text("QTA_SELF_TEST=1\n", encoding="utf-8")
        environment.chmod(0o600)
        raw = {
            "schema": BUNDLE_SCHEMA,
            "unit_prefix": "qta",
            "python_executable": str(Path(sys.executable).resolve()),
            "technical_skill_root": str(technical),
            "trader_skill_root": str(trader),
            "environment_file": str(environment),
            "runtime_directory": str(runtime),
            "jobs": [
                {
                    "name": "eod-kr",
                    "kind": "eod",
                    "market": "KR",
                    "input_path": str(runtime / "kr-eod.json"),
                    "plan_path": "",
                    "arm_path": "",
                    "state_directory": "",
                    "output_path": "",
                    "broker": "",
                    "mode": "",
                    "venue_map": "",
                    "max_cycles": 0,
                    "timeout_start_seconds": 7200,
                },
                {
                    "name": "snapshot-kr",
                    "kind": "snapshot",
                    "market": "KR",
                    "input_path": str(runtime / "kr-account-snapshot.json"),
                    "plan_path": "",
                    "arm_path": "",
                    "state_directory": "",
                    "output_path": "",
                    "broker": "kis-live",
                    "mode": "shadow",
                    "venue_map": "",
                    "max_cycles": 0,
                    "timeout_start_seconds": 300,
                },
                {
                    "name": "entry-us",
                    "kind": "entry",
                    "market": "US",
                    "input_path": "",
                    "plan_path": str(runtime / "us-plan.json"),
                    "arm_path": str(runtime / "us-arm.json"),
                    "state_directory": str(runtime / "state-us"),
                    "output_path": str(runtime / "us-receipt.json"),
                    "broker": "kis-live",
                    "mode": "shadow",
                    "venue_map": "",
                    "max_cycles": 1,
                    "timeout_start_seconds": 3900,
                },
            ],
        }
        bundle = normalize_bundle(raw)
        receipt = generate(bundle, output)
        if len(receipt["unit_files"]) != 6:
            raise AssertionError("systemd self-test unit count mismatch")
        service = (output / "qta-entry-us.service").read_text()
        timer = (output / "qta-entry-us.timer").read_text()
        if "ProtectSystem=strict" not in service or "live" in service:
            raise AssertionError("systemd self-test hardening failed")
        if (
            f"WorkingDirectory={systemd_path(bundle['runtime_directory'])}\n"
            not in service
            or f"EnvironmentFile={systemd_path(bundle['environment_file'])}\n"
            not in service
            or "UnsetEnvironment=PYTHONPATH PYTHONHOME\n" not in service
        ):
            raise AssertionError("systemd self-test path rendering failed")
        if "09:29:00 America/New_York" not in timer or "Persistent=no" not in timer:
            raise AssertionError("systemd self-test timer failed")
        if read_bundle(output / "systemd-bundle.json") != bundle:
            raise AssertionError("generated bundle is not executable by the reader")
        print(
            json.dumps(
                {
                    "self_test": "PASS",
                    "unit_files": 6,
                    "activation_performed": False,
                    "live_enabled": False,
                },
                sort_keys=True,
            )
        )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    generate_parser = subparsers.add_parser("generate")
    generate_parser.add_argument("--bundle", required=True)
    generate_parser.add_argument("--output-directory", required=True)
    execute_parser = subparsers.add_parser("execute")
    execute_parser.add_argument("--bundle", required=True)
    execute_parser.add_argument("--name", required=True)
    subparsers.add_parser("self-test")
    return parser.parse_args()


def read_bundle(path: Path) -> dict[str, Any]:
    regular_nonsymlink(path, "bundle")
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise UnitBlockedError("bundle is not valid JSON") from exc
    return normalize_bundle(raw)


def main() -> int:
    args = parse_args()
    try:
        if args.command == "self-test":
            self_test()
            return 0
        bundle_path = absolute_path(args.bundle, "bundle")
        bundle = read_bundle(bundle_path)
        if args.command == "generate":
            receipt = generate(
                bundle, absolute_path(args.output_directory, "output_directory")
            )
            print(json.dumps(receipt, sort_keys=True))
            return 0
        execute(bundle, args.name)
    except (UnitBlockedError, OSError) as exc:
        print(
            json.dumps(
                {"status": "BLOCKED", "reason": str(exc)},
                ensure_ascii=False,
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

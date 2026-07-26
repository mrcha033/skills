#!/usr/bin/env python3
"""Load and configure KIS credentials without exposing their values."""

from __future__ import annotations

import argparse
import getpass
import json
import os
import re
import secrets
import stat
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from unittest.mock import patch

from broker_adapters import KisBroker, TransportFailure
from execution_core import BlockedError, canonical_json

STATUS_SCHEMA = "qta-broker-credential-status/v1"
DEFAULT_SECRETS_PATH = Path("~/.config/mrcha-skills/secrets.env").expanduser()
SECRETS_PATH_ENV = "QTA_SECRETS_FILE"
ACCOUNT_BINDING_KEY = "QTA_ACCOUNT_BINDING_KEY"
FIELD_SUFFIXES = {
    "app_key": "APP_KEY",
    "app_secret": "APP_SECRET",
    "account_prefix": "ACCOUNT_PREFIX",
    "account_product": "ACCOUNT_PRODUCT",
}
GENERIC_NAMES = {
    field: f"QTA_KIS_{suffix}" for field, suffix in FIELD_SUFFIXES.items()
}
DOTENV_KEY = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_INJECTED_ENVIRONMENT: dict[str, str] = {}


def credential_scope(environment: str) -> str:
    normalized = environment.strip().lower()
    if normalized == "paper":
        return "PAPER"
    if normalized in {"shadow", "live"}:
        return "LIVE"
    raise BlockedError("KIS credential environment must be paper, shadow, or live")


def resolve_secrets_path(path: str | Path | None = None) -> Path:
    candidate = path if path is not None else os.environ.get(SECRETS_PATH_ENV)
    return Path(candidate).expanduser() if candidate else DEFAULT_SECRETS_PATH


def inspect_secrets_file(path: Path) -> dict[str, Any]:
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        return {
            "path": str(path),
            "exists": False,
            "secure": None,
            "mode": None,
        }
    if stat.S_ISLNK(metadata.st_mode):
        raise BlockedError(f"credential file must not be a symlink: {path}")
    if not stat.S_ISREG(metadata.st_mode):
        raise BlockedError(f"credential path must be a regular file: {path}")
    if metadata.st_uid != os.getuid():
        raise BlockedError(f"credential file must be owned by the current user: {path}")
    mode = stat.S_IMODE(metadata.st_mode)
    if mode != 0o600:
        raise BlockedError(
            f"credential file permissions must be 0600, found {mode:04o}: {path}"
        )
    return {
        "path": str(path),
        "exists": True,
        "secure": True,
        "mode": "0600",
    }


def parse_dotenv_value(raw: str, *, line_number: int) -> str:
    value = raw.strip()
    if not value:
        return ""
    if value.startswith('"'):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError as exc:
            raise BlockedError(
                f"invalid double-quoted dotenv value on line {line_number}"
            ) from exc
        if not isinstance(parsed, str):
            raise BlockedError(f"dotenv value on line {line_number} must be a string")
        return parsed
    if value.startswith("'"):
        if len(value) < 2 or not value.endswith("'"):
            raise BlockedError(
                f"invalid single-quoted dotenv value on line {line_number}"
            )
        return value[1:-1].replace("\\'", "'").replace("\\\\", "\\")
    return value


def parse_dotenv(path: Path) -> dict[str, str]:
    inspection = inspect_secrets_file(path)
    if not inspection["exists"]:
        return {}
    values: dict[str, str] = {}
    for line_number, raw_line in enumerate(
        path.read_text(encoding="utf-8").splitlines(), start=1
    ):
        stripped = raw_line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if stripped.startswith("export "):
            stripped = stripped[7:].lstrip()
        if "=" not in stripped:
            raise BlockedError(f"invalid dotenv assignment on line {line_number}")
        key, raw_value = stripped.split("=", 1)
        key = key.strip()
        if not DOTENV_KEY.fullmatch(key):
            raise BlockedError(f"invalid dotenv key on line {line_number}")
        if key in values:
            raise BlockedError(f"duplicate dotenv key on line {line_number}: {key}")
        values[key] = parse_dotenv_value(raw_value, line_number=line_number)
    return values


def scoped_name(scope: str, field: str) -> str:
    return f"QTA_KIS_{scope}_{FIELD_SUFFIXES[field]}"


def choose_value(
    *,
    generic_name: str,
    scoped_variable: str,
    dotenv_values: dict[str, str],
) -> tuple[str | None, str | None]:
    candidates = (
        (os.environ.get(generic_name), f"environment:{generic_name}"),
        (os.environ.get(scoped_variable), f"environment:{scoped_variable}"),
        (dotenv_values.get(scoped_variable), f"dotenv:{scoped_variable}"),
        (dotenv_values.get(generic_name), f"dotenv:{generic_name}"),
    )
    for value, source in candidates:
        if value:
            return value, source
    return None, None


def clear_injected_environment() -> None:
    for name, injected_value in tuple(_INJECTED_ENVIRONMENT.items()):
        if os.environ.get(name) == injected_value:
            os.environ.pop(name, None)
        _INJECTED_ENVIRONMENT.pop(name, None)


def load_kis_credentials(
    environment: str,
    *,
    secrets_path: str | Path | None = None,
) -> dict[str, Any]:
    clear_injected_environment()
    scope = credential_scope(environment)
    path = resolve_secrets_path(secrets_path)
    inspection = inspect_secrets_file(path)
    dotenv_values = parse_dotenv(path) if inspection["exists"] else {}
    sources: dict[str, str] = {}
    present: list[str] = []
    missing: list[str] = []

    for field, generic_name in GENERIC_NAMES.items():
        value, source = choose_value(
            generic_name=generic_name,
            scoped_variable=scoped_name(scope, field),
            dotenv_values=dotenv_values,
        )
        if value is None or source is None:
            missing.append(generic_name)
            continue
        os.environ[generic_name] = value
        if source != f"environment:{generic_name}":
            _INJECTED_ENVIRONMENT[generic_name] = value
        present.append(generic_name)
        sources[generic_name] = source

    binding_value, binding_source = choose_value(
        generic_name=ACCOUNT_BINDING_KEY,
        scoped_variable=ACCOUNT_BINDING_KEY,
        dotenv_values=dotenv_values,
    )
    if binding_value and binding_source:
        os.environ[ACCOUNT_BINDING_KEY] = binding_value
        if binding_source != f"environment:{ACCOUNT_BINDING_KEY}":
            _INJECTED_ENVIRONMENT[ACCOUNT_BINDING_KEY] = binding_value
        present.append(ACCOUNT_BINDING_KEY)
        sources[ACCOUNT_BINDING_KEY] = binding_source
    else:
        missing.append(ACCOUNT_BINDING_KEY)

    return {
        "schema": STATUS_SCHEMA,
        "broker": "kis",
        "credential_scope": scope.lower(),
        "status": "READY" if not missing else "BLOCKED",
        "secrets_file": inspection,
        "present": sorted(present),
        "missing": sorted(missing),
        "sources": dict(sorted(sources.items())),
    }


def require_kis_runtime_credentials(report: dict[str, Any]) -> None:
    if report["missing"]:
        raise BlockedError(
            "missing KIS runtime credentials: " + ", ".join(report["missing"])
        )


def dotenv_quote(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def assignment_key(raw_line: str) -> str | None:
    stripped = raw_line.strip()
    if not stripped or stripped.startswith("#"):
        return None
    if stripped.startswith("export "):
        stripped = stripped[7:].lstrip()
    if "=" not in stripped:
        return None
    key = stripped.split("=", 1)[0].strip()
    return key if DOTENV_KEY.fullmatch(key) else None


def write_dotenv_updates(path: Path, updates: dict[str, str]) -> None:
    inspection = inspect_secrets_file(path)
    existing_lines = (
        path.read_text(encoding="utf-8").splitlines() if inspection["exists"] else []
    )
    output_lines: list[str] = []
    replaced: set[str] = set()
    for raw_line in existing_lines:
        key = assignment_key(raw_line)
        if key not in updates:
            output_lines.append(raw_line)
            continue
        if key not in replaced:
            output_lines.append(f"{key}={dotenv_quote(updates[key])}")
            replaced.add(key)
    if output_lines and output_lines[-1] != "":
        output_lines.append("")
    for key in updates:
        if key not in replaced:
            output_lines.append(f"{key}={dotenv_quote(updates[key])}")

    parent_existed = path.parent.exists()
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    if not parent_existed:
        path.parent.chmod(0o700)
    file_descriptor, temporary_name = tempfile.mkstemp(
        prefix=".secrets-", dir=path.parent
    )
    temporary_path = Path(temporary_name)
    try:
        os.fchmod(file_descriptor, 0o600)
        with os.fdopen(file_descriptor, "w", encoding="utf-8") as handle:
            handle.write("\n".join(output_lines) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, path)
        path.chmod(0o600)
    except BaseException:
        temporary_path.unlink(missing_ok=True)
        raise


def prompt_secret(label: str, *, existing: str | None = None) -> str:
    suffix = " (Enter keeps the stored value)" if existing else ""
    value = getpass.getpass(f"{label}{suffix}: ")
    if value:
        return value
    if existing:
        return existing
    raise BlockedError(f"{label} is required")


def configure_kis(environment: str, path: Path) -> dict[str, Any]:
    scope = credential_scope(environment)
    inspection = inspect_secrets_file(path)
    existing = parse_dotenv(path) if inspection["exists"] else {}
    updates: dict[str, str] = {}
    labels = {
        "app_key": "KIS app key",
        "app_secret": "KIS app secret",
        "account_prefix": "KIS account number (first 8 digits)",
    }
    for field in ("app_key", "app_secret", "account_prefix"):
        name = scoped_name(scope, field)
        updates[name] = prompt_secret(labels[field], existing=existing.get(name))

    product_name = scoped_name(scope, "account_product")
    stored_product = existing.get(product_name)
    product = getpass.getpass(
        "KIS account product (2 digits"
        + (", Enter keeps the stored value" if stored_product else ", Enter uses 01")
        + "): "
    )
    updates[product_name] = product or stored_product or "01"
    if len(updates[scoped_name(scope, "account_prefix")]) != 8 or not updates[
        scoped_name(scope, "account_prefix")
    ].isdigit():
        raise BlockedError("KIS account number prefix must be 8 digits")
    if len(updates[product_name]) != 2 or not updates[product_name].isdigit():
        raise BlockedError("KIS account product must be 2 digits")
    if ACCOUNT_BINDING_KEY in existing:
        updates[ACCOUNT_BINDING_KEY] = existing[ACCOUNT_BINDING_KEY]
    elif not os.environ.get(ACCOUNT_BINDING_KEY):
        updates[ACCOUNT_BINDING_KEY] = secrets.token_urlsafe(32)

    write_dotenv_updates(path, updates)
    report = load_kis_credentials(environment, secrets_path=path)
    require_kis_runtime_credentials(report)
    return {
        **report,
        "configured": True,
        "values_printed": False,
    }


def auth_check(environment: str, path: Path) -> dict[str, Any]:
    report = load_kis_credentials(environment, secrets_path=path)
    require_kis_runtime_credentials(report)
    broker = KisBroker(
        app_key=os.environ["QTA_KIS_APP_KEY"],
        app_secret=os.environ["QTA_KIS_APP_SECRET"],
        account_prefix=os.environ["QTA_KIS_ACCOUNT_PREFIX"],
        account_product=os.environ["QTA_KIS_ACCOUNT_PRODUCT"],
        environment="paper" if credential_scope(environment) == "PAPER" else "live",
        access_token=None,
    )
    token = broker.token()
    if not token:
        raise BlockedError("KIS authentication returned an empty access token")
    return {
        "schema": STATUS_SCHEMA,
        "broker": "kis",
        "credential_scope": report["credential_scope"],
        "status": "AUTHENTICATED",
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "token_received": True,
        "token_persisted": False,
        "secrets_file": report["secrets_file"],
        "sources": report["sources"],
    }


def self_test() -> None:
    with tempfile.TemporaryDirectory(prefix="qta-credentials-") as directory:
        path = Path(directory) / "secrets.env"
        path.write_text(
            "\n".join(
                (
                    'UNRELATED_VALUE="preserved"',
                    'QTA_KIS_PAPER_APP_KEY="fixture-app"',
                    'QTA_KIS_PAPER_APP_SECRET="fixture-secret"',
                    'QTA_KIS_PAPER_ACCOUNT_PREFIX="00000000"',
                    'QTA_KIS_PAPER_ACCOUNT_PRODUCT="01"',
                    'QTA_KIS_LIVE_APP_KEY="fixture-live-app"',
                    'QTA_KIS_LIVE_APP_SECRET="fixture-live-secret"',
                    'QTA_KIS_LIVE_ACCOUNT_PREFIX="11111111"',
                    'QTA_KIS_LIVE_ACCOUNT_PRODUCT="01"',
                    'QTA_ACCOUNT_BINDING_KEY="fixture-account-binding-key-0001"',
                    "",
                )
            ),
            encoding="utf-8",
        )
        path.chmod(0o600)
        with patch.dict(os.environ, {}, clear=True):
            report = load_kis_credentials("paper", secrets_path=path)
            assert report["status"] == "READY"
            assert os.environ["QTA_KIS_APP_KEY"] == "fixture-app"
            rendered = canonical_json(report)
            assert "fixture-app" not in rendered
            assert "fixture-secret" not in rendered
            live_report = load_kis_credentials("live", secrets_path=path)
            assert live_report["status"] == "READY"
            assert os.environ["QTA_KIS_APP_KEY"] == "fixture-live-app"
            assert os.environ["QTA_KIS_ACCOUNT_PREFIX"] == "11111111"
        with patch.dict(
            os.environ,
            {"QTA_KIS_APP_KEY": "environment-app"},
            clear=True,
        ):
            report = load_kis_credentials("paper", secrets_path=path)
            assert report["sources"]["QTA_KIS_APP_KEY"].startswith("environment:")
            assert os.environ["QTA_KIS_APP_KEY"] == "environment-app"
        write_dotenv_updates(
            path,
            {"QTA_KIS_PAPER_ACCOUNT_PRODUCT": "02"},
        )
        assert parse_dotenv(path)["UNRELATED_VALUE"] == "preserved"
        assert parse_dotenv(path)["QTA_KIS_PAPER_ACCOUNT_PRODUCT"] == "02"
        path.chmod(0o644)
        try:
            load_kis_credentials("paper", secrets_path=path)
        except BlockedError as exc:
            assert "0600" in str(exc)
        else:
            raise AssertionError("insecure credential file permissions must be blocked")

        configured_path = Path(directory) / "configured.env"
        with (
            patch.dict(os.environ, {}, clear=True),
            patch.object(
                getpass,
                "getpass",
                side_effect=("new-app", "new-secret", "22222222", "01"),
            ),
        ):
            configured = configure_kis("paper", configured_path)
            assert configured["status"] == "READY"
            assert configured_path.stat().st_mode & 0o777 == 0o600
            configured_rendered = canonical_json(configured)
            assert "new-app" not in configured_rendered
            assert "new-secret" not in configured_rendered
            with patch.object(KisBroker, "token", return_value="sensitive-token"):
                authenticated = auth_check("paper", configured_path)
            authenticated_rendered = canonical_json(authenticated)
            assert authenticated["status"] == "AUTHENTICATED"
            assert "sensitive-token" not in authenticated_rendered
    print(canonical_json({"self_test": "PASS", "secrets_exposed": False}))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command")
    for command in ("status", "configure", "auth-check"):
        subparser = subparsers.add_parser(command)
        subparser.add_argument(
            "--environment",
            required=True,
            choices=("paper", "shadow", "live"),
        )
        subparser.add_argument("--secrets-file")
    subparsers.add_parser("self-test")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.command == "self-test":
        self_test()
        return 0
    if args.command not in {"status", "configure", "auth-check"}:
        print(
            canonical_json(
                {
                    "schema": STATUS_SCHEMA,
                    "status": "BLOCKED",
                    "reason": "choose status, configure, auth-check, or self-test",
                }
            )
        )
        return 2
    path = resolve_secrets_path(args.secrets_file)
    try:
        if args.command == "configure":
            output = configure_kis(args.environment, path)
        elif args.command == "auth-check":
            output = auth_check(args.environment, path)
        else:
            output = load_kis_credentials(args.environment, secrets_path=path)
    except (BlockedError, OSError, TransportFailure, ValueError) as exc:
        output = {
            "schema": STATUS_SCHEMA,
            "broker": "kis",
            "status": "BLOCKED",
            "reason": str(exc),
        }
        print(canonical_json(output))
        return 2
    print(canonical_json(output))
    return 0 if output["status"] in {"READY", "AUTHENTICATED"} else 2


if __name__ == "__main__":
    sys.exit(main())

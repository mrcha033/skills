#!/usr/bin/env python3
"""Offline contract tests for deterministic QTA systemd generation."""

from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = (
    ROOT / "skills" / "quant-stock-polling-trader" / "scripts" / "systemd_units.py"
)
SPEC = importlib.util.spec_from_file_location("qta_systemd_contract", MODULE_PATH)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError("cannot load systemd_units.py")
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


class SystemdUnitTests(unittest.TestCase):
    def test_embedded_generator_contract(self) -> None:
        MODULE.self_test()

    def test_entry_timers_are_not_persistent(self) -> None:
        self.assertEqual(
            MODULE.SCHEDULES[("entry", "KR")],
            "Mon..Fri *-*-* 08:59:00 Asia/Seoul",
        )
        self.assertEqual(
            MODULE.SCHEDULES[("entry", "US")],
            "Mon..Fri *-*-* 09:29:00 America/New_York",
        )
        self.assertEqual(
            MODULE.SCHEDULES[("snapshot", "KR")],
            "Mon..Fri *-*-* 08:50:00 Asia/Seoul",
        )
        self.assertEqual(
            MODULE.SCHEDULES[("snapshot", "US")],
            "Mon..Fri *-*-* 09:20:00 America/New_York",
        )
        self.assertEqual(
            MODULE.SCHEDULES[("daily-prepare", "KR")],
            "Mon..Fri *-*-* 01:00:00 Asia/Seoul",
        )
        self.assertEqual(
            MODULE.SCHEDULES[("daily-prepare", "US")],
            "Mon..Fri *-*-* 01:00:00 America/New_York",
        )
        self.assertEqual(
            MODULE.SCHEDULES[("daily-entry", "US")],
            "Mon..Fri *-*-* 09:29:00 America/New_York",
        )

    def test_systemd_escaping_disables_specifiers(self) -> None:
        self.assertEqual(MODULE.systemd_quote("/tmp/100%"), '"/tmp/100%%"')

    def test_directive_path_escaping_is_unquoted(self) -> None:
        self.assertEqual(
            MODULE.systemd_path("/tmp/qta 100%"),
            r"/tmp/qta\x20100%%",
        )

    def test_ntfy_is_optional_and_secrets_are_not_reported(self) -> None:
        job = {"market": "US", "kind": "daily-entry"}
        self.assertEqual(
            MODULE.publish_ntfy(
                environment={},
                job=job,
                outcome="READY",
                duration_seconds=60,
                details=[],
            ),
            {"channel": "ntfy", "status": "DISABLED"},
        )

        response = mock.MagicMock()
        response.__enter__.return_value.status = 200
        with mock.patch.object(MODULE.urllib.request, "urlopen", return_value=response):
            receipt = MODULE.publish_ntfy(
                environment={
                    "QTA_NTFY_TOPIC_URL": "https://ntfy.example/qta-secret-topic",
                    "QTA_NTFY_TOKEN": "secret-token",
                },
                job=job,
                outcome="READY",
                duration_seconds=60,
                details=["session=2026-07-29", "filled=0"],
            )
        self.assertEqual(receipt, {"channel": "ntfy", "status": "SENT"})
        self.assertNotIn("secret", json.dumps(receipt))

        invalid = MODULE.publish_ntfy(
            environment={
                "QTA_NTFY_TOPIC_URL": "https://user:password@ntfy.example/topic"
            },
            job=job,
            outcome="BLOCKED",
            duration_seconds=0,
            details=[],
        )
        self.assertEqual(invalid["reason"], "INVALID_TOPIC_URL")

    def test_generated_bundle_round_trips_through_execute_reader(self) -> None:
        with tempfile.TemporaryDirectory(prefix="qta-systemd-roundtrip-") as temporary:
            root = Path(temporary)
            technical = root / "technical"
            trader = root / "trader"
            runtime = root / "runtime"
            output = root / "generated"
            for path in (technical / "scripts", trader / "scripts", runtime):
                path.mkdir(parents=True)
            for path in (
                technical / "scripts" / "fetch_kis_kr_eod.py",
                technical / "scripts" / "fetch_kis_us_eod.py",
                trader / "scripts" / "account_snapshot.py",
                trader / "scripts" / "daily_pipeline.py",
                trader / "scripts" / "market_calendar.py",
                trader / "scripts" / "run_session.py",
                trader / "scripts" / "systemd_units.py",
                runtime / "us-eod.json",
            ):
                path.write_text("{}\n", encoding="utf-8")
            marker = runtime / "worker-result.json"
            (technical / "scripts" / "fetch_kis_us_eod.py").write_text(
                "import json, os, sys\n"
                "from pathlib import Path\n"
                f"Path({str(marker)!r}).write_text(json.dumps({{"
                "'argv': sys.argv[1:], "
                "'pythonpath': os.environ.get('PYTHONPATH'), "
                "'pythonhome': os.environ.get('PYTHONHOME')"
                "}), encoding='utf-8')\n",
                encoding="utf-8",
            )
            environment = root / "secrets.env"
            environment.write_text("QTA_TEST=1\n", encoding="utf-8")
            environment.chmod(0o600)
            raw = {
                "schema": MODULE.BUNDLE_SCHEMA,
                "unit_prefix": "qta",
                "python_executable": str(Path(sys.executable).resolve()),
                "technical_skill_root": str(technical),
                "trader_skill_root": str(trader),
                "environment_file": str(environment),
                "runtime_directory": str(runtime),
                "jobs": [
                    {
                        "name": "eod-us",
                        "kind": "eod",
                        "market": "US",
                        "input_path": str(runtime / "us-eod.json"),
                        "plan_path": "",
                        "state_directory": "",
                        "output_path": "",
                        "broker": "",
                        "mode": "",
                        "venue_map": "",
                        "max_cycles": 0,
                        "timeout_start_seconds": 7200,
                    }
                ],
            }
            normalized = MODULE.normalize_bundle(raw)
            MODULE.generate(normalized, output)

            serialized = json.loads(
                (output / "systemd-bundle.json").read_text(encoding="utf-8")
            )
            self.assertEqual(set(serialized["jobs"][0]), MODULE.JOB_FIELDS)
            self.assertNotIn("schedule", serialized["jobs"][0])
            self.assertNotIn("persistent", serialized["jobs"][0])
            self.assertEqual(
                MODULE.read_bundle(output / "systemd-bundle.json"),
                normalized,
            )
            service = (output / "qta-eod-us.service").read_text(encoding="utf-8")
            self.assertIn(
                "UnsetEnvironment=PYTHONPATH PYTHONHOME\n",
                service,
            )
            environment_for_execute = dict(os.environ)
            environment_for_execute["PYTHONPATH"] = "/tmp/qta-test-pythonpath"
            environment_for_execute["PYTHONHOME"] = sys.prefix
            result = subprocess.run(
                [
                    sys.executable,
                    "-B",
                    "-s",
                    str(MODULE_PATH),
                    "execute",
                    "--bundle",
                    str(output / "systemd-bundle.json"),
                    "--name",
                    "eod-us",
                ],
                check=False,
                capture_output=True,
                text=True,
                env=environment_for_execute,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            worker_result = json.loads(marker.read_text(encoding="utf-8"))
            self.assertEqual(
                worker_result["argv"],
                ["collect", "--job", str(runtime / "us-eod.json")],
            )
            self.assertIsNone(worker_result["pythonpath"])
            self.assertIsNone(worker_result["pythonhome"])

    def test_nonempty_output_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory(prefix="qta-systemd-stale-") as temporary:
            output = Path(temporary)
            (output / "stale.timer").write_text("stale\n", encoding="utf-8")
            with self.assertRaisesRegex(
                MODULE.UnitBlockedError,
                "must be empty",
            ):
                MODULE.generate({}, output)

    def test_daily_pipeline_bundle_uses_dynamic_config_and_dependencies(self) -> None:
        with tempfile.TemporaryDirectory(prefix="qta-systemd-daily-") as temporary:
            root = Path(temporary)
            technical = root / "technical"
            trader = root / "trader"
            runtime = root / "runtime"
            output = root / "generated"
            for path in (technical / "scripts", trader / "scripts", runtime):
                path.mkdir(parents=True)
            for path in (
                technical / "scripts" / "fetch_kis_kr_eod.py",
                technical / "scripts" / "fetch_kis_us_eod.py",
                trader / "scripts" / "account_snapshot.py",
                trader / "scripts" / "daily_pipeline.py",
                trader / "scripts" / "market_calendar.py",
                trader / "scripts" / "run_session.py",
                trader / "scripts" / "systemd_units.py",
            ):
                path.write_text("{}\n", encoding="utf-8")
            config = runtime / "daily-config.json"
            config.write_text("{}\n", encoding="utf-8")
            environment = root / "secrets.env"
            environment.write_text("QTA_TEST=1\n", encoding="utf-8")
            environment.chmod(0o600)

            def job(name: str, kind: str, max_cycles: int = 0) -> dict[str, object]:
                return {
                    "name": name,
                    "kind": kind,
                    "market": "US",
                    "input_path": str(config),
                    "plan_path": "",
                    "state_directory": "",
                    "output_path": "",
                    "broker": "kis-live",
                    "mode": "shadow",
                    "venue_map": "",
                    "max_cycles": max_cycles,
                    "timeout_start_seconds": 7200,
                }

            bundle = MODULE.normalize_bundle(
                {
                    "schema": MODULE.BUNDLE_SCHEMA,
                    "unit_prefix": "qta",
                    "python_executable": str(Path(sys.executable).resolve()),
                    "technical_skill_root": str(technical),
                    "trader_skill_root": str(trader),
                    "environment_file": str(environment),
                    "runtime_directory": str(runtime),
                    "jobs": [
                        job("daily-prepare-us", "daily-prepare"),
                        job("daily-snapshot-us", "daily-snapshot"),
                        job("daily-entry-us", "daily-entry", 3),
                    ],
                }
            )
            MODULE.generate(bundle, output)
            entry = next(
                item for item in bundle["jobs"] if item["kind"] == "daily-entry"
            )
            command = MODULE.command_for(bundle, entry)
            self.assertIn("daily_pipeline.py", command[3])
            self.assertEqual(command[4:8], ["entry", "--config", str(config), "--market"])
            self.assertEqual(command[-2:], ["--max-cycles", "3"])
            service = (output / "qta-daily-entry-us.service").read_text(
                encoding="utf-8"
            )
            self.assertIn(
                "After=network-online.target qta-daily-snapshot-us.service",
                service,
            )
            prepare_timer = (
                output / "qta-daily-prepare-us.timer"
            ).read_text(encoding="utf-8")
            entry_timer = (output / "qta-daily-entry-us.timer").read_text(
                encoding="utf-8"
            )
            self.assertIn("Persistent=yes", prepare_timer)
            self.assertIn("Persistent=no", entry_timer)

    def test_symlink_lock_directory_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory(prefix="qta-systemd-lock-") as temporary:
            root = Path(temporary)
            technical = root / "technical"
            trader = root / "trader"
            runtime = root / "runtime"
            outside = root / "outside-locks"
            for path in (
                technical / "scripts",
                trader / "scripts",
                runtime,
                outside,
            ):
                path.mkdir(parents=True)
            for path in (
                technical / "scripts" / "fetch_kis_kr_eod.py",
                technical / "scripts" / "fetch_kis_us_eod.py",
                trader / "scripts" / "account_snapshot.py",
                trader / "scripts" / "daily_pipeline.py",
                trader / "scripts" / "market_calendar.py",
                trader / "scripts" / "run_session.py",
                trader / "scripts" / "systemd_units.py",
                runtime / "kr-eod.json",
            ):
                path.write_text("{}\n", encoding="utf-8")
            environment = root / "secrets.env"
            environment.write_text("QTA_TEST=1\n", encoding="utf-8")
            environment.chmod(0o600)
            (runtime / "locks").symlink_to(outside, target_is_directory=True)
            bundle = MODULE.normalize_bundle(
                {
                    "schema": MODULE.BUNDLE_SCHEMA,
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
                            "state_directory": "",
                            "output_path": "",
                            "broker": "",
                            "mode": "",
                            "venue_map": "",
                            "max_cycles": 0,
                            "timeout_start_seconds": 7200,
                        }
                    ],
                }
            )
            with self.assertRaisesRegex(
                MODULE.UnitBlockedError,
                "lock directory must be a non-symlink directory",
            ):
                MODULE.execute(bundle, "eod-kr")

    @unittest.skipUnless(shutil.which("systemd-analyze"), "systemd-analyze unavailable")
    def test_generated_units_pass_systemd_analyze(self) -> None:
        with tempfile.TemporaryDirectory(prefix="qta-systemd-test-") as temporary:
            root = Path(temporary) / "qta 100%"
            technical = root / "technical"
            trader = root / "trader"
            runtime = root / "runtime"
            output = runtime / "generated"
            for path in (technical / "scripts", trader / "scripts", runtime):
                path.mkdir(parents=True)
            for path in (
                technical / "scripts" / "fetch_kis_kr_eod.py",
                technical / "scripts" / "fetch_kis_us_eod.py",
                trader / "scripts" / "account_snapshot.py",
                trader / "scripts" / "daily_pipeline.py",
                trader / "scripts" / "market_calendar.py",
                trader / "scripts" / "run_session.py",
                trader / "scripts" / "systemd_units.py",
                runtime / "kr-eod.json",
            ):
                path.write_text("{}\n", encoding="utf-8")
            environment = root / "secrets.env"
            environment.write_text("QTA_TEST=1\n", encoding="utf-8")
            environment.chmod(0o600)
            bundle = MODULE.normalize_bundle(
                {
                    "schema": MODULE.BUNDLE_SCHEMA,
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
                            "state_directory": "",
                            "output_path": "",
                            "broker": "",
                            "mode": "",
                            "venue_map": "",
                            "max_cycles": 0,
                            "timeout_start_seconds": 7200,
                        }
                    ],
                }
            )
            receipt = MODULE.generate(bundle, output)
            result = subprocess.run(
                [
                    shutil.which("systemd-analyze"),
                    "--user",
                    "verify",
                    *(item["path"] for item in receipt["unit_files"]),
                ],
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()

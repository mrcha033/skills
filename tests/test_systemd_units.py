#!/usr/bin/env python3
"""Offline contract tests for deterministic QTA systemd generation."""

from __future__ import annotations

import importlib.util
import shutil
import subprocess
import sys
import tempfile
import unittest
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

    def test_systemd_escaping_disables_specifiers(self) -> None:
        self.assertEqual(MODULE.systemd_quote("/tmp/100%"), '"/tmp/100%%"')

    def test_directive_path_escaping_is_unquoted(self) -> None:
        self.assertEqual(
            MODULE.systemd_path("/tmp/qta 100%"),
            r"/tmp/qta\x20100%%",
        )

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
                            "arm_path": "",
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

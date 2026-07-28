#!/usr/bin/env python3
"""Offline regression tests for the recurring daily shadow pipeline."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = (
    ROOT
    / "skills"
    / "quant-stock-polling-trader"
    / "scripts"
    / "daily_pipeline.py"
)


class DailyPipelineTests(unittest.TestCase):
    def test_embedded_self_test(self) -> None:
        result = subprocess.run(
            [sys.executable, "-B", "-s", str(SCRIPT), "self-test"],
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["self_test"], "PASS")

    def test_blocked_stage_is_persisted_as_current_terminal_state(self) -> None:
        with tempfile.TemporaryDirectory(prefix="qta-daily-blocked-") as temporary:
            root = Path(temporary)
            runtime = root / "runtime"
            config = root / "invalid-config.json"
            config.write_text(
                json.dumps({"runtime_root": str(runtime)}),
                encoding="utf-8",
            )
            environment = dict(os.environ)
            environment["PYTHONDONTWRITEBYTECODE"] = "1"
            environment.pop("PYTHONPATH", None)
            environment.pop("PYTHONHOME", None)
            command = [
                sys.executable,
                "-B",
                "-s",
                str(SCRIPT),
                "prepare",
                "--config",
                str(config),
                "--market",
                "KR",
                "--technical-skill-root",
                str(root / "technical"),
                "--trader-skill-root",
                str(root / "trader"),
            ]
            first = subprocess.run(
                command,
                check=False,
                capture_output=True,
                text=True,
                env=environment,
            )
            self.assertEqual(first.returncode, 2)
            session = datetime.now(ZoneInfo("Asia/Seoul")).date().isoformat()
            descriptor_path = runtime / "current" / "kr.json"
            descriptor = json.loads(descriptor_path.read_text(encoding="utf-8"))
            self.assertEqual(descriptor["status"], "BLOCKED")
            self.assertEqual(descriptor["blocked_stage"], "prepare")
            self.assertEqual(descriptor["session_date"], session)
            self.assertFalse(descriptor["live_enabled"])
            self.assertEqual(descriptor["api_mutation_count"], 0)
            receipt_path = (
                runtime
                / "workflows"
                / "kr"
                / session
                / "prepare-receipt.json"
            )
            self.assertEqual(
                json.loads(receipt_path.read_text(encoding="utf-8"))["status"],
                "BLOCKED",
            )


if __name__ == "__main__":
    unittest.main()

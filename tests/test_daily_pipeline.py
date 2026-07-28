#!/usr/bin/env python3
"""Offline regression tests for the recurring daily shadow pipeline."""

from __future__ import annotations

import json
import importlib.util
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
sys.path.insert(0, str(SCRIPT.parent))
SPEC = importlib.util.spec_from_file_location("daily_pipeline_contract", SCRIPT)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError(f"cannot load {SCRIPT}")
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


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

    def test_same_analysis_date_ready_cache_is_seeded(self) -> None:
        with tempfile.TemporaryDirectory(prefix="qta-daily-cache-") as temporary:
            runtime = Path(temporary)
            ready_root = runtime / "eod" / "2026-07-27" / "approved-a" / "kr"
            blocked_root = runtime / "eod" / "2026-07-27" / "approved-z" / "kr"
            target_root = runtime / "eod" / "2026-07-27" / "current" / "kr"
            for candidate_root, status in (
                (ready_root, "READY"),
                (blocked_root, "BLOCKED"),
            ):
                (candidate_root / "stocks" / "KOSPI").mkdir(parents=True)
                (candidate_root / "benchmarks").mkdir()
                (candidate_root / "stocks" / "KOSPI" / "005930.csv").write_text(
                    f"{status}\n",
                    encoding="utf-8",
                )
                (candidate_root / "benchmarks" / "KOSPI_COMPOSITE.csv").write_text(
                    f"{status}\n",
                    encoding="utf-8",
                )
                (candidate_root / "eod-catalog.csv").write_text(
                    "catalog\n",
                    encoding="utf-8",
                )
                (candidate_root / "eod-bundle-receipt.json").write_text(
                    json.dumps(
                        {
                            "status": status,
                            "analysis_date": "2026-07-27",
                            "api_mutation_count": 0,
                        }
                    ),
                    encoding="utf-8",
                )

            MODULE.seed_eod_cache(
                runtime,
                target_root,
                "2026-07-27",
                "kr",
            )

            self.assertEqual(
                (target_root / "stocks" / "KOSPI" / "005930.csv").read_text(
                    encoding="utf-8"
                ),
                "READY\n",
            )
            self.assertEqual(
                (
                    target_root / "benchmarks" / "KOSPI_COMPOSITE.csv"
                ).read_text(encoding="utf-8"),
                "READY\n",
            )


if __name__ == "__main__":
    unittest.main()

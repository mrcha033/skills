#!/usr/bin/env python3
"""Offline contract tests for deterministic QTA systemd generation."""

from __future__ import annotations

import importlib.util
import sys
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

    def test_systemd_escaping_disables_specifiers(self) -> None:
        self.assertEqual(MODULE.systemd_quote("/tmp/100%"), '"/tmp/100%%"')


if __name__ == "__main__":
    unittest.main()

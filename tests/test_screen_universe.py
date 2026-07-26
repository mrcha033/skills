"""Focused contract tests for deterministic universe screening."""

from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "skills" / "quant-stock-technical" / "scripts"
MODULE_PATH = SCRIPTS / "screen_universe.py"
sys.path.insert(0, str(SCRIPTS))
SPEC = importlib.util.spec_from_file_location("screen_universe_contract", MODULE_PATH)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError(f"cannot load {MODULE_PATH}")
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


class ScreenUniverseContractTests(unittest.TestCase):
    def test_catalog_coverage_contract_requires_both_ratio_maps(self) -> None:
        self.assertTrue(MODULE.meets_minimum_coverage_ratio(1, 2, "0.5"))
        self.assertFalse(MODULE.meets_minimum_coverage_ratio(1, 2, "0.5001"))
        self.assertFalse(MODULE.meets_minimum_coverage_ratio(0, 0, "0.1"))
        contract = MODULE.normalize_catalog_coverage_contract(
            {
                "schema": MODULE.CATALOG_COVERAGE_SCHEMA,
                "minimum_ratio_by_exchange": {
                    exchange: "1.00" for exchange in MODULE.EXCHANGE_ORDER
                },
                "minimum_screenable_ratio_by_exchange": {
                    exchange: "0.500" for exchange in MODULE.EXCHANGE_ORDER
                },
            }
        )
        self.assertEqual(
            contract["minimum_ratio_by_exchange"],
            {exchange: "1" for exchange in MODULE.EXCHANGE_ORDER},
        )
        self.assertEqual(
            contract["minimum_screenable_ratio_by_exchange"],
            {exchange: "0.5" for exchange in MODULE.EXCHANGE_ORDER},
        )
        with self.assertRaisesRegex(
            MODULE.ScreenBlockedError,
            "fields must be exactly",
        ):
            MODULE.normalize_catalog_coverage_contract(
                {
                    "schema": MODULE.CATALOG_COVERAGE_SCHEMA,
                    "minimum_ratio_by_exchange": {
                        exchange: "1" for exchange in MODULE.EXCHANGE_ORDER
                    },
                }
            )

    def test_v1_selector_allows_only_known_setup_statuses(self) -> None:
        selector = {
            "selector_version": MODULE.SELECTOR_VERSION,
            "min_total_score": "60",
            "eligible_setup_statuses": ["READY", "CONDITIONAL", "READY"],
            "top_k_by_market": {"KR": 1, "US": 1},
            "max_blocked_fraction": "0",
        }
        normalized = MODULE.normalize_selector_v1(selector)
        self.assertEqual(
            normalized["eligible_setup_statuses"],
            ["CONDITIONAL", "READY"],
        )
        with self.assertRaisesRegex(
            MODULE.ScreenBlockedError,
            "subset of READY and CONDITIONAL",
        ):
            MODULE.normalize_selector_v1(
                {**selector, "eligible_setup_statuses": ["READY", "BLOCKED"]}
            )

    def test_v2_selector_allows_only_known_setup_statuses(self) -> None:
        selector = {
            "selector_version": MODULE.SELECTOR_VERSION_V2,
            "min_total_score": "60",
            "eligible_setup_statuses": ["CONDITIONAL"],
            "top_k_by_exchange": {exchange: 1 for exchange in MODULE.EXCHANGE_ORDER},
            "min_selected_by_exchange": {
                exchange: 0 for exchange in MODULE.EXCHANGE_ORDER
            },
            "max_blocked_fraction": "0",
        }
        normalized = MODULE.normalize_selector_v2(selector)
        self.assertEqual(
            normalized["eligible_setup_statuses"],
            ["CONDITIONAL"],
        )
        with self.assertRaisesRegex(
            MODULE.ScreenBlockedError,
            "subset of READY and CONDITIONAL",
        ):
            MODULE.normalize_selector_v2(
                {**selector, "eligible_setup_statuses": ["conditional"]}
            )


if __name__ == "__main__":
    unittest.main()

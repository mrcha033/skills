#!/usr/bin/env python3
"""Regression tests for the QTA 2.0 shadow execution contract."""

from __future__ import annotations

import sys
import unittest
from copy import deepcopy
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "skills" / "quant-stock-polling-trader" / "scripts"
sys.path.insert(0, str(SCRIPTS))

import execution_core
import run_session
from tests.test_quant_execution import (
    complete_v2_screen,
    rehash_screen,
    v2_selected_item,
)


def qta2_screen() -> dict[str, object]:
    selected = {
        "KOSPI": [
            v2_selected_item(
                "KOSPI",
                1,
                "005930",
                "005930",
                "005930",
            )
        ],
        "KOSDAQ": [],
        "NYSE": [],
        "NASDAQ": [],
    }
    screen = complete_v2_screen(execution_core, selected)
    screen["method_version"] = execution_core.QTA_METHOD_V2
    payloads = [
        selected["KOSPI"][0]["qta"],
        screen["decisions"][0]["qta"],
    ]
    for payload in payloads:
        payload.update(
            {
                "method_version": execution_core.QTA_METHOD_V2,
                "validation_status": "RESEARCH_ONLY",
                "score_basis": execution_core.QTA2_SCORE_BASIS,
                "liquidity": {
                    "median_20_session_turnover": 2_000_000_000.0,
                    "minimum_turnover": 1_000_000_000.0,
                    "currency": "KRW",
                    "status": "READY",
                },
                "market_regime": {
                    "metric": execution_core.QTA2_REGIME_METRIC,
                    "minimum_change_bps": 0.0,
                    "max_age_seconds": (
                        execution_core.QTA2_REGIME_MAX_AGE_SECONDS
                    ),
                    "status": "REQUIRED",
                },
                "assumptions": list(execution_core.QTA2_ASSUMPTIONS),
            }
        )
    screen["selected"]["KOSPI"][0]["qta"] = deepcopy(payloads[0])
    rehash_screen(execution_core, screen)
    return screen


class Qta2ExecutionTests(unittest.TestCase):
    def test_qta2_screen_is_valid_and_shadow_only(self) -> None:
        screen = qta2_screen()
        execution_core.validate_screen(screen)
        plan = {
            "context": {"broker": "kis", "environment": "shadow"},
            "frozen_inputs": {"screen": screen},
        }
        run_session.validate_mode(plan, "kis-live", "shadow")
        with self.assertRaisesRegex(
            execution_core.BlockedError, "RESEARCH_ONLY"
        ):
            paper_plan = {
                "context": {"broker": "kis", "environment": "paper"},
                "frozen_inputs": {"screen": screen},
            }
            run_session.validate_mode(paper_plan, "kis-paper", "paper")

    def test_qta2_formula_tamper_is_rejected(self) -> None:
        screen = qta2_screen()
        screen["selected"]["KOSPI"][0]["qta"]["total_score"] = 71.0
        screen["decisions"][0]["qta"]["total_score"] = 71.0
        rehash_screen(execution_core, screen)
        with self.assertRaisesRegex(
            execution_core.BlockedError,
            "qta-2.0.0 score formula",
        ):
            execution_core.validate_screen(screen)

    def test_qta2_liquidity_status_tamper_is_rejected(self) -> None:
        screen = qta2_screen()
        for payload in (
            screen["selected"]["KOSPI"][0]["qta"],
            screen["decisions"][0]["qta"],
        ):
            payload["liquidity"]["status"] = "BLOCKED"
        rehash_screen(execution_core, screen)
        with self.assertRaisesRegex(
            execution_core.BlockedError,
            "liquidity.status",
        ):
            execution_core.validate_screen(screen)

    def test_qta2_market_regime_tamper_is_rejected(self) -> None:
        screen = qta2_screen()
        for payload in (
            screen["selected"]["KOSPI"][0]["qta"],
            screen["decisions"][0]["qta"],
        ):
            payload["market_regime"]["minimum_change_bps"] = -50.0
        rehash_screen(execution_core, screen)
        with self.assertRaisesRegex(
            execution_core.BlockedError,
            "minimum_change_bps",
        ):
            execution_core.validate_screen(screen)

    def test_screen_and_nested_qta_methods_must_match(self) -> None:
        screen = qta2_screen()
        screen["method_version"] = execution_core.QTA_METHOD_V1
        rehash_screen(execution_core, screen)
        with self.assertRaisesRegex(
            execution_core.BlockedError,
            "method differs",
        ):
            execution_core.validate_screen(screen)


if __name__ == "__main__":
    unittest.main()

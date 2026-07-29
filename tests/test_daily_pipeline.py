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
from unittest.mock import patch
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

    def test_v2_config_needs_no_approval_or_external_broker_files(self) -> None:
        with tempfile.TemporaryDirectory(prefix="qta-daily-config-v2-") as temporary:
            root = Path(temporary)
            risk_path = (
                ROOT
                / "skills"
                / "quant-stock-polling-trader"
                / "references"
                / "fixtures"
                / "aggressive-2m-risk-policy.json"
            )
            raw = {
                "schema": MODULE.CONFIG_SCHEMA,
                "runtime_root": str(root / "runtime"),
                "broker": "kis-live",
                "mode": "shadow",
                "history_start_date": "2021-01-01",
                "minimum_sessions": 756,
                "request_interval_ms": 120,
                "catalog_coverage_contract": {},
                "selector": {"selector_version": "qta-screen-1.1.0"},
                "risk_policy_path": str(risk_path),
                "execution_policy": {
                    "snapshot_max_age_seconds": 1800,
                    "poll_interval_seconds": 2,
                    "quote_max_age_seconds": 5,
                    "max_spread_bps": "25",
                    "max_gap_bps": "20",
                    "trigger_mode": "CROSS_FROM_BELOW",
                    "order_ttl_seconds": 30,
                    "order_type": "LIMIT",
                    "time_in_force": "DAY",
                    "allow_partial_fill": True,
                    "cancel_remainder_at_window_end": True,
                },
                "account_aliases": {
                    "KR": "kis-live-kr",
                    "US": "kis-live-us",
                },
                "prepare_complete_seconds_before_open": 600,
            }
            normalized = MODULE.normalize_config(raw, root / "daily.json")
            self.assertEqual(normalized["exposure_component_paths"], [])
            self.assertNotIn("approved_trader_tree_sha256", normalized)
            self.assertNotIn("required_manual_exposure_brokers", normalized)

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
                / "prepare-status.json"
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

    def test_cached_eod_build_spec_rebinds_current_coverage_policy(self) -> None:
        with tempfile.TemporaryDirectory(prefix="qta-daily-build-spec-") as temporary:
            root = Path(temporary)
            us_output = root / "eod" / "us"
            workflow = root / "workflow"
            source_path = us_output / "universe-build-spec.json"
            original_contract = {
                "schema": "qta-catalog-coverage-contract/v1",
                "minimum_ratio_by_exchange": {
                    exchange: "1"
                    for exchange in ("KOSPI", "KOSDAQ", "NYSE", "NASDAQ")
                },
                "minimum_screenable_ratio_by_exchange": {
                    exchange: "0.5"
                    for exchange in ("KOSPI", "KOSDAQ", "NYSE", "NASDAQ")
                },
            }
            current_contract = json.loads(json.dumps(original_contract))
            current_contract["minimum_screenable_ratio_by_exchange"][
                "NASDAQ"
            ] = "0.35"
            source = {
                "schema": "qta-universe-build-spec/v1",
                "as_of": "2026-07-28",
                "analysis_date": "2026-07-27",
                "official_sources": [
                    {"exchange": "NYSE"},
                    {"exchange": "NASDAQ"},
                ],
                "broker_sources": [
                    {"exchange": "NYSE"},
                    {"exchange": "NASDAQ"},
                ],
                "eod_catalog": {},
                "catalog_coverage_contract": original_contract,
            }
            MODULE.atomic_write_json(source_path, source)
            MODULE.atomic_write_json(
                us_output / "eod-bundle-receipt.json",
                {
                    "build_spec": {
                        "path": str(source_path.resolve()),
                        "sha256": MODULE.sha256_file(source_path),
                    }
                },
            )

            derived_path = MODULE.derive_universe_build_spec(
                config={"catalog_coverage_contract": current_contract},
                eod_output=us_output,
                workflow=workflow,
                market="US",
            )

            self.assertEqual(
                json.loads(derived_path.read_text(encoding="utf-8"))[
                    "catalog_coverage_contract"
                ],
                current_contract,
            )
            self.assertEqual(
                json.loads(source_path.read_text(encoding="utf-8"))[
                    "catalog_coverage_contract"
                ],
                original_contract,
            )
            source["official_sources"].append({"exchange": "KOSPI"})
            MODULE.atomic_write_json(source_path, source)
            MODULE.atomic_write_json(
                us_output / "eod-bundle-receipt.json",
                {
                    "build_spec": {
                        "path": str(source_path.resolve()),
                        "sha256": MODULE.sha256_file(source_path),
                    }
                },
            )
            with self.assertRaisesRegex(
                MODULE.PipelineBlockedError,
                "official_sources must contain exactly",
            ):
                MODULE.derive_universe_build_spec(
                    config={"catalog_coverage_contract": current_contract},
                    eod_output=us_output,
                    workflow=workflow,
                    market="US",
                )

    def test_eod_job_uses_only_the_selected_market(self) -> None:
        with tempfile.TemporaryDirectory(prefix="qta-daily-market-eod-") as temporary:
            root = Path(temporary)
            technical = root / "technical"
            scripts = technical / "scripts"
            scripts.mkdir(parents=True)
            for name in ("fetch_kis_kr_eod.py", "fetch_kis_us_eod.py"):
                (scripts / name).write_text("# fixture\n", encoding="utf-8")
            config = {
                "runtime_root": root / "runtime",
                "history_start_date": "2021-01-01",
                "minimum_sessions": 756,
                "request_interval_ms": 120,
                "catalog_coverage_contract": {"schema": "fixture"},
            }

            for market, exchanges in (
                ("KR", {"KOSPI", "KOSDAQ"}),
                ("US", {"NYSE", "NASDAQ"}),
            ):
                sources = {
                    "official_sources": [
                        {"exchange": exchange} for exchange in sorted(exchanges)
                    ],
                    "broker_sources": [
                        {"exchange": exchange} for exchange in sorted(exchanges)
                    ],
                }

                def complete_job(command: list[str], label: str) -> None:
                    self.assertEqual(label, f"KIS {market} adjusted EOD")
                    job = json.loads(Path(command[-1]).read_text(encoding="utf-8"))
                    self.assertEqual(
                        {item["exchange"] for item in job["official_sources"]},
                        exchanges,
                    )
                    self.assertEqual(
                        {item["exchange"] for item in job["broker_sources"]},
                        exchanges,
                    )
                    self.assertEqual(job["base_eod_catalog"], "")
                    output = Path(job["output_directory"])
                    output.mkdir(parents=True, exist_ok=True)
                    MODULE.atomic_write_json(
                        output / "eod-bundle-receipt.json",
                        {
                            "status": "READY",
                            "analysis_date": "2026-07-28",
                            "api_mutation_count": 0,
                        },
                    )

                with patch.object(MODULE, "run_command", side_effect=complete_job):
                    output = MODULE.ensure_eod(
                        config=config,
                        technical_root=technical,
                        workflow=root / "workflow" / market.lower(),
                        as_of="2026-07-29",
                        analysis_date="2026-07-28",
                        market=market,
                        sources=sources,
                        cache_namespace=f"test-{market.lower()}",
                    )
                self.assertEqual(output.name, market.lower())

    def test_legacy_harness_descriptor_does_not_block_v2_prepare(self) -> None:
        with tempfile.TemporaryDirectory(prefix="qta-daily-v2-") as temporary:
            runtime = Path(temporary)
            session = "2026-07-28"
            workflow = runtime / "workflows" / "us" / session
            current = runtime / "current" / "us.json"
            workflow.mkdir(parents=True)
            current.parent.mkdir(parents=True)
            MODULE.atomic_write_json(
                current,
                {
                    "schema": "qta-daily-shadow-descriptor/v1",
                    "market": "US",
                    "session_date": session,
                    "status": "PREPARED",
                },
            )

            with (
                patch.object(MODULE, "current_market_date", return_value=session),
                patch.object(
                    MODULE.market_calendar,
                    "snapshot",
                    return_value={
                        "status": "MARKET_CLOSED",
                        "session_date": session,
                    },
                ) as calendar_snapshot,
            ):
                receipt = MODULE.prepare(
                    {"runtime_root": runtime},
                    "US",
                    runtime / "technical",
                    runtime / "trader",
                )

            self.assertEqual(receipt["status"], "MARKET_CLOSED")
            calendar_snapshot.assert_called_once()
            self.assertEqual(
                json.loads(current.read_text(encoding="utf-8"))["schema"],
                MODULE.DESCRIPTOR_SCHEMA,
            )


if __name__ == "__main__":
    unittest.main()

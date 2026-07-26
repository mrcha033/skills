#!/usr/bin/env python3
"""Offline contract tests for the read-only KIS Korean EOD collector."""

from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "skills" / "quant-stock-technical" / "scripts"
MODULE_PATH = SCRIPTS / "fetch_kis_kr_eod.py"
sys.path.insert(0, str(SCRIPTS))
SPEC = importlib.util.spec_from_file_location("fetch_kis_kr_eod_contract", MODULE_PATH)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError(f"cannot load {MODULE_PATH}")
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


class KisKrEodTests(unittest.TestCase):
    def test_embedded_pagination_and_csv_round_trip(self) -> None:
        MODULE.self_test()

    def test_adjusted_price_flag_is_fixed(self) -> None:
        self.assertEqual(MODULE.STOCK_TR_ID, "FHKST03010100")
        self.assertEqual(
            MODULE.STOCK_PATH,
            "/uapi/domestic-stock/v1/quotations/inquire-daily-itemchartprice",
        )
        row = MODULE.normalize_stock_row(
            {
                "stck_bsop_date": "20260724",
                "stck_oprc": "1000",
                "stck_hgpr": "1100",
                "stck_lwpr": "900",
                "stck_clpr": "1050",
                "acml_vol": "123456",
            }
        )
        self.assertEqual(row["adjusted_close"], row["close"])

    def test_kospi_and_kosdaq_tick_ladders_remain_distinct(self) -> None:
        self.assertEqual(MODULE.tick_size("KOSPI", Decimal("100000")), Decimal("500"))
        self.assertEqual(MODULE.tick_size("KOSDAQ", Decimal("100000")), Decimal("100"))

    def test_history_minimum_cannot_be_weakened(self) -> None:
        with self.assertRaisesRegex(MODULE.EodBlockedError, "at least 756"):
            MODULE.normalize_job(
                {
                    "schema": MODULE.JOB_SCHEMA,
                    "as_of": "2026-07-27",
                    "analysis_date": "2026-07-24",
                    "environment": "live",
                    "output_directory": "output",
                    "history_start_date": "2021-01-01",
                    "minimum_sessions": 755,
                    "request_interval_ms": 120,
                    "official_sources": [],
                    "broker_sources": [],
                    "catalog_coverage_contract": {
                        "schema": MODULE.universe.CATALOG_COVERAGE_SCHEMA,
                        "minimum_ratio_by_exchange": {
                            exchange: "1" for exchange in MODULE.universe.EXCHANGES
                        },
                        "minimum_screenable_ratio_by_exchange": {
                            exchange: "0.5" for exchange in MODULE.universe.EXCHANGES
                        },
                    },
                    "base_eod_catalog": "",
                },
                ROOT,
            )

    def test_token_throttle_retries_once_without_exposing_secrets(self) -> None:
        responses = iter(
            (
                (403, {"error_code": "EGW00133"}),
                (200, {"access_token": "fixture-token"}),
            )
        )

        def transport(method, url, headers, body):
            del method, url, headers, body
            return next(responses)

        client = MODULE.KisReadClient(
            environment="live",
            app_key="fixture-key",
            app_secret="fixture-secret",
            interval_ms=100,
            transport=transport,
        )
        with patch.object(MODULE.time, "sleep") as sleep:
            self.assertEqual(client.token(), "fixture-token")
        sleep.assert_called_once_with(61.0)
        self.assertEqual(client.request_count, 2)
        self.assertEqual(client.retry_count, 1)

    def test_offline_bundle_emits_catalog_spec_and_zero_mutations(self) -> None:
        end = date(2026, 7, 24)
        stock_rows = MODULE._synthetic_rows(end, 800, index=False)
        index_rows = MODULE._synthetic_rows(end, 800, index=True)

        def transport(method, url, headers, body):
            del headers, body
            self.assertEqual(method, "GET")
            query = dict(item.split("=", 1) for item in url.split("?", 1)[1].split("&"))
            cursor = datetime.strptime(query["FID_INPUT_DATE_2"], "%Y%m%d").date()
            source = index_rows if MODULE.INDEX_PATH in url else stock_rows
            page_size = 50 if source is index_rows else 100
            selected = [
                row
                for row in source
                if datetime.strptime(row["stck_bsop_date"], "%Y%m%d").date() <= cursor
            ][:page_size]
            return 200, {"rt_cd": "0", "output2": selected}

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            sources = root / "sources"
            sources.mkdir()
            official_paths = {}
            broker_paths = {}
            for exchange, symbol in (("KOSPI", "005930"), ("KOSDAQ", "035720")):
                official_path = sources / f"official-{exchange}.csv"
                broker_path = sources / f"broker-{exchange}.csv"
                payload = (
                    "symbol,name,instrument_type,status\n"
                    f"{symbol},fixture,COMMON,NORMAL\n"
                )
                official_path.write_text(payload, encoding="utf-8")
                broker_path.write_text(payload, encoding="utf-8")
                official_paths[exchange] = official_path
                broker_paths[exchange] = broker_path

            def descriptor(exchange, path, provider, source_id):
                return {
                    "source_id": source_id,
                    "provider": provider,
                    "exchange": exchange,
                    "as_of": "2026-07-27",
                    "path": str(path),
                    "format": "KRX_CSV" if provider == "KRX" else "KIS_CSV",
                    "encoding": "utf-8",
                    "delimiter": ",",
                    "skip_rows": 0,
                    "columns": {
                        "symbol": "symbol",
                        "name": "name",
                        "instrument_type": "instrument_type",
                        "status": "status",
                    },
                    "normal_status_values": ["NORMAL"],
                }

            official_sources = [
                descriptor(
                    exchange,
                    official_paths[exchange],
                    "KRX",
                    f"official-{exchange}",
                )
                for exchange in MODULE.KR_EXCHANGES
            ]
            broker_sources = [
                descriptor(
                    exchange,
                    broker_paths[exchange],
                    "KIS",
                    f"broker-{exchange}",
                )
                for exchange in MODULE.KR_EXCHANGES
            ]
            job_path = root / "job.json"
            raw_job = {
                "schema": MODULE.JOB_SCHEMA,
                "as_of": "2026-07-27",
                "analysis_date": "2026-07-24",
                "environment": "live",
                "output_directory": str(root / "output"),
                "history_start_date": "2023-01-01",
                "minimum_sessions": 756,
                "request_interval_ms": 100,
                "official_sources": official_sources,
                "broker_sources": broker_sources,
                "catalog_coverage_contract": {
                    "schema": MODULE.universe.CATALOG_COVERAGE_SCHEMA,
                    "minimum_ratio_by_exchange": {
                        exchange: "1" for exchange in MODULE.universe.EXCHANGES
                    },
                    "minimum_screenable_ratio_by_exchange": {
                        exchange: "0.5" for exchange in MODULE.universe.EXCHANGES
                    },
                },
                "base_eod_catalog": "",
            }
            job_path.write_text(json.dumps(raw_job), encoding="utf-8")
            job = MODULE.normalize_job(raw_job, root)
            client = MODULE.KisReadClient(
                environment="live",
                app_key="fixture",
                app_secret="fixture",
                interval_ms=100,
                access_token="fixture-token",
                transport=transport,
            )
            client.interval_seconds = 0
            receipt = MODULE.build_bundle(job, client=client)

            self.assertEqual(receipt["status"], "READY")
            self.assertEqual(receipt["ready_symbols"], 2)
            self.assertEqual(receipt["api_mutation_count"], 0)
            self.assertEqual(receipt["failed_symbols"], [])
            self.assertTrue(Path(receipt["catalog"]["path"]).is_file())
            self.assertTrue(Path(receipt["build_spec"]["path"]).is_file())
            self.assertEqual(receipt["coverage_by_exchange"]["KOSPI"]["ready"], 1)


if __name__ == "__main__":
    unittest.main()

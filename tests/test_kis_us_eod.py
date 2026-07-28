#!/usr/bin/env python3
"""Offline contract tests for the KIS U.S. EOD collector."""

from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "skills" / "quant-stock-technical" / "scripts"
sys.path.insert(0, str(SCRIPTS))
SPEC = importlib.util.spec_from_file_location(
    "fetch_kis_us_eod_contract", SCRIPTS / "fetch_kis_us_eod.py"
)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError("cannot load fetch_kis_us_eod.py")
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


class KisUsEodTests(unittest.TestCase):
    def test_embedded_pagination_is_read_only(self) -> None:
        MODULE.self_test()

    def test_adjusted_price_contract_is_fixed(self) -> None:
        self.assertEqual(MODULE.STOCK_TR_ID, "HHDFS76240000")
        self.assertEqual(
            MODULE.STOCK_PATH,
            "/uapi/overseas-price/v1/quotations/dailyprice",
        )

    def test_exchange_codes_are_not_collapsed(self) -> None:
        self.assertEqual(MODULE.EXCHANGE_CODES["NASDAQ"], "NAS")
        self.assertEqual(MODULE.EXCHANGE_CODES["NYSE"], "NYS")

    def test_conservative_us_tick_ladder(self) -> None:
        self.assertEqual(MODULE.tick_size(Decimal("0.25")), Decimal("0.0001"))
        self.assertEqual(MODULE.tick_size(Decimal("1")), Decimal("0.01"))
        self.assertEqual(MODULE.tick_size(Decimal("250")), Decimal("0.01"))

    def test_benchmark_identity_is_exchange_specific(self) -> None:
        self.assertEqual(MODULE.BENCHMARK_SYMBOLS["NYSE"], "^NYA")
        self.assertEqual(MODULE.BENCHMARK_SYMBOLS["NASDAQ"], "^IXIC")

    def test_interior_invalid_geometry_is_audited_without_interpolation(self) -> None:
        end = date(2026, 7, 24)
        rows = MODULE.shared._synthetic_rows(end, 800, index=False)
        overseas_rows = [
            {
                "xymd": item["stck_bsop_date"],
                "open": item["stck_oprc"],
                "high": item["stck_hgpr"],
                "low": item["stck_lwpr"],
                "clos": item["stck_clpr"],
                "tvol": item["acml_vol"],
            }
            for item in rows
        ]
        invalid = overseas_rows[400]
        invalid["low"] = str(Decimal(invalid["open"]) + Decimal("1"))

        def transport(method, url, headers, body):
            del headers, body
            self.assertEqual(method, "GET")
            query = dict(item.split("=", 1) for item in url.split("?", 1)[1].split("&"))
            cursor = datetime.strptime(query["BYMD"], "%Y%m%d").date()
            selected = [
                row
                for row in overseas_rows
                if datetime.strptime(row["xymd"], "%Y%m%d").date() <= cursor
            ][:100]
            return 200, {"rt_cd": "0", "output2": selected}

        client = MODULE.shared.KisReadClient(
            environment="live",
            app_key="fixture",
            app_secret="fixture",
            interval_ms=100,
            access_token="fixture-token",
            transport=transport,
        )
        client.interval_seconds = 0
        audit = []
        result = MODULE.fetch_stock_history(
            client,
            exchange="NYSE",
            symbol="A",
            start=end - timedelta(days=1200),
            end=end,
            invalid_rows=audit,
        )
        self.assertEqual(len(result), 799)
        self.assertEqual(len(audit), 1)
        self.assertEqual(audit[0]["date"], datetime.strptime(invalid["xymd"], "%Y%m%d").date().isoformat())
        self.assertNotIn(audit[0]["date"], {row["date"] for row in result})

    def test_invalid_completed_cutoff_remains_blocked(self) -> None:
        end = date(2026, 7, 24)
        row = {
            "xymd": end.strftime("%Y%m%d"),
            "open": "10",
            "high": "11",
            "low": "10.5",
            "clos": "10.25",
            "tvol": "100",
        }

        def transport(method, url, headers, body):
            del method, url, headers, body
            return 200, {"rt_cd": "0", "output2": [row]}

        client = MODULE.shared.KisReadClient(
            environment="live",
            app_key="fixture",
            app_secret="fixture",
            interval_ms=100,
            access_token="fixture-token",
            transport=transport,
        )
        client.interval_seconds = 0
        with self.assertRaisesRegex(
            MODULE.UsEodBlockedError,
            "completed cutoff row has invalid OHLC geometry",
        ):
            MODULE.fetch_stock_history(
                client,
                exchange="NYSE",
                symbol="A",
                start=end - timedelta(days=10),
                end=end,
                invalid_rows=[],
            )

    def test_cached_invalid_geometry_is_excluded_and_audited(self) -> None:
        end = date(2026, 7, 24)
        rows = [
            {
                "date": (end - timedelta(days=offset)).isoformat(),
                "open": "10",
                "high": "11",
                "low": "9",
                "close": "10.5",
                "adjusted_close": "10.5",
                "volume": "100",
            }
            for offset in range(3)
        ]
        rows[1]["low"] = "10.25"
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "cached.csv"
            MODULE.shared.write_csv_rows(path, sorted(rows, key=lambda row: row["date"]))
            client = MODULE.shared.KisReadClient(
                environment="live",
                app_key="fixture",
                app_secret="fixture",
                interval_ms=100,
                access_token="fixture-token",
                transport=lambda *args: self.fail("cache should satisfy the job"),
            )
            result, requests, audit = MODULE.update_stock_file(
                client,
                exchange="NYSE",
                symbol="A",
                path=path,
                start=end - timedelta(days=3),
                end=end,
                minimum_sessions=2,
            )
            self.assertEqual(requests, 0)
            self.assertEqual(len(result), 2)
            self.assertEqual(len(audit), 1)
            self.assertNotIn(audit[0]["date"], {row["date"] for row in result})

    def test_offline_bundle_emits_cross_market_inputs(self) -> None:
        end = date(2026, 7, 24)
        raw_rows = MODULE.shared._synthetic_rows(end, 800, index=False)
        overseas_rows = [
            {
                "xymd": item["stck_bsop_date"],
                "open": item["stck_oprc"],
                "high": item["stck_hgpr"],
                "low": item["stck_lwpr"],
                "clos": item["stck_clpr"],
                "tvol": item["acml_vol"],
            }
            for item in raw_rows
        ]
        benchmark_rows = [MODULE.shared.normalize_stock_row(item) for item in raw_rows]

        def transport(method, url, headers, body):
            del headers, body
            self.assertEqual(method, "GET")
            query = dict(item.split("=", 1) for item in url.split("?", 1)[1].split("&"))
            cursor = datetime.strptime(query["BYMD"], "%Y%m%d").date()
            selected = [
                row
                for row in overseas_rows
                if datetime.strptime(row["xymd"], "%Y%m%d").date() <= cursor
            ][:100]
            return 200, {"rt_cd": "0", "output2": selected}

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            nasdaq = root / "nasdaqlisted.txt"
            nyse = root / "otherlisted.txt"
            nasdaq.write_text(
                "Symbol|Security Name|Market Category|Test Issue|Financial Status|"
                "Round Lot Size|ETF|NextShares\n"
                "AAPL|Apple Inc|Q|N|N|100|N|N\n",
                encoding="utf-8",
            )
            nyse.write_text(
                "ACT Symbol|Security Name|Exchange|CQS Symbol|ETF|Round Lot Size|"
                "Test Issue|NASDAQ Symbol\n"
                "BA|Boeing|N|BA|N|100|N|BA\n",
                encoding="utf-8",
            )
            masters = {}
            for exchange, symbol in (("NASDAQ", "AAPL"), ("NYSE", "BA")):
                path = root / ("nasmst.cod" if exchange == "NASDAQ" else "nysmst.cod")
                path.write_text(
                    MODULE.universe.synthetic_kis_overseas_line(
                        exchange, symbol, "2", "N"
                    )
                    + "\n",
                    encoding="cp949",
                )
                masters[exchange] = path
            official_sources = [
                {
                    "source_id": f"official-{exchange}",
                    "provider": "NASDAQ_TRADER",
                    "exchange": exchange,
                    "as_of": "2026-07-27",
                    "path": str(nasdaq if exchange == "NASDAQ" else nyse),
                    "format": (
                        "NASDAQ_LISTED" if exchange == "NASDAQ" else "NASDAQ_OTHER"
                    ),
                    "encoding": "utf-8",
                    "delimiter": "|",
                    "skip_rows": 0,
                    "columns": {},
                    "normal_status_values": ["N"] if exchange == "NASDAQ" else [],
                }
                for exchange in MODULE.US_EXCHANGES
            ]
            broker_sources = [
                {
                    "source_id": f"broker-{exchange}",
                    "provider": "KIS",
                    "exchange": exchange,
                    "as_of": "2026-07-27",
                    "path": str(masters[exchange]),
                    "format": "KIS_OVERSEAS_MASTER",
                    "encoding": "cp949",
                    "delimiter": "\t",
                    "skip_rows": 0,
                    "columns": {},
                    "normal_status_values": [],
                }
                for exchange in MODULE.US_EXCHANGES
            ]
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
            job_path = root / "job.json"
            job_path.write_text(json.dumps(raw_job), encoding="utf-8")
            client = MODULE.shared.KisReadClient(
                environment="live",
                app_key="fixture",
                app_secret="fixture",
                interval_ms=100,
                access_token="fixture-token",
                transport=transport,
            )
            client.interval_seconds = 0
            receipt = MODULE.build_bundle(
                MODULE.normalize_job(raw_job, root),
                client=client,
                benchmark_loader=lambda symbol, start, end: benchmark_rows,
            )
            self.assertEqual(receipt["status"], "READY")
            self.assertEqual(receipt["ready_symbols"], 2)
            self.assertEqual(receipt["api_mutation_count"], 0)
            self.assertTrue(Path(receipt["catalog"]["path"]).is_file())
            build_spec_path = Path(receipt["build_spec"]["path"])
            build_spec = json.loads(build_spec_path.read_text(encoding="utf-8"))
            for role, sources in (
                ("OFFICIAL_MASTER", build_spec["official_sources"]),
                ("BROKER_MASTER", build_spec["broker_sources"]),
            ):
                for index, source in enumerate(sources):
                    self.assertEqual(set(source), MODULE.universe.SOURCE_FIELDS)
                    MODULE.universe.normalize_source_descriptor(
                        source,
                        role,
                        build_spec["as_of"],
                        build_spec_path.parent,
                        index,
                    )


if __name__ == "__main__":
    unittest.main()

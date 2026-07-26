#!/usr/bin/env python3
"""Focused regression tests for the four-exchange universe manifest builder."""

from __future__ import annotations

import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = (
    ROOT / "skills" / "quant-stock-technical" / "scripts" / "build_universe_manifest.py"
)
SPEC = importlib.util.spec_from_file_location("build_universe_manifest", MODULE_PATH)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError(f"cannot load {MODULE_PATH}")
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


class UniverseBuilderTests(unittest.TestCase):
    def test_embedded_offline_contract(self) -> None:
        MODULE.self_test()

    def test_no_implicit_build_spec_fields(self) -> None:
        with self.assertRaisesRegex(MODULE.UniverseBlockedError, "fields mismatch"):
            MODULE.normalize_build_spec(
                {
                    "schema": MODULE.BUILD_SPEC_SCHEMA,
                    "as_of": "2026-07-26",
                    "analysis_date": "2026-07-24",
                    "official_sources": [],
                    "broker_sources": [],
                    "eod_catalog": {},
                    "guessed_tick_size": "0.01",
                },
                ROOT,
            )

    def test_tick_contract_requires_positive_decimals(self) -> None:
        with self.assertRaisesRegex(MODULE.UniverseBlockedError, "must be positive"):
            MODULE.decimal_string("0", "resolved_tick_size", positive=True)
        self.assertEqual(
            MODULE.decimal_string("0.0100", "resolved_tick_size", positive=True),
            "0.0100",
        )
        row = MODULE.CatalogRow(
            exchange="KOSPI",
            canonical_symbol="005930",
            data_symbol="005930.KS",
            broker_symbol="005930",
            instrument_type="COMMON",
            benchmark_id="KOSPI_COMPOSITE",
            ticker_csv="ticker.csv",
            benchmark_csv="benchmark.csv",
            tick_rule_id="KRX-2026",
            tick_effective_date="2026-07-25",
            tick_reference_price="100",
            resolved_tick_size="1",
            source_name="fixture",
        )
        contract, reason = MODULE.normalize_tick_contract(row, "2026-07-24")
        self.assertIsNone(contract)
        self.assertEqual(reason, "invalid_tick_contract")

    def test_catalog_coverage_contract_is_exact_and_canonical(self) -> None:
        self.assertTrue(MODULE.meets_minimum_coverage_ratio(1, 2, "0.5"))
        self.assertFalse(MODULE.meets_minimum_coverage_ratio(1, 2, "0.5001"))
        self.assertFalse(MODULE.meets_minimum_coverage_ratio(0, 0, "0.1"))
        contract = MODULE.normalize_catalog_coverage_contract(
            {
                "schema": MODULE.CATALOG_COVERAGE_SCHEMA,
                "minimum_ratio_by_exchange": {
                    "KOSPI": "1.00",
                    "KOSDAQ": "0.9500",
                    "NYSE": 1,
                    "NASDAQ": "0.5",
                },
                "minimum_screenable_ratio_by_exchange": {
                    "KOSPI": "0.500",
                    "KOSDAQ": "0.400",
                    "NYSE": "0.300",
                    "NASDAQ": "0.200",
                },
            }
        )
        self.assertEqual(
            contract,
            {
                "schema": MODULE.CATALOG_COVERAGE_SCHEMA,
                "minimum_ratio_by_exchange": {
                    "KOSPI": "1",
                    "KOSDAQ": "0.95",
                    "NYSE": "1",
                    "NASDAQ": "0.5",
                },
                "minimum_screenable_ratio_by_exchange": {
                    "KOSPI": "0.5",
                    "KOSDAQ": "0.4",
                    "NYSE": "0.3",
                    "NASDAQ": "0.2",
                },
            },
        )
        with self.assertRaisesRegex(
            MODULE.UniverseBlockedError,
            "fields mismatch",
        ):
            MODULE.normalize_catalog_coverage_contract(
                {
                    "schema": MODULE.CATALOG_COVERAGE_SCHEMA,
                    "minimum_ratio_by_exchange": {
                        exchange: "1" for exchange in MODULE.EXCHANGES
                    },
                }
            )
        with self.assertRaisesRegex(
            MODULE.UniverseBlockedError,
            "must contain exactly",
        ):
            MODULE.normalize_catalog_coverage_contract(
                {
                    "schema": MODULE.CATALOG_COVERAGE_SCHEMA,
                    "minimum_ratio_by_exchange": {
                        "KOSPI": "1",
                        "KOSDAQ": "1",
                        "NYSE": "1",
                    },
                    "minimum_screenable_ratio_by_exchange": {
                        "KOSPI": "0.5",
                        "KOSDAQ": "0.5",
                        "NYSE": "0.5",
                        "NASDAQ": "0.5",
                    },
                }
            )
        for invalid in ("0", "1.01", "NaN"):
            with (
                self.subTest(invalid=invalid),
                self.assertRaisesRegex(
                    MODULE.UniverseBlockedError,
                    "greater than 0 and at most 1",
                ),
            ):
                MODULE.canonical_ratio_string(invalid, "coverage")

    def test_exchange_contract_is_complete(self) -> None:
        self.assertEqual(
            set(MODULE.EXCHANGE_CONTRACTS),
            {"KOSPI", "KOSDAQ", "NYSE", "NASDAQ"},
        )
        self.assertEqual(
            MODULE.EXCHANGE_CONTRACTS["NASDAQ"]["venue"],
            "NASD",
        )
        self.assertEqual(
            MODULE.EXCHANGE_CONTRACTS["KOSDAQ"]["benchmark_id"],
            "KOSDAQ_COMPOSITE",
        )

    def test_kind_cp949_html_preserves_alphanumeric_code(self) -> None:
        with tempfile.TemporaryDirectory(prefix="kind-html-test-") as directory:
            path = Path(directory) / "corpList.xls"
            path.write_text(
                "<html><body><table>"
                "<tr><th>회사명</th><th>시장구분</th><th>종목코드</th></tr>"
                "<tr><td>에이치엘지노믹스</td><td>코스닥</td>"
                "<td style=\"mso-number-format:'@'\">0156T0</td></tr>"
                "<tr><td>에이치엘지노믹스</td><td>코스닥</td>"
                "<td style=\"mso-number-format:'@'\">0156T0</td></tr>"
                "<tr><td>삼성전자</td><td>유가</td><td>005930</td></tr>"
                "</table></body></html>",
                encoding="cp949",
            )
            base = {
                "source_id": "kind",
                "provider": "KRX",
                "as_of": "2026-07-26",
                "path": str(path),
                "format": "KRX_KIND_HTML",
                "encoding": "cp949",
                "delimiter": "",
                "skip_rows": 0,
                "columns": {},
                "normal_status_values": [],
                "role": "OFFICIAL_MASTER",
            }
            kosdaq_rows = MODULE.parse_master_source({**base, "exchange": "KOSDAQ"})
            kospi_rows = MODULE.parse_master_source({**base, "exchange": "KOSPI"})
            self.assertEqual([row.symbol for row in kosdaq_rows], ["0156T0"])
            self.assertEqual([row.symbol for row in kospi_rows], ["005930"])

            path.write_text(
                "<html><body><table>"
                "<tr><th>회사명</th><th>시장구분</th><th>종목코드</th></tr>"
                "<tr><td>회사 A</td><td>코스닥</td><td>0156T0</td></tr>"
                "<tr><td>회사 B</td><td>코스닥</td><td>0156T0</td></tr>"
                "</table></body></html>",
                encoding="cp949",
            )
            with self.assertRaisesRegex(
                MODULE.UniverseBlockedError, "conflicting duplicate"
            ):
                MODULE.parse_master_source({**base, "exchange": "KOSDAQ"})

    def test_kis_domestic_raw_master_flags(self) -> None:
        self.assertEqual(
            sum(MODULE.KIS_KOSPI_WIDTHS),
            MODULE.KIS_KRX_LAYOUTS["KOSPI"]["tail_length"],
        )
        self.assertEqual(
            sum(MODULE.KIS_KOSDAQ_WIDTHS),
            MODULE.KIS_KRX_LAYOUTS["KOSDAQ"]["tail_length"],
        )
        with tempfile.TemporaryDirectory(prefix="kis-krx-test-") as directory:
            path = Path(directory) / "kospi_code.mst"
            path.write_text(
                MODULE.synthetic_kis_krx_line(
                    "KOSPI",
                    "005935",
                    "삼성전자우",
                    etp_code="3",
                    spac="Y",
                    preferred="1",
                    trading_halt="Y",
                    liquidation="Y",
                    administrative="Y",
                )
                + "\n",
                encoding="cp949",
            )
            descriptor = {
                "source_id": "kis-kospi",
                "provider": "KIS",
                "exchange": "KOSPI",
                "as_of": "2026-07-26",
                "path": str(path),
                "format": "KIS_KRX_MASTER",
                "encoding": "cp949",
                "delimiter": "",
                "skip_rows": 0,
                "columns": {},
                "normal_status_values": [],
                "role": "BROKER_MASTER",
            }
            rows = MODULE.parse_kis_krx_master(descriptor)
            self.assertEqual(rows[0].symbol, "005935")
            reasons = MODULE.metadata_exclusion_reasons(rows[0], descriptor)
            self.assertTrue(
                {
                    "instrument_etn",
                    "instrument_spac",
                    "instrument_preferred",
                    "trading_halt",
                    "liquidation",
                    "administrative_issue",
                }.issubset(set(reasons))
            )

    def test_kis_overseas_headerless_24_column_master(self) -> None:
        with tempfile.TemporaryDirectory(prefix="kis-us-test-") as directory:
            path = Path(directory) / "nysmst.cod"
            path.write_text(
                MODULE.synthetic_kis_overseas_line("NYSE", "ADR1", "2", "Y")
                + "\n"
                + MODULE.synthetic_kis_overseas_line("NYSE", "ETF1", "3", "N", "001")
                + "\n",
                encoding="cp949",
            )
            descriptor = {
                "source_id": "kis-nyse",
                "provider": "KIS",
                "exchange": "NYSE",
                "as_of": "2026-07-26",
                "path": str(path),
                "format": "KIS_OVERSEAS_MASTER",
                "encoding": "cp949",
                "delimiter": "\t",
                "skip_rows": 0,
                "columns": {},
                "normal_status_values": [],
                "role": "BROKER_MASTER",
            }
            rows = MODULE.parse_kis_overseas_master(descriptor)
            self.assertEqual(
                [(row.symbol, row.instrument_type) for row in rows],
                [("ADR1", "DR"), ("ETF1", "ETP")],
            )
            self.assertIn(
                "instrument_etf",
                MODULE.metadata_exclusion_reasons(rows[1], descriptor),
            )


if __name__ == "__main__":
    unittest.main()

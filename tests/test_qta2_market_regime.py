#!/usr/bin/env python3
"""Regression tests for QTA2 same-session market-regime admission."""

from __future__ import annotations

import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "skills" / "quant-stock-polling-trader" / "scripts"
sys.path.insert(0, str(SCRIPTS))

from broker_adapters import HttpResponse, KisBroker, QueueTransport
from execution_core import BlockedError
from run_session import evaluate_market_regime


def kis_broker(responses: list[HttpResponse], *, environment: str = "live") -> KisBroker:
    return KisBroker(
        app_key="test-key",
        app_secret="test-secret",
        account_prefix="12345678",
        account_product="01",
        environment=environment,
        transport=QueueTransport(responses),
        access_token="test-token",
    )


class Qta2MarketRegimeTests(unittest.TestCase):
    def test_kr_benchmark_quote_and_admission(self) -> None:
        broker = kis_broker(
            [
                HttpResponse(
                    200,
                    {},
                    {
                        "rt_cd": "0",
                        "msg_cd": "0",
                        "msg1": "OK",
                        "output": [
                            {
                                "stck_cntg_hour": "090005",
                                "bstp_nmix_prpr": "3200",
                                "bstp_nmix_prdy_vrss": "16",
                                "prdy_vrss_sign": "2",
                            }
                        ],
                    },
                )
            ]
        )
        quote = broker.benchmark_quote(
            exchange="KOSPI",
            session_date="2026-07-29",
        )
        observed = datetime.fromisoformat(quote["source_timestamp"]) + timedelta(
            seconds=1
        )
        quote["received_at"] = observed.astimezone(timezone.utc).isoformat()
        decision = evaluate_market_regime(
            exchange="KOSPI",
            quote=quote,
            observed_at=observed,
            session_date="2026-07-29",
        )
        self.assertTrue(decision["quality_valid"])
        self.assertTrue(decision["admitted"])
        self.assertEqual(quote["previous_close"], "3184")

    def test_negative_benchmark_blocks_entry(self) -> None:
        now = datetime(2026, 7, 29, 9, 0, 5, tzinfo=timezone(timedelta(hours=9)))
        quote = {
            "schema": "qta-market-regime-quote/v1",
            "broker": "kis",
            "exchange": "KOSDAQ",
            "benchmark_id": "KOSDAQ_COMPOSITE",
            "regime_proxy_id": "KOSDAQ_COMPOSITE",
            "provider_symbol": "1001",
            "current": "990",
            "previous_close": "1000",
            "change_bps": "-100.00",
            "source_timestamp": now.isoformat(),
            "received_at": now.isoformat(),
            "raw_status": "OK",
        }
        decision = evaluate_market_regime(
            exchange="KOSDAQ",
            quote=quote,
            observed_at=now,
            session_date="2026-07-29",
        )
        self.assertTrue(decision["quality_valid"])
        self.assertFalse(decision["admitted"])
        self.assertIn("benchmark_below_minimum", decision["reasons"])

    def test_stale_benchmark_blocks_entry(self) -> None:
        now = datetime(2026, 7, 29, 9, 2, 0, tzinfo=timezone(timedelta(hours=9)))
        stale = now - timedelta(seconds=91)
        quote = {
            "schema": "qta-market-regime-quote/v1",
            "broker": "kis",
            "exchange": "KOSPI",
            "benchmark_id": "KOSPI_COMPOSITE",
            "regime_proxy_id": "KOSPI_COMPOSITE",
            "provider_symbol": "0001",
            "current": "1001",
            "previous_close": "1000",
            "change_bps": "10.00",
            "source_timestamp": stale.isoformat(),
            "received_at": now.isoformat(),
            "raw_status": "OK",
        }
        decision = evaluate_market_regime(
            exchange="KOSPI",
            quote=quote,
            observed_at=now,
            session_date="2026-07-29",
        )
        self.assertFalse(decision["quality_valid"])
        self.assertFalse(decision["admitted"])
        self.assertIn("source_timestamp_stale", decision["reasons"])

    def test_us_benchmark_uses_timestamped_intraday_price(self) -> None:
        broker = kis_broker(
            [
                HttpResponse(
                    200,
                    {},
                    {
                        "rt_cd": "0",
                        "msg_cd": "0",
                        "msg1": "OK",
                        "output1": {
                            "ovrs_nmix_prdy_clpr": "25000",
                            "ovrs_nmix_prpr": "25100",
                        },
                        "output2": [
                            {
                                "stck_bsop_date": "20260729",
                                "stck_cntg_hour": "093000",
                                "optn_prpr": "25025",
                                "optn_oprc": "25020",
                                "optn_hgpr": "25030",
                                "optn_lwpr": "25010",
                                "cntg_vol": "0",
                            }
                        ],
                    },
                )
            ]
        )
        quote = broker.benchmark_quote(
            exchange="NASDAQ",
            session_date="2026-07-29",
        )
        self.assertEqual(quote["current"], "25025")
        self.assertEqual(quote["previous_close"], "25000")
        self.assertEqual(quote["change_bps"], "10.00")
        self.assertEqual(quote["regime_proxy_id"], "NASDAQ_COMPOSITE")

    def test_us_paper_benchmark_is_rejected(self) -> None:
        broker = kis_broker([], environment="paper")
        with self.assertRaisesRegex(BlockedError, "paper benchmark"):
            broker.benchmark_quote(
                exchange="NASDAQ",
                session_date="2026-07-29",
            )


if __name__ == "__main__":
    unittest.main()

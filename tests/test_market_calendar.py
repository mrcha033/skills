#!/usr/bin/env python3
"""Offline tests for official market-calendar parsing and freezing."""

from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "skills" / "quant-stock-polling-trader" / "scripts"
sys.path.insert(0, str(SCRIPTS))
SPEC = importlib.util.spec_from_file_location(
    "qta_market_calendar_contract",
    SCRIPTS / "market_calendar.py",
)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError("cannot load market_calendar.py")
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


NASDAQ = b"""
<table>
<tr><th>2025</th><th>Holiday</th><th>Status</th></tr>
<tr><td>January 1, 2025</td><td>New Year</td><td>Closed</td></tr>
<tr><th>2026</th><th>Holiday</th><th>Status</th></tr>
<tr><td>January 1, 2026</td><td>New Year</td><td>Closed</td></tr>
<tr><td>November 27, 2026</td><td>Early Close</td><td>1:00 p.m.</td></tr>
</table>
"""
NYSE = b"""
<p>Core Trading Session: 9:30 a.m. to 4:00 p.m. ET</p>
<table>
<tr><th>Holiday</th><th>2025</th><th>2026</th></tr>
<tr><th>New Year</th><td>Wednesday, January 1</td><td>Thursday, January 1</td></tr>
</table>
<p>Each market will close early at 1:00 p.m. on Friday,
November 27, 2026.</p>
"""


class MarketCalendarTests(unittest.TestCase):
    def test_embedded_self_test(self) -> None:
        MODULE.self_test()

    def test_us_sources_must_agree(self) -> None:
        self.assertEqual(
            MODULE.parse_nasdaq_calendar(NASDAQ, 2026),
            MODULE.parse_nyse_calendar(NYSE, 2026),
        )
        mismatched = NASDAQ.replace(
            b"January 1, 2026</td><td>New Year</td><td>Closed",
            b"January 1, 2026</td><td>New Year</td><td>1:00 p.m.",
            1,
        )
        with self.assertRaisesRegex(
            MODULE.CalendarBlockedError, "no closed dates"
        ):
            MODULE.parse_nasdaq_calendar(mismatched, 2026)

    def test_krx_holiday_page_contract_is_required(self) -> None:
        valid = (
            f"<script>{MODULE.KRX_BLD}; search_bas_yy</script>".encode()
        )
        MODULE.validate_krx_holiday_page(valid)
        with self.assertRaisesRegex(
            MODULE.CalendarBlockedError, "request contract"
        ):
            MODULE.validate_krx_holiday_page(b"<html></html>")

    def test_mocked_us_snapshot_is_source_hashed(self) -> None:
        responses = {
            MODULE.NYSE_CALENDAR_URL: NYSE,
            MODULE.NASDAQ_CALENDAR_URL: NASDAQ,
        }

        def fetcher(url: str, data: bytes | None = None) -> bytes:
            self.assertIsNone(data)
            return responses[url]

        with tempfile.TemporaryDirectory(prefix="qta-market-calendar-") as temporary:
            receipt = MODULE.snapshot(
                market="US",
                session_date="2026-07-28",
                output_directory=Path(temporary),
                now=datetime(
                    2026,
                    7,
                    28,
                    8,
                    0,
                    tzinfo=ZoneInfo("America/New_York"),
                ),
                fetcher=fetcher,
            )
            self.assertEqual(receipt["status"], "READY")
            self.assertEqual(receipt["previous_session_date"], "2026-07-27")
            self.assertEqual(receipt["api_mutation_count"], 0)
            bound = json.loads(
                Path(receipt["market_session_path"]).read_text(encoding="utf-8")
            )
            self.assertEqual(bound["regular_open"], "2026-07-28T09:30:00-04:00")
            self.assertEqual(bound["regular_close"], "2026-07-28T16:00:00-04:00")

    def test_closed_session_does_not_emit_bound_market_session(self) -> None:
        responses = {
            MODULE.NYSE_CALENDAR_URL: NYSE,
            MODULE.NASDAQ_CALENDAR_URL: NASDAQ,
        }

        def fetcher(url: str, data: bytes | None = None) -> bytes:
            self.assertIsNone(data)
            return responses[url]

        with tempfile.TemporaryDirectory(prefix="qta-market-closed-") as temporary:
            receipt = MODULE.snapshot(
                market="US",
                session_date="2026-01-01",
                output_directory=Path(temporary),
                now=datetime(
                    2026,
                    1,
                    1,
                    8,
                    0,
                    tzinfo=ZoneInfo("America/New_York"),
                ),
                fetcher=fetcher,
            )
            self.assertEqual(receipt["status"], "MARKET_CLOSED")
            self.assertEqual(receipt["market_session_path"], "")
            self.assertFalse((Path(temporary) / "market-session.json").exists())


if __name__ == "__main__":
    unittest.main()

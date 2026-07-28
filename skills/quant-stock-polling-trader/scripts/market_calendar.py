#!/usr/bin/env python3
"""Freeze an official KR or US equity-market calendar snapshot."""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import os
import re
import sys
import tempfile
from collections.abc import Callable
from datetime import date, datetime, time, timedelta
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

from execution_core import (
    ENTRY_WINDOWS,
    BlockedError,
    market_session_from_source,
    normalized_market_session,
)

RECEIPT_SCHEMA = "qta-market-calendar-receipt/v1"
KRX_HOLIDAY_PAGE = (
    "https://global.krx.co.kr/contents/GLB/05/0501/0501110000/"
    "GLB0501110000.jsp"
)
KRX_HOURS_PAGE = (
    "https://global.krx.co.kr/contents/GLB/06/0602/0602020204/"
    "GLB0602020204T1.jsp"
)
KRX_OTP_URL = "https://global.krx.co.kr/contents/COM/GenerateOTP.jspx"
KRX_DATA_URL = "https://global.krx.co.kr/contents/GLB/99/GLB99000001.jspx"
KRX_BLD = "GLB/05/0501/0501110000/glb0501110000_01"
NYSE_CALENDAR_URL = "https://www.nyse.com/markets/hours-calendars"
NASDAQ_CALENDAR_URL = "https://www.nasdaqtrader.com/Trader.aspx?id=calendar"
USER_AGENT = "qta-market-calendar/1.0 (+official read-only snapshot)"
MONTH_PATTERN = (
    r"January|February|March|April|May|June|July|August|"
    r"September|October|November|December"
)
FULL_DATE = re.compile(
    rf"\b({MONTH_PATTERN})\s+(\d{{1,2}}),\s+(\d{{4}})\b",
    re.IGNORECASE,
)
MONTH_DAY = re.compile(
    rf"\b({MONTH_PATTERN})\s+(\d{{1,2}})\b",
    re.IGNORECASE,
)


class CalendarBlockedError(BlockedError):
    """Raised when official calendar evidence cannot support one session."""


class CalendarHtmlParser(HTMLParser):
    """Collect text rows and block text without executing page scripts."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.rows: list[list[str]] = []
        self.blocks: list[str] = []
        self._row: list[str] | None = None
        self._cell: list[str] | None = None
        self._block: list[str] | None = None
        self._block_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        del attrs
        tag = tag.lower()
        if tag == "tr":
            self._row = []
        elif tag in {"th", "td"} and self._row is not None:
            self._cell = []
        if tag in {"p", "li"}:
            if self._block is None:
                self._block = []
                self._block_depth = 1
            else:
                self._block_depth += 1

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in {"th", "td"} and self._row is not None and self._cell is not None:
            self._row.append(normalize_text(" ".join(self._cell)))
            self._cell = None
        elif tag == "tr" and self._row is not None:
            if any(self._row):
                self.rows.append(self._row)
            self._row = None
            self._cell = None
        if tag in {"p", "li"} and self._block is not None:
            self._block_depth -= 1
            if self._block_depth == 0:
                text = normalize_text(" ".join(self._block))
                if text:
                    self.blocks.append(text)
                self._block = None

    def handle_data(self, data: str) -> None:
        if self._cell is not None:
            self._cell.append(data)
        if self._block is not None:
            self._block.append(data)


def normalize_text(value: str) -> str:
    return " ".join(html.unescape(value).replace("\xa0", " ").split())


def canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def atomic_write(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent)
    )
    temporary_path = Path(temporary)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, path)
        os.chmod(path, 0o600)
    finally:
        if temporary_path.exists():
            temporary_path.unlink()


def atomic_write_json(path: Path, value: Any) -> None:
    atomic_write(
        path,
        (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode(
            "utf-8"
        ),
    )


def fetch_bytes(url: str, data: bytes | None = None) -> bytes:
    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "application/json,text/html,application/xhtml+xml",
    }
    if data is not None:
        headers["Content-Type"] = "application/x-www-form-urlencoded"
    request = Request(url, data=data, headers=headers, method="POST" if data else "GET")
    with urlopen(request, timeout=30) as response:
        status = getattr(response, "status", 200)
        if status != 200:
            raise CalendarBlockedError(f"official calendar HTTP status {status}: {url}")
        payload = response.read()
    if not payload:
        raise CalendarBlockedError(f"official calendar response is empty: {url}")
    return payload


def parse_iso_date(value: str, label: str) -> date:
    try:
        return date.fromisoformat(value)
    except (TypeError, ValueError) as exc:
        raise CalendarBlockedError(f"{label} must be YYYY-MM-DD") from exc


def parsed_html(payload: bytes, label: str) -> CalendarHtmlParser:
    try:
        text = payload.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise CalendarBlockedError(f"{label} is not UTF-8") from exc
    parser = CalendarHtmlParser()
    parser.feed(text)
    parser.close()
    if not parser.rows:
        raise CalendarBlockedError(f"{label} contains no calendar table rows")
    return parser


def full_date_from_match(match: re.Match[str]) -> date:
    return datetime.strptime(
        f"{match.group(1)} {match.group(2)}, {match.group(3)}",
        "%B %d, %Y",
    ).date()


def parse_nasdaq_calendar(payload: bytes, year: int) -> tuple[set[date], set[date]]:
    parser = parsed_html(payload, "Nasdaq calendar")
    closed: set[date] = set()
    early: set[date] = set()
    for row in parser.rows:
        if len(row) < 3:
            continue
        match = FULL_DATE.search(row[0])
        if match is None:
            continue
        session = full_date_from_match(match)
        if session.year != year:
            continue
        status = row[-1].lower()
        if status == "closed":
            closed.add(session)
        elif re.fullmatch(r"1:00\s*p\.?m\.?", status):
            early.add(session)
        else:
            raise CalendarBlockedError(
                f"Nasdaq calendar has unknown status for {session}: {row[-1]}"
            )
    if not closed:
        raise CalendarBlockedError(f"Nasdaq calendar has no closed dates for {year}")
    return closed, early


def parse_nyse_calendar(payload: bytes, year: int) -> tuple[set[date], set[date]]:
    parser = parsed_html(payload, "NYSE calendar")
    header: list[str] | None = None
    closed: set[date] = set()
    for row in parser.rows:
        if "Holiday" in row and str(year) in row:
            header = row
            continue
        if header is None or len(row) < len(header):
            continue
        year_index = header.index(str(year))
        value = row[year_index]
        match = MONTH_DAY.search(value)
        if match is None:
            continue
        closed.add(
            datetime.strptime(
                f"{match.group(1)} {match.group(2)}, {year}",
                "%B %d, %Y",
            ).date()
        )
    early: set[date] = set()
    for block in parser.blocks:
        lowered = block.lower()
        if "close early at 1:00 p.m." not in lowered:
            continue
        for match in FULL_DATE.finditer(block):
            parsed = full_date_from_match(match)
            if parsed.year == year:
                early.add(parsed)
    if not closed:
        raise CalendarBlockedError(f"NYSE calendar has no closed dates for {year}")
    if "Core Trading Session: 9:30 a.m. to 4:00 p.m. ET" not in normalize_text(
        payload.decode("utf-8")
    ):
        raise CalendarBlockedError("NYSE core trading-hour contract is missing")
    return closed, early


def parse_krx_holidays(payload: bytes, year: int) -> set[date]:
    try:
        decoded = json.loads(payload)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CalendarBlockedError("KRX holiday payload is not valid JSON") from exc
    if not isinstance(decoded, dict) or set(decoded) != {"block1"}:
        raise CalendarBlockedError("KRX holiday payload fields changed")
    rows = decoded["block1"]
    if not isinstance(rows, list) or not rows:
        raise CalendarBlockedError(f"KRX holiday payload is empty for {year}")
    holidays: set[date] = set()
    for index, row in enumerate(rows):
        if not isinstance(row, dict) or "calnd_dd" not in row:
            raise CalendarBlockedError(f"KRX holiday row {index} is malformed")
        parsed = parse_iso_date(row["calnd_dd"], f"KRX holiday row {index}.calnd_dd")
        if parsed.year != year:
            raise CalendarBlockedError("KRX holiday payload contains another year")
        holidays.add(parsed)
    return holidays


def validate_krx_hours(payload: bytes) -> None:
    text = normalize_text(payload.decode("utf-8"))
    if "Regular market session" not in text or "09:00 - 15:30" not in text:
        raise CalendarBlockedError("KRX regular trading-hour contract is missing")


def validate_krx_holiday_page(payload: bytes) -> None:
    try:
        text = payload.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise CalendarBlockedError("KRX holiday page is not UTF-8") from exc
    if KRX_BLD.lower() not in text.lower() or "search_bas_yy" not in text:
        raise CalendarBlockedError("KRX holiday-page request contract is missing")


def previous_open_session(session: date, closed: set[date]) -> date:
    candidate = session - timedelta(days=1)
    for _ in range(14):
        if candidate.weekday() < 5 and candidate not in closed:
            return candidate
        candidate -= timedelta(days=1)
    raise CalendarBlockedError("official calendar cannot resolve previous session")


def source_record(path: Path, source_id: str) -> dict[str, Any]:
    return {
        "source_id": source_id,
        "path": str(path.resolve()),
        "sha256": sha256_file(path),
        "bytes": path.stat().st_size,
    }


def krx_year_payload(
    year: int,
    fetcher: Callable[[str, bytes | None], bytes],
) -> bytes:
    otp = fetcher(KRX_OTP_URL, urlencode({"bld": KRX_BLD}).encode("ascii"))
    try:
        otp_text = otp.decode("ascii").strip()
    except UnicodeDecodeError as exc:
        raise CalendarBlockedError("KRX OTP is not ASCII") from exc
    if not otp_text or not re.fullmatch(r"[A-Za-z0-9+/=_-]+", otp_text):
        raise CalendarBlockedError("KRX OTP format changed")
    return fetcher(
        KRX_DATA_URL,
        urlencode(
            {"search_bas_yy": str(year), "gridTp": "KRX", "code": otp_text}
        ).encode("ascii"),
    )


def build_kr_snapshot(
    *,
    session: date,
    now: datetime,
    output_directory: Path,
    fetcher: Callable[[str, bytes | None], bytes],
) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    holiday_page = fetcher(KRX_HOLIDAY_PAGE, None)
    hours_page = fetcher(KRX_HOURS_PAGE, None)
    validate_krx_holiday_page(holiday_page)
    validate_krx_hours(hours_page)
    years = {session.year, (session - timedelta(days=14)).year}
    holiday_payloads = {year: krx_year_payload(year, fetcher) for year in years}
    closed = set().union(
        *(parse_krx_holidays(payload, year) for year, payload in holiday_payloads.items())
    )

    raw_directory = output_directory / "official"
    records: list[dict[str, Any]] = []
    for path, payload, source_id in (
        (raw_directory / "krx-holiday-page.html", holiday_page, KRX_HOLIDAY_PAGE),
        (raw_directory / "krx-trading-hours.html", hours_page, KRX_HOURS_PAGE),
    ):
        atomic_write(path, payload)
        records.append(source_record(path, source_id))
    for year, payload in sorted(holiday_payloads.items()):
        path = raw_directory / f"krx-holidays-{year}.json"
        atomic_write(path, payload)
        records.append(source_record(path, f"{KRX_DATA_URL}#KRX-{year}"))

    is_open = session.weekday() < 5 and session not in closed
    previous = previous_open_session(session, closed)
    if not is_open:
        return None, {
            "market": "KR",
            "session_date": session.isoformat(),
            "previous_session_date": previous.isoformat(),
            "scheduled_status": "MARKET_CLOSED",
            "official_sources": sorted(records, key=lambda item: item["source_id"]),
        }
    zone = ZoneInfo("Asia/Seoul")
    regular_open = datetime.combine(session, time(9, 0), zone)
    regular_close = datetime.combine(session, time(15, 30), zone)
    if now > regular_open:
        raise CalendarBlockedError("KR calendar snapshot completed after regular open")
    source = {
        "schema": "qta-market-session/v1",
        "provider": "KRX",
        "source_id": KRX_HOLIDAY_PAGE,
        "source_as_of": now.isoformat(),
        "market": "KR",
        "timezone": "Asia/Seoul",
        "session_date": session.isoformat(),
        "previous_session_date": previous.isoformat(),
        "scheduled_status": "OPEN",
        "regular_open": regular_open.isoformat(),
        "regular_close": regular_close.isoformat(),
    }
    return source, {
        "market": "KR",
        "session_date": session.isoformat(),
        "previous_session_date": previous.isoformat(),
        "scheduled_status": "OPEN",
        "official_sources": sorted(records, key=lambda item: item["source_id"]),
    }


def build_us_snapshot(
    *,
    session: date,
    now: datetime,
    output_directory: Path,
    fetcher: Callable[[str, bytes | None], bytes],
) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    nyse_payload = fetcher(NYSE_CALENDAR_URL, None)
    nasdaq_payload = fetcher(NASDAQ_CALENDAR_URL, None)
    years = {session.year, (session - timedelta(days=14)).year}
    nyse_closed: set[date] = set()
    nyse_early: set[date] = set()
    for year in sorted(years):
        year_nyse_closed, year_nyse_early = parse_nyse_calendar(
            nyse_payload, year
        )
        year_nasdaq_closed, year_nasdaq_early = parse_nasdaq_calendar(
            nasdaq_payload, year
        )
        if year_nyse_closed != year_nasdaq_closed:
            raise CalendarBlockedError(
                f"NYSE and Nasdaq official closed-date sets do not match for {year}"
            )
        if year_nyse_early != year_nasdaq_early:
            raise CalendarBlockedError(
                f"NYSE and Nasdaq official early-close date sets do not match for {year}"
            )
        nyse_closed.update(year_nyse_closed)
        nyse_early.update(year_nyse_early)
    raw_directory = output_directory / "official"
    records: list[dict[str, Any]] = []
    for path, payload, source_id in (
        (raw_directory / "nyse-hours-calendar.html", nyse_payload, NYSE_CALENDAR_URL),
        (
            raw_directory / "nasdaq-trader-calendar.html",
            nasdaq_payload,
            NASDAQ_CALENDAR_URL,
        ),
    ):
        atomic_write(path, payload)
        records.append(source_record(path, source_id))

    is_open = session.weekday() < 5 and session not in nyse_closed
    previous = previous_open_session(session, nyse_closed)
    if not is_open:
        return None, {
            "market": "US",
            "session_date": session.isoformat(),
            "previous_session_date": previous.isoformat(),
            "scheduled_status": "MARKET_CLOSED",
            "official_sources": sorted(records, key=lambda item: item["source_id"]),
        }
    zone = ZoneInfo("America/New_York")
    regular_open = datetime.combine(session, time(9, 30), zone)
    close_clock = time(13, 0) if session in nyse_early else time(16, 0)
    regular_close = datetime.combine(session, close_clock, zone)
    if now > regular_open:
        raise CalendarBlockedError("US calendar snapshot completed after regular open")
    source = {
        "schema": "qta-market-session/v1",
        "provider": "NYSE+NASDAQ",
        "source_id": f"{NYSE_CALENDAR_URL}|{NASDAQ_CALENDAR_URL}",
        "source_as_of": now.isoformat(),
        "market": "US",
        "timezone": "America/New_York",
        "session_date": session.isoformat(),
        "previous_session_date": previous.isoformat(),
        "scheduled_status": "OPEN",
        "regular_open": regular_open.isoformat(),
        "regular_close": regular_close.isoformat(),
    }
    return source, {
        "market": "US",
        "session_date": session.isoformat(),
        "previous_session_date": previous.isoformat(),
        "scheduled_status": "OPEN",
        "official_sources": sorted(records, key=lambda item: item["source_id"]),
    }


def snapshot(
    *,
    market: str,
    session_date: str,
    output_directory: Path,
    now: datetime | None = None,
    fetcher: Callable[[str, bytes | None], bytes] = fetch_bytes,
) -> dict[str, Any]:
    normalized_market = market.upper()
    if normalized_market not in ENTRY_WINDOWS:
        raise CalendarBlockedError("market must be KR or US")
    session = parse_iso_date(session_date, "session_date")
    zone = ZoneInfo(ENTRY_WINDOWS[normalized_market]["timezone"])
    observed_now = (now or datetime.now(zone)).astimezone(zone).replace(microsecond=0)
    if observed_now.date() != session:
        raise CalendarBlockedError(
            "session_date must equal the current date in the market timezone"
        )
    if output_directory.exists() and (
        output_directory.is_symlink() or not output_directory.is_dir()
    ):
        raise CalendarBlockedError("output_directory must be a non-symlink directory")
    output_directory.mkdir(parents=True, exist_ok=True)

    builder = build_kr_snapshot if normalized_market == "KR" else build_us_snapshot
    source, evidence = builder(
        session=session,
        now=observed_now,
        output_directory=output_directory,
        fetcher=fetcher,
    )
    base_receipt = {
        "schema": RECEIPT_SCHEMA,
        **evidence,
        "source_as_of": observed_now.isoformat(),
        "live_enabled": False,
        "api_mutation_count": 0,
    }
    if source is None:
        receipt_without_hash = {
            **base_receipt,
            "status": "MARKET_CLOSED",
            "market_session_source_path": "",
            "market_session_source_sha256": "",
            "market_session_path": "",
            "session_hash": "",
        }
    else:
        source_path = output_directory / "market-session-source.json"
        atomic_write_json(source_path, source)
        bound = market_session_from_source(source_path)
        bound = normalized_market_session(
            bound,
            expected_market=normalized_market,
            expected_timezone=ENTRY_WINDOWS[normalized_market]["timezone"],
        )
        bound_path = output_directory / "market-session.json"
        atomic_write_json(bound_path, bound)
        receipt_without_hash = {
            **base_receipt,
            "status": "READY",
            "market_session_source_path": str(source_path.resolve()),
            "market_session_source_sha256": sha256_file(source_path),
            "market_session_path": str(bound_path.resolve()),
            "session_hash": bound["session_hash"],
        }
    receipt = {
        **receipt_without_hash,
        "receipt_hash": sha256_bytes(
            canonical_json(receipt_without_hash).encode("utf-8")
        ),
    }
    atomic_write_json(output_directory / "calendar-receipt.json", receipt)
    return receipt


def self_test() -> None:
    nasdaq = b"""
    <table><tr><th>2026</th><th>Holiday</th><th>Status</th></tr>
    <tr><td>January 1, 2026</td><td>New Year</td><td>Closed</td></tr>
    <tr><td>November 27, 2026</td><td>Early Close</td><td>1:00 p.m.</td></tr>
    </table>
    """
    nyse = b"""
    <p>Core Trading Session: 9:30 a.m. to 4:00 p.m. ET</p>
    <table><tr><th>Holiday</th><th>2026</th></tr>
    <tr><th>New Year</th><td>Thursday, January 1</td></tr></table>
    <p>Each market will close early at 1:00 p.m. on Friday,
    November 27, 2026.</p>
    """
    if parse_nasdaq_calendar(nasdaq, 2026) != parse_nyse_calendar(nyse, 2026):
        raise AssertionError("US calendar parser parity failed")
    krx = json.dumps(
        {
            "block1": [
                {
                    "calnd_dd": "2026-01-01",
                    "dy_tp_cd": "THU",
                    "calnd_dd_dy": "2026-01-01",
                    "kr_dy_tp": "Thursday",
                    "holdy_eng_nm": "New Year's Day",
                }
            ]
        }
    ).encode()
    if parse_krx_holidays(krx, 2026) != {date(2026, 1, 1)}:
        raise AssertionError("KRX calendar parser failed")
    print(
        json.dumps(
            {
                "self_test": "PASS",
                "live_enabled": False,
                "api_mutation_count": 0,
            },
            sort_keys=True,
        )
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    snapshot_parser = subparsers.add_parser("snapshot")
    snapshot_parser.add_argument("--market", choices=("KR", "US"), required=True)
    snapshot_parser.add_argument("--session-date", required=True)
    snapshot_parser.add_argument("--output-directory", required=True)
    subparsers.add_parser("self-test")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.command == "self-test":
        self_test()
        return 0
    output_directory = Path(args.output_directory).expanduser()
    if not output_directory.is_absolute():
        print(
            json.dumps(
                {"status": "BLOCKED", "reason": "output_directory must be absolute"},
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        return 2
    try:
        receipt = snapshot(
            market=args.market,
            session_date=args.session_date,
            output_directory=output_directory,
        )
        print(json.dumps(receipt, sort_keys=True))
        return 0
    except (CalendarBlockedError, OSError, ValueError) as exc:
        blocked = {
            "schema": RECEIPT_SCHEMA,
            "status": "BLOCKED",
            "market": args.market,
            "session_date": args.session_date,
            "reason": str(exc),
            "live_enabled": False,
            "api_mutation_count": 0,
        }
        try:
            atomic_write_json(output_directory / "calendar-receipt.json", blocked)
        except OSError:
            pass
        print(json.dumps(blocked, sort_keys=True), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

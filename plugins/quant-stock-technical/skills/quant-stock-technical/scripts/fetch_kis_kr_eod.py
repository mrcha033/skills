#!/usr/bin/env python3
"""Build a resumable, source-hashed Korean adjusted-EOD bundle from KIS."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import re
import stat
import sys
import tempfile
import time
import zipfile
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Callable, Mapping, Optional
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import build_universe_manifest as universe


JOB_SCHEMA = "qta-kis-kr-eod-job/v1"
RECEIPT_SCHEMA = "qta-kis-kr-eod-receipt/v1"
SNAPSHOT_SCHEMA = "qta-kr-source-snapshot/v1"
CATALOG_FIELDS = universe.EOD_CATALOG_FIELDS
KR_EXCHANGES = ("KOSPI", "KOSDAQ")
JOB_FIELDS = {
    "schema",
    "as_of",
    "analysis_date",
    "environment",
    "output_directory",
    "history_start_date",
    "minimum_sessions",
    "request_interval_ms",
    "official_sources",
    "broker_sources",
    "catalog_coverage_contract",
    "base_eod_catalog",
}
KIND_URL = (
    "https://kind.krx.co.kr/corpgeneral/corpList.do?method=download&searchType=13"
)
KIS_MASTER_URLS = {
    "KOSPI": ("https://new.real.download.dws.co.kr/common/master/kospi_code.mst.zip"),
    "KOSDAQ": ("https://new.real.download.dws.co.kr/common/master/kosdaq_code.mst.zip"),
}
KIS_MASTER_NAMES = {
    "KOSPI": "kospi_code.mst",
    "KOSDAQ": "kosdaq_code.mst",
}
BASE_URLS = {
    "live": "https://openapi.koreainvestment.com:9443",
    "paper": "https://openapivts.koreainvestment.com:29443",
}
TOKEN_PATH = "/oauth2/tokenP"
STOCK_PATH = "/uapi/domestic-stock/v1/quotations/inquire-daily-itemchartprice"
INDEX_PATH = "/uapi/domestic-stock/v1/quotations/inquire-daily-indexchartprice"
STOCK_TR_ID = "FHKST03010100"
INDEX_TR_ID = "FHKUP03500100"
INDEX_CODES = {"KOSPI": "0001", "KOSDAQ": "1001"}
BENCHMARK_IDS = {
    "KOSPI": "KOSPI_COMPOSITE",
    "KOSDAQ": "KOSDAQ_COMPOSITE",
}
DEFAULT_SECRETS_PATH = Path("~/.config/mrcha-skills/secrets.env").expanduser()
DOTENV_KEY = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class EodBlockedError(ValueError):
    """Raised when an EOD bundle cannot be completed without guessing."""


def canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


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


def require_exact_fields(
    value: Mapping[str, Any], expected: set[str], label: str
) -> None:
    if set(value) != expected:
        raise EodBlockedError(
            f"{label} fields mismatch; "
            f"missing={sorted(expected - set(value))}, "
            f"extra={sorted(set(value) - expected)}"
        )


def require_iso_date(value: Any, label: str) -> str:
    if not isinstance(value, str):
        raise EodBlockedError(f"{label} must be YYYY-MM-DD")
    try:
        date.fromisoformat(value)
    except ValueError as exc:
        raise EodBlockedError(f"{label} must be YYYY-MM-DD") from exc
    return value


def require_positive_integer(value: Any, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise EodBlockedError(f"{label} must be a positive integer")
    return value


def normalize_job(raw: Any, job_directory: Path) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise EodBlockedError("job must be one JSON object")
    require_exact_fields(raw, JOB_FIELDS, "job")
    if raw["schema"] != JOB_SCHEMA:
        raise EodBlockedError(f"job.schema must be {JOB_SCHEMA}")
    as_of = require_iso_date(raw["as_of"], "job.as_of")
    analysis_date = require_iso_date(raw["analysis_date"], "job.analysis_date")
    history_start = require_iso_date(
        raw["history_start_date"], "job.history_start_date"
    )
    if history_start >= analysis_date:
        raise EodBlockedError("history_start_date must be before analysis_date")
    if as_of < analysis_date:
        raise EodBlockedError("as_of cannot be before analysis_date")
    environment = str(raw["environment"]).lower()
    if environment not in BASE_URLS:
        raise EodBlockedError("environment must be live or paper")
    output_directory = Path(str(raw["output_directory"])).expanduser()
    if not output_directory.is_absolute():
        output_directory = (job_directory / output_directory).resolve()
    minimum_sessions = require_positive_integer(
        raw["minimum_sessions"], "job.minimum_sessions"
    )
    if minimum_sessions < 756:
        raise EodBlockedError("minimum_sessions must be at least 756")
    request_interval_ms = require_positive_integer(
        raw["request_interval_ms"], "job.request_interval_ms"
    )
    if environment == "paper" and request_interval_ms < 1000:
        raise EodBlockedError("paper request_interval_ms must be at least 1000")
    if environment == "live" and request_interval_ms < 100:
        raise EodBlockedError("live request_interval_ms must be at least 100")

    def normalize_sources(role: str) -> list[dict[str, Any]]:
        sources = raw[role]
        if not isinstance(sources, list):
            raise EodBlockedError(f"job.{role} must be an array")
        normalized: list[dict[str, Any]] = []
        seen: set[str] = set()
        for index, source in enumerate(sources):
            if not isinstance(source, dict):
                raise EodBlockedError(f"job.{role}[{index}] must be an object")
            item = universe.normalize_source_descriptor(
                source,
                ("OFFICIAL_MASTER" if role == "official_sources" else "BROKER_MASTER"),
                as_of,
                job_directory,
                index,
            )
            if item["exchange"] in seen:
                raise EodBlockedError(f"duplicate {role} exchange {item['exchange']}")
            seen.add(item["exchange"])
            normalized.append(item)
        missing = set(KR_EXCHANGES) - seen
        if missing:
            raise EodBlockedError(f"job.{role} missing KR exchanges {sorted(missing)}")
        return sorted(normalized, key=lambda item: item["exchange"])

    base_catalog = str(raw["base_eod_catalog"] or "").strip()
    if base_catalog:
        base_path = Path(base_catalog).expanduser()
        if not base_path.is_absolute():
            base_path = (job_directory / base_path).resolve()
        if not base_path.is_file():
            raise EodBlockedError(f"base_eod_catalog is not a file: {base_path}")
        base_catalog = str(base_path)

    coverage = universe.normalize_catalog_coverage_contract(
        raw["catalog_coverage_contract"]
    )
    return {
        "schema": JOB_SCHEMA,
        "as_of": as_of,
        "analysis_date": analysis_date,
        "environment": environment,
        "output_directory": str(output_directory),
        "history_start_date": history_start,
        "minimum_sessions": minimum_sessions,
        "request_interval_ms": request_interval_ms,
        "official_sources": normalize_sources("official_sources"),
        "broker_sources": normalize_sources("broker_sources"),
        "catalog_coverage_contract": coverage,
        "base_eod_catalog": base_catalog,
    }


def inspect_secrets_file(path: Path) -> bool:
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        return False
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
        raise EodBlockedError("KIS secrets path must be a regular non-symlink file")
    if metadata.st_uid != os.getuid() or stat.S_IMODE(metadata.st_mode) != 0o600:
        raise EodBlockedError("KIS secrets file must be user-owned mode 0600")
    return True


def parse_dotenv(path: Path) -> dict[str, str]:
    if not inspect_secrets_file(path):
        return {}
    output: dict[str, str] = {}
    for line_number, raw_line in enumerate(
        path.read_text(encoding="utf-8").splitlines(), start=1
    ):
        stripped = raw_line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if stripped.startswith("export "):
            stripped = stripped[7:].lstrip()
        if "=" not in stripped:
            raise EodBlockedError(f"invalid dotenv line {line_number}")
        key, value = stripped.split("=", 1)
        key = key.strip()
        if not DOTENV_KEY.fullmatch(key) or key in output:
            raise EodBlockedError(
                f"invalid or duplicate dotenv key on line {line_number}"
            )
        value = value.strip()
        if value.startswith('"'):
            try:
                decoded = json.loads(value)
            except json.JSONDecodeError as exc:
                raise EodBlockedError(
                    f"invalid quoted dotenv value on line {line_number}"
                ) from exc
            if not isinstance(decoded, str):
                raise EodBlockedError(f"dotenv line {line_number} is not a string")
            value = decoded
        elif value.startswith("'"):
            if len(value) < 2 or not value.endswith("'"):
                raise EodBlockedError(
                    f"invalid quoted dotenv value on line {line_number}"
                )
            value = value[1:-1]
        output[key] = value
    return output


def load_api_credentials(environment: str) -> tuple[str, str, Optional[str]]:
    scope = "LIVE" if environment == "live" else "PAPER"
    path = Path(
        os.environ.get("QTA_SECRETS_FILE", str(DEFAULT_SECRETS_PATH))
    ).expanduser()
    dotenv = parse_dotenv(path)

    def value(generic: str, scoped: str) -> Optional[str]:
        return (
            os.environ.get(generic)
            or os.environ.get(scoped)
            or dotenv.get(scoped)
            or dotenv.get(generic)
        )

    app_key = value("QTA_KIS_APP_KEY", f"QTA_KIS_{scope}_APP_KEY")
    app_secret = value("QTA_KIS_APP_SECRET", f"QTA_KIS_{scope}_APP_SECRET")
    access_token = value("QTA_KIS_ACCESS_TOKEN", f"QTA_KIS_{scope}_ACCESS_TOKEN")
    missing = [
        name
        for name, item in (
            ("QTA_KIS_APP_KEY", app_key),
            ("QTA_KIS_APP_SECRET", app_secret),
        )
        if not item
    ]
    if missing:
        raise EodBlockedError("missing KIS credentials: " + ", ".join(missing))
    return str(app_key), str(app_secret), access_token


class KisReadClient:
    """Read-only KIS HTTP client with deterministic pacing and bounded retries."""

    def __init__(
        self,
        *,
        environment: str,
        app_key: str,
        app_secret: str,
        interval_ms: int,
        access_token: Optional[str] = None,
        transport: Optional[
            Callable[[str, str, Mapping[str, str], Optional[dict[str, Any]]], Any]
        ] = None,
    ) -> None:
        self.base_url = BASE_URLS[environment]
        self.app_key = app_key
        self.app_secret = app_secret
        self.interval_seconds = interval_ms / 1000.0
        self.access_token = access_token
        self.last_request_at = 0.0
        self.request_count = 0
        self.retry_count = 0
        self.transport = transport or self._urllib_transport

    @staticmethod
    def _urllib_transport(
        method: str,
        url: str,
        headers: Mapping[str, str],
        body: Optional[dict[str, Any]],
    ) -> Any:
        payload = None
        if body is not None:
            payload = canonical_json(body).encode("utf-8")
        request = Request(url, data=payload, headers=dict(headers), method=method)
        try:
            with urlopen(request, timeout=15) as response:
                raw = response.read()
                status_code = int(response.status)
        except HTTPError as exc:
            raw = exc.read()
            status_code = int(exc.code)
        except (URLError, TimeoutError, OSError) as exc:
            raise EodBlockedError(f"KIS network failure: {type(exc).__name__}") from exc
        try:
            decoded = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise EodBlockedError(f"KIS returned non-JSON HTTP {status_code}") from exc
        return status_code, decoded

    def _pace(self) -> None:
        now = time.monotonic()
        remaining = self.interval_seconds - (now - self.last_request_at)
        if remaining > 0:
            time.sleep(remaining)
        self.last_request_at = time.monotonic()

    def token(self) -> str:
        if self.access_token:
            return self.access_token
        for attempt in range(2):
            status, body = self.transport(
                "POST",
                f"{self.base_url}{TOKEN_PATH}",
                {"Content-Type": "application/json"},
                {
                    "grant_type": "client_credentials",
                    "appkey": self.app_key,
                    "appsecret": self.app_secret,
                },
            )
            self.request_count += 1
            if status == 200 and isinstance(body, dict) and body.get("access_token"):
                self.access_token = str(body["access_token"])
                return self.access_token
            code = (
                str(body.get("error_code", body.get("msg_cd", "unknown")))
                if isinstance(body, dict)
                else "invalid_body"
            )
            if code == "EGW00133" and attempt == 0:
                self.retry_count += 1
                time.sleep(61.0)
                continue
            raise EodBlockedError(f"KIS token request failed: HTTP {status} {code}")
        raise AssertionError("bounded KIS token loop fell through")

    def get(self, path: str, tr_id: str, params: Mapping[str, str]) -> dict[str, Any]:
        query = urlencode(sorted(params.items()))
        headers = {
            "authorization": f"Bearer {self.token()}",
            "appkey": self.app_key,
            "appsecret": self.app_secret,
            "tr_id": tr_id,
            "custtype": "P",
            "Content-Type": "application/json",
        }
        for attempt in range(4):
            self._pace()
            status, body = self.transport(
                "GET", f"{self.base_url}{path}?{query}", headers, None
            )
            self.request_count += 1
            code = (
                str(body.get("msg_cd", body.get("error_code", "")))
                if isinstance(body, dict)
                else ""
            )
            if status == 200 and isinstance(body, dict) and body.get("rt_cd") == "0":
                return body
            if status == 429 or code == "EGW00201":
                if attempt == 3:
                    break
                self.retry_count += 1
                time.sleep(float(2**attempt))
                continue
            message = (
                str(body.get("msg1", body.get("error_description", code)))
                if isinstance(body, dict)
                else "invalid_body"
            )
            raise EodBlockedError(
                f"KIS read failed: HTTP {status} {code or 'unknown'} {message}"
            )
        raise EodBlockedError("KIS read rate limit persisted after bounded retries")


def decimal_text(value: Any, label: str, *, allow_zero: bool = False) -> str:
    try:
        parsed = Decimal(str(value).replace(",", "").strip())
    except (InvalidOperation, ValueError) as exc:
        raise EodBlockedError(f"{label} is not decimal") from exc
    if not parsed.is_finite() or (parsed < 0 if allow_zero else parsed <= 0):
        raise EodBlockedError(
            f"{label} must be {'non-negative' if allow_zero else 'positive'}"
        )
    rendered = format(parsed, "f")
    return rendered.rstrip("0").rstrip(".") if "." in rendered else rendered


def normalize_stock_row(raw: Mapping[str, Any]) -> dict[str, str]:
    try:
        day = datetime.strptime(str(raw["stck_bsop_date"]), "%Y%m%d").date()
    except (KeyError, ValueError) as exc:
        raise EodBlockedError("KIS stock row has invalid stck_bsop_date") from exc
    output = {
        "date": day.isoformat(),
        "open": decimal_text(raw.get("stck_oprc"), "stck_oprc"),
        "high": decimal_text(raw.get("stck_hgpr"), "stck_hgpr"),
        "low": decimal_text(raw.get("stck_lwpr"), "stck_lwpr"),
        "close": decimal_text(raw.get("stck_clpr"), "stck_clpr"),
        "volume": decimal_text(raw.get("acml_vol", "0"), "acml_vol", allow_zero=True),
    }
    output["adjusted_close"] = output["close"]
    if Decimal(output["high"]) < max(
        Decimal(output["open"]), Decimal(output["low"]), Decimal(output["close"])
    ):
        raise EodBlockedError("KIS stock row high is inconsistent")
    if Decimal(output["low"]) > min(
        Decimal(output["open"]), Decimal(output["high"]), Decimal(output["close"])
    ):
        raise EodBlockedError("KIS stock row low is inconsistent")
    return output


def normalize_index_row(raw: Mapping[str, Any]) -> dict[str, str]:
    day_value = raw.get("stck_bsop_date", raw.get("bstp_nmix_prdy_vrss_sign"))
    try:
        day = datetime.strptime(str(day_value), "%Y%m%d").date()
    except ValueError as exc:
        raise EodBlockedError("KIS index row has invalid stck_bsop_date") from exc
    output = {
        "date": day.isoformat(),
        "open": decimal_text(raw.get("bstp_nmix_oprc"), "bstp_nmix_oprc"),
        "high": decimal_text(raw.get("bstp_nmix_hgpr"), "bstp_nmix_hgpr"),
        "low": decimal_text(raw.get("bstp_nmix_lwpr"), "bstp_nmix_lwpr"),
        "close": decimal_text(raw.get("bstp_nmix_prpr"), "bstp_nmix_prpr"),
        "volume": decimal_text(
            raw.get("acml_vol", "0") or "0", "acml_vol", allow_zero=True
        ),
    }
    output["adjusted_close"] = output["close"]
    if Decimal(output["high"]) < max(
        Decimal(output["open"]), Decimal(output["low"]), Decimal(output["close"])
    ):
        raise EodBlockedError("KIS index row high is inconsistent")
    if Decimal(output["low"]) > min(
        Decimal(output["open"]), Decimal(output["high"]), Decimal(output["close"])
    ):
        raise EodBlockedError("KIS index row low is inconsistent")
    return output


def fetch_history(
    client: KisReadClient,
    *,
    kind: str,
    symbol: str,
    start: date,
    end: date,
) -> list[dict[str, str]]:
    if kind == "stock":
        path, tr_id, market_code = STOCK_PATH, STOCK_TR_ID, "J"
        parser = normalize_stock_row
    elif kind == "index":
        path, tr_id, market_code = INDEX_PATH, INDEX_TR_ID, "U"
        parser = normalize_index_row
    else:
        raise EodBlockedError("history kind must be stock or index")
    cursor = end
    output: dict[str, dict[str, str]] = {}
    for _ in range(32):
        params = {
            "FID_COND_MRKT_DIV_CODE": market_code,
            "FID_INPUT_ISCD": symbol,
            "FID_INPUT_DATE_1": start.strftime("%Y%m%d"),
            "FID_INPUT_DATE_2": cursor.strftime("%Y%m%d"),
            "FID_PERIOD_DIV_CODE": "D",
        }
        if kind == "stock":
            params["FID_ORG_ADJ_PRC"] = "0"
        body = client.get(path, tr_id, params)
        rows = body.get("output2")
        if not isinstance(rows, list):
            raise EodBlockedError("KIS history output2 must be an array")
        if not rows:
            break
        normalized = [parser(row) for row in rows if isinstance(row, dict)]
        if not normalized:
            raise EodBlockedError("KIS history page has no valid rows")
        for row in normalized:
            day = date.fromisoformat(row["date"])
            if start <= day <= end:
                existing = output.get(row["date"])
                if existing is not None and existing != row:
                    raise EodBlockedError(
                        f"KIS returned conflicting duplicate date {row['date']}"
                    )
                output[row["date"]] = row
        oldest = min(date.fromisoformat(row["date"]) for row in normalized)
        if oldest <= start:
            break
        next_cursor = oldest - timedelta(days=1)
        if next_cursor >= cursor:
            raise EodBlockedError("KIS history pagination did not move backward")
        cursor = next_cursor
    else:
        raise EodBlockedError("KIS history exceeded 32 pages")
    return [output[key] for key in sorted(output)]


def read_csv_rows(path: Path) -> list[dict[str, str]]:
    if not path.is_file():
        return []
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        if tuple(reader.fieldnames or ()) != (
            "date",
            "open",
            "high",
            "low",
            "close",
            "adjusted_close",
            "volume",
        ):
            raise EodBlockedError(f"cached EOD header mismatch: {path}")
        rows = [dict(row) for row in reader]
    normalized: list[dict[str, str]] = []
    for row in rows:
        canonical = {
            "date": require_iso_date(row["date"], f"{path}.date"),
            "open": decimal_text(row["open"], f"{path}.open"),
            "high": decimal_text(row["high"], f"{path}.high"),
            "low": decimal_text(row["low"], f"{path}.low"),
            "close": decimal_text(row["close"], f"{path}.close"),
            "adjusted_close": decimal_text(
                row["adjusted_close"], f"{path}.adjusted_close"
            ),
            "volume": decimal_text(row["volume"], f"{path}.volume", allow_zero=True),
        }
        normalized.append(canonical)
    if len({row["date"] for row in normalized}) != len(normalized):
        raise EodBlockedError(f"cached EOD contains duplicate dates: {path}")
    return sorted(normalized, key=lambda row: row["date"])


def write_csv_rows(path: Path, rows: list[dict[str, str]]) -> None:
    fields = (
        "date",
        "open",
        "high",
        "low",
        "close",
        "adjusted_close",
        "volume",
    )
    with tempfile.SpooledTemporaryFile(
        mode="w+", encoding="utf-8", newline=""
    ) as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
        handle.seek(0)
        atomic_write(path, handle.read().encode("utf-8"))


def update_history_file(
    client: KisReadClient,
    *,
    kind: str,
    symbol: str,
    path: Path,
    start: date,
    end: date,
    minimum_sessions: int,
) -> tuple[list[dict[str, str]], int]:
    cached = read_csv_rows(path)
    cached_map = {
        row["date"]: row
        for row in cached
        if start <= date.fromisoformat(row["date"]) <= end
    }
    before_requests = client.request_count
    if end.isoformat() not in cached_map or len(cached_map) < minimum_sessions:
        fetched = fetch_history(client, kind=kind, symbol=symbol, start=start, end=end)
        for row in fetched:
            existing = cached_map.get(row["date"])
            if existing is not None and existing != row:
                cached_map[row["date"]] = row
            else:
                cached_map[row["date"]] = row
    rows = [cached_map[key] for key in sorted(cached_map)]
    if end.isoformat() not in cached_map:
        raise EodBlockedError(f"{symbol} has no completed row on {end.isoformat()}")
    if len(rows) < minimum_sessions:
        raise EodBlockedError(
            f"{symbol} needs {minimum_sessions} sessions; found {len(rows)}"
        )
    write_csv_rows(path, rows)
    return rows, client.request_count - before_requests


def tick_size(exchange: str, price: Decimal) -> Decimal:
    if exchange == "KOSPI":
        bands = (
            (Decimal("1000"), Decimal("1")),
            (Decimal("5000"), Decimal("5")),
            (Decimal("10000"), Decimal("10")),
            (Decimal("50000"), Decimal("50")),
            (Decimal("100000"), Decimal("100")),
            (Decimal("500000"), Decimal("500")),
        )
        fallback = Decimal("1000")
    elif exchange == "KOSDAQ":
        bands = (
            (Decimal("1000"), Decimal("1")),
            (Decimal("5000"), Decimal("5")),
            (Decimal("10000"), Decimal("10")),
            (Decimal("50000"), Decimal("50")),
        )
        fallback = Decimal("100")
    else:
        raise EodBlockedError("tick exchange must be KOSPI or KOSDAQ")
    for ceiling, tick in bands:
        if price < ceiling:
            return tick
    return fallback


def catalog_type(reasons: list[str]) -> str:
    mappings = (
        ("instrument_etf", "ETF"),
        ("instrument_etn", "ETN"),
        ("instrument_preferred", "PREFERRED"),
        ("instrument_spac", "SPAC"),
        ("instrument_unit", "UNIT"),
        ("instrument_right", "RIGHT"),
        ("instrument_warrant", "WARRANT"),
        ("test_issue", "TEST"),
        ("abnormal_status", "ABNORMAL"),
    )
    for reason, rendered in mappings:
        if reason in reasons:
            return rendered
    return "COMMON"


def load_base_catalog(path: str) -> dict[tuple[str, str], dict[str, str]]:
    if not path:
        return {}
    output: dict[tuple[str, str], dict[str, str]] = {}
    with Path(path).open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        if tuple(reader.fieldnames or ()) != CATALOG_FIELDS:
            raise EodBlockedError("base_eod_catalog header mismatch")
        for row in reader:
            exchange = str(row["exchange"]).upper()
            symbol = str(row["canonical_symbol"]).upper()
            if exchange not in universe.EXCHANGES or not symbol:
                raise EodBlockedError(
                    "base_eod_catalog has invalid exchange or canonical_symbol"
                )
            key = (exchange, symbol)
            if key in output:
                raise EodBlockedError(f"duplicate base catalog row {key}")
            output[key] = {field: str(row[field]) for field in CATALOG_FIELDS}
    return output


def write_catalog(path: Path, rows: list[dict[str, str]]) -> None:
    with tempfile.SpooledTemporaryFile(
        mode="w+", encoding="utf-8", newline=""
    ) as handle:
        writer = csv.DictWriter(handle, fieldnames=CATALOG_FIELDS, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
        handle.seek(0)
        atomic_write(path, handle.read().encode("utf-8"))


def build_bundle(
    job: dict[str, Any],
    *,
    client: Optional[KisReadClient] = None,
) -> dict[str, Any]:
    output_root = Path(job["output_directory"])
    output_root.mkdir(parents=True, exist_ok=True)
    if client is None:
        app_key, app_secret, access_token = load_api_credentials(job["environment"])
        client = KisReadClient(
            environment=job["environment"],
            app_key=app_key,
            app_secret=app_secret,
            access_token=access_token,
            interval_ms=job["request_interval_ms"],
        )
    start = date.fromisoformat(job["history_start_date"])
    end = date.fromisoformat(job["analysis_date"])
    minimum_sessions = int(job["minimum_sessions"])
    official_descriptors = {
        item["exchange"]: item
        for item in job["official_sources"]
        if item["exchange"] in KR_EXCHANGES
    }
    broker_descriptors = {
        item["exchange"]: item
        for item in job["broker_sources"]
        if item["exchange"] in KR_EXCHANGES
    }
    official_rows = {
        exchange: {
            row.symbol: row
            for row in universe.parse_master_source(official_descriptors[exchange])
        }
        for exchange in KR_EXCHANGES
    }
    broker_rows = {
        exchange: {
            row.symbol: row
            for row in universe.parse_master_source(broker_descriptors[exchange])
        }
        for exchange in KR_EXCHANGES
    }

    benchmark_paths: dict[str, Path] = {}
    benchmark_results: dict[str, dict[str, Any]] = {}
    for exchange in KR_EXCHANGES:
        benchmark_path = output_root / "benchmarks" / f"{BENCHMARK_IDS[exchange]}.csv"
        rows, requests = update_history_file(
            client,
            kind="index",
            symbol=INDEX_CODES[exchange],
            path=benchmark_path,
            start=start,
            end=end,
            minimum_sessions=minimum_sessions,
        )
        benchmark_paths[exchange] = benchmark_path.resolve()
        benchmark_results[exchange] = {
            "symbol": INDEX_CODES[exchange],
            "path": str(benchmark_path.resolve()),
            "sha256": sha256_file(benchmark_path),
            "sessions": len(rows),
            "requests": requests,
        }

    catalog = load_base_catalog(job["base_eod_catalog"])
    failures: list[dict[str, str]] = []
    eligible_count = 0
    ready_count = 0
    cache_hits = 0
    coverage_by_exchange = {
        exchange: {
            "official": len(official_rows[exchange]),
            "eligible": 0,
            "ready": 0,
            "failed": 0,
        }
        for exchange in KR_EXCHANGES
    }
    for exchange in KR_EXCHANGES:
        official_descriptor = official_descriptors[exchange]
        broker_descriptor = broker_descriptors[exchange]
        for symbol in sorted(official_rows[exchange]):
            official = official_rows[exchange][symbol]
            broker = broker_rows[exchange].get(symbol)
            reasons = universe.metadata_exclusion_reasons(official, official_descriptor)
            if broker is None:
                reasons.append("not_kis_tradable")
            else:
                reasons.extend(
                    universe.metadata_exclusion_reasons(broker, broker_descriptor)
                )
            reasons = sorted(set(reasons))
            instrument_type = catalog_type(reasons)
            ticker_path = output_root / "stocks" / exchange / f"{symbol}.csv"
            latest_close = ""
            resolved_tick = ""
            source_name = "KIS_OPEN_API_ADJUSTED_DAILY:FHKST03010100"
            data_path = ""
            if not reasons:
                eligible_count += 1
                coverage_by_exchange[exchange]["eligible"] += 1
                before = client.request_count
                try:
                    rows, _ = update_history_file(
                        client,
                        kind="stock",
                        symbol=symbol,
                        path=ticker_path,
                        start=start,
                        end=end,
                        minimum_sessions=minimum_sessions,
                    )
                    if client.request_count == before:
                        cache_hits += 1
                    latest_close = rows[-1]["close"]
                    resolved_tick = format(
                        tick_size(exchange, Decimal(latest_close)), "f"
                    )
                    data_path = str(ticker_path.resolve())
                    ready_count += 1
                    coverage_by_exchange[exchange]["ready"] += 1
                except EodBlockedError as exc:
                    coverage_by_exchange[exchange]["failed"] += 1
                    failures.append(
                        {
                            "exchange": exchange,
                            "symbol": symbol,
                            "reason": str(exc),
                        }
                    )
            catalog[(exchange, symbol)] = {
                "exchange": exchange,
                "canonical_symbol": symbol,
                "data_symbol": symbol,
                "broker_symbol": symbol if broker is not None else "",
                "instrument_type": instrument_type,
                "benchmark_id": BENCHMARK_IDS[exchange],
                "ticker_csv": data_path,
                "benchmark_csv": str(benchmark_paths[exchange]),
                "tick_rule_id": (
                    f"KRX_GUIDE_OBSERVED_{job['as_of']}_{exchange}"
                    if resolved_tick
                    else ""
                ),
                "tick_effective_date": job["analysis_date"] if resolved_tick else "",
                "tick_reference_price": latest_close,
                "resolved_tick_size": resolved_tick,
                "source_name": source_name,
            }

    catalog_rows = [
        catalog[key]
        for key in sorted(
            catalog,
            key=lambda item: (
                universe.EXCHANGES.index(item[0]),
                item[1],
            ),
        )
    ]
    catalog_path = output_root / "eod-catalog.csv"
    write_catalog(catalog_path, catalog_rows)

    build_spec = {
        "schema": universe.BUILD_SPEC_SCHEMA,
        "as_of": job["as_of"],
        "analysis_date": job["analysis_date"],
        "official_sources": job["official_sources"],
        "broker_sources": job["broker_sources"],
        "eod_catalog": {
            "source_id": f"kis-eod-catalog-{job['as_of']}",
            "provider": "KIS",
            "as_of": job["as_of"],
            "path": str(catalog_path.resolve()),
            "encoding": "utf-8",
        },
        "catalog_coverage_contract": job["catalog_coverage_contract"],
    }
    build_spec_path = output_root / "universe-build-spec.json"
    atomic_write_json(build_spec_path, build_spec)

    source_hashes = [
        {
            "source_id": item["source_id"],
            "exchange": item["exchange"],
            "role": role,
            "path": item["path"],
            "sha256": sha256_file(Path(item["path"])),
        }
        for role, sources in (
            ("OFFICIAL_MASTER", job["official_sources"]),
            ("BROKER_MASTER", job["broker_sources"]),
        )
        for item in sources
    ]
    source_hashes.sort(key=lambda item: item["source_id"])
    receipt_without_hash = {
        "schema": RECEIPT_SCHEMA,
        "status": "READY",
        "as_of": job["as_of"],
        "analysis_date": job["analysis_date"],
        "environment": job["environment"],
        "api_mutation_count": 0,
        "adjusted_price_parameter": "FID_ORG_ADJ_PRC=0",
        "minimum_sessions": minimum_sessions,
        "eligible_symbols": eligible_count,
        "ready_symbols": ready_count,
        "cache_hits": cache_hits,
        "coverage_by_exchange": coverage_by_exchange,
        "failed_symbols": failures,
        "request_count": client.request_count,
        "retry_count": client.retry_count,
        "benchmarks": benchmark_results,
        "source_hashes": source_hashes,
        "catalog": {
            "path": str(catalog_path.resolve()),
            "sha256": sha256_file(catalog_path),
            "rows": len(catalog_rows),
        },
        "build_spec": {
            "path": str(build_spec_path.resolve()),
            "sha256": sha256_file(build_spec_path),
        },
        "completed_at": datetime.now(timezone.utc).isoformat(),
    }
    receipt = {
        **receipt_without_hash,
        "receipt_hash": sha256_bytes(
            canonical_json(receipt_without_hash).encode("utf-8")
        ),
    }
    atomic_write_json(output_root / "eod-bundle-receipt.json", receipt)
    return receipt


def download_bytes(url: str) -> bytes:
    request = Request(
        url,
        headers={
            "User-Agent": "mrcha-skills/quant-stock-technical",
            "Accept": "*/*",
        },
    )
    try:
        with urlopen(request, timeout=30) as response:
            return response.read()
    except (HTTPError, URLError, TimeoutError, OSError) as exc:
        raise EodBlockedError(
            f"source snapshot download failed: {url} ({type(exc).__name__})"
        ) from exc


def snapshot_sources(as_of: str, output_directory: Path) -> dict[str, Any]:
    require_iso_date(as_of, "as_of")
    output_directory.mkdir(parents=True, exist_ok=True)
    kind_path = output_directory / "krx-kind-corporate-list.xls"
    atomic_write(kind_path, download_bytes(KIND_URL))
    try:
        text = kind_path.read_text(encoding="cp949")
    except UnicodeDecodeError as exc:
        raise EodBlockedError("KRX KIND snapshot is not CP949 HTML") from exc
    if "종목코드" not in text or "시장구분" not in text:
        raise EodBlockedError("KRX KIND snapshot is missing expected headers")

    master_paths: dict[str, Path] = {}
    for exchange in KR_EXCHANGES:
        payload = download_bytes(KIS_MASTER_URLS[exchange])
        with tempfile.TemporaryDirectory() as temporary:
            archive_path = Path(temporary) / "master.zip"
            archive_path.write_bytes(payload)
            try:
                with zipfile.ZipFile(archive_path) as archive:
                    names = archive.namelist()
                    expected = KIS_MASTER_NAMES[exchange]
                    matches = [
                        name
                        for name in names
                        if Path(name).name.lower() == expected.lower()
                    ]
                    if len(matches) != 1:
                        raise EodBlockedError(
                            f"{exchange} master archive must contain {expected}"
                        )
                    master_payload = archive.read(matches[0])
            except zipfile.BadZipFile as exc:
                raise EodBlockedError(
                    f"{exchange} master download is not a ZIP"
                ) from exc
        master_path = output_directory / KIS_MASTER_NAMES[exchange]
        atomic_write(master_path, master_payload)
        master_paths[exchange] = master_path

    official_sources = [
        {
            "source_id": f"krx-kind-{exchange.lower()}-{as_of}",
            "provider": "KRX",
            "exchange": exchange,
            "as_of": as_of,
            "path": str(kind_path.resolve()),
            "format": "KRX_KIND_HTML",
            "encoding": "cp949",
            "delimiter": "",
            "skip_rows": 0,
            "columns": {},
            "normal_status_values": [],
        }
        for exchange in KR_EXCHANGES
    ]
    broker_sources = [
        {
            "source_id": f"kis-master-{exchange.lower()}-{as_of}",
            "provider": "KIS",
            "exchange": exchange,
            "as_of": as_of,
            "path": str(master_paths[exchange].resolve()),
            "format": "KIS_KRX_MASTER",
            "encoding": "cp949",
            "delimiter": "",
            "skip_rows": 0,
            "columns": {},
            "normal_status_values": [],
        }
        for exchange in KR_EXCHANGES
    ]
    receipt_without_hash = {
        "schema": SNAPSHOT_SCHEMA,
        "as_of": as_of,
        "official_sources": official_sources,
        "broker_sources": broker_sources,
        "files": [
            {
                "path": str(path.resolve()),
                "sha256": sha256_file(path),
            }
            for path in [kind_path, *master_paths.values()]
        ],
        "source_urls": [KIND_URL, *KIS_MASTER_URLS.values()],
        "captured_at": datetime.now(timezone.utc).isoformat(),
    }
    receipt = {
        **receipt_without_hash,
        "receipt_hash": sha256_bytes(
            canonical_json(receipt_without_hash).encode("utf-8")
        ),
    }
    atomic_write_json(output_directory / "source-snapshot.json", receipt)
    return receipt


def _synthetic_rows(end: date, count: int, *, index: bool) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    cursor = end
    for offset in range(count):
        while cursor.weekday() >= 5:
            cursor -= timedelta(days=1)
        close = Decimal("1000") + Decimal(offset)
        if index:
            rows.append(
                {
                    "stck_bsop_date": cursor.strftime("%Y%m%d"),
                    "bstp_nmix_oprc": str(close),
                    "bstp_nmix_hgpr": str(close + 10),
                    "bstp_nmix_lwpr": str(close - 10),
                    "bstp_nmix_prpr": str(close),
                    "acml_vol": "0",
                }
            )
        else:
            rows.append(
                {
                    "stck_bsop_date": cursor.strftime("%Y%m%d"),
                    "stck_oprc": str(close),
                    "stck_hgpr": str(close + 10),
                    "stck_lwpr": str(close - 10),
                    "stck_clpr": str(close),
                    "acml_vol": str(100000 + offset),
                }
            )
        cursor -= timedelta(days=1)
    return rows


def self_test() -> None:
    end = date(2026, 7, 24)
    stock_rows = _synthetic_rows(end, 800, index=False)
    index_rows = _synthetic_rows(end, 800, index=True)

    def transport(
        method: str,
        url: str,
        headers: Mapping[str, str],
        body: Optional[dict[str, Any]],
    ) -> Any:
        del headers, body
        if method == "POST":
            return 200, {"access_token": "fixture-token"}
        query = dict(item.split("=", 1) for item in url.split("?", 1)[1].split("&"))
        cursor = datetime.strptime(query["FID_INPUT_DATE_2"], "%Y%m%d").date()
        source = index_rows if INDEX_PATH in url else stock_rows
        page_size = 50 if index_rows is source else 100
        selected = [
            row
            for row in source
            if datetime.strptime(row["stck_bsop_date"], "%Y%m%d").date() <= cursor
        ][:page_size]
        return 200, {"rt_cd": "0", "output2": selected}

    client = KisReadClient(
        environment="live",
        app_key="fixture",
        app_secret="fixture",
        interval_ms=100,
        access_token="fixture-token",
        transport=transport,
    )
    client.interval_seconds = 0
    start = date(2023, 1, 1)
    stocks = fetch_history(client, kind="stock", symbol="005930", start=start, end=end)
    indexes = fetch_history(client, kind="index", symbol="0001", start=start, end=end)
    assert len(stocks) >= 756
    assert len(indexes) >= 756
    assert stocks[-1]["date"] == end.isoformat()
    assert stocks[-1]["adjusted_close"] == stocks[-1]["close"]
    assert tick_size("KOSPI", Decimal("999")) == Decimal("1")
    assert tick_size("KOSPI", Decimal("100000")) == Decimal("500")
    assert tick_size("KOSDAQ", Decimal("500000")) == Decimal("100")
    with tempfile.TemporaryDirectory() as temporary:
        path = Path(temporary) / "history.csv"
        write_csv_rows(path, stocks)
        assert read_csv_rows(path) == stocks
    print(
        json.dumps(
            {
                "self_test": "PASS",
                "stock_sessions": len(stocks),
                "index_sessions": len(indexes),
                "requests": client.request_count,
            },
            sort_keys=True,
        )
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--self-test", action="store_true")
    subparsers = parser.add_subparsers(dest="command")
    collect = subparsers.add_parser("collect")
    collect.add_argument("--job", required=True)
    snapshot = subparsers.add_parser("snapshot-sources")
    snapshot.add_argument("--as-of", required=True)
    snapshot.add_argument("--output-directory", required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        if args.self_test:
            self_test()
            return 0
        if args.command == "snapshot-sources":
            receipt = snapshot_sources(
                args.as_of, Path(args.output_directory).expanduser().resolve()
            )
        elif args.command == "collect":
            job_path = Path(args.job).expanduser().resolve()
            raw = json.loads(job_path.read_text(encoding="utf-8"))
            receipt = build_bundle(normalize_job(raw, job_path.parent))
        else:
            raise EodBlockedError("use collect --job, snapshot-sources, or --self-test")
    except (
        EodBlockedError,
        universe.UniverseBlockedError,
        json.JSONDecodeError,
    ) as exc:
        print(
            json.dumps(
                {"status": "BLOCKED", "reason": str(exc)},
                ensure_ascii=False,
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        return 2
    print(json.dumps(receipt, ensure_ascii=False, sort_keys=True))
    return 0 if receipt.get("status", "READY") == "READY" else 2


if __name__ == "__main__":
    raise SystemExit(main())

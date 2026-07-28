#!/usr/bin/env python3
"""Build a resumable, source-hashed U.S. adjusted-EOD bundle from KIS."""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
import zipfile
from collections.abc import Callable, Mapping
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Optional
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import build_universe_manifest as universe
import fetch_kis_kr_eod as shared

JOB_SCHEMA = "qta-kis-us-eod-job/v1"
RECEIPT_SCHEMA = "qta-kis-us-eod-receipt/v1"
SNAPSHOT_SCHEMA = "qta-us-source-snapshot/v1"
US_EXCHANGES = ("NYSE", "NASDAQ")
JOB_FIELDS = shared.JOB_FIELDS
STOCK_PATH = "/uapi/overseas-price/v1/quotations/dailyprice"
STOCK_TR_ID = "HHDFS76240000"
EXCHANGE_CODES = {"NYSE": "NYS", "NASDAQ": "NAS"}
BENCHMARK_IDS = {"NYSE": "NYSE_COMPOSITE", "NASDAQ": "NASDAQ_COMPOSITE"}
BENCHMARK_SYMBOLS = {"NYSE": "^NYA", "NASDAQ": "^IXIC"}
NASDAQ_URLS = {
    "NASDAQ": "https://www.nasdaqtrader.com/dynamic/SymDir/nasdaqlisted.txt",
    "NYSE": "https://www.nasdaqtrader.com/dynamic/SymDir/otherlisted.txt",
}
KIS_MASTER_URLS = {
    "NASDAQ": "https://new.real.download.dws.co.kr/common/master/nasmst.cod.zip",
    "NYSE": "https://new.real.download.dws.co.kr/common/master/nysmst.cod.zip",
}
KIS_MASTER_NAMES = {"NASDAQ": "nasmst.cod", "NYSE": "nysmst.cod"}
YAHOO_CHART_URL = "https://query1.finance.yahoo.com/v8/finance/chart"


class UsEodBlockedError(shared.EodBlockedError):
    """Raised when a U.S. EOD bundle cannot be completed without guessing."""


class InvalidOhlcGeometry(UsEodBlockedError):
    """One parseable provider row has OHLC values that cannot be admitted."""

    def __init__(self, reason: str, row: dict[str, str]):
        super().__init__(reason)
        self.reason = reason
        self.row = row


def validate_ohlc_geometry(row: dict[str, str]) -> None:
    if Decimal(row["high"]) < max(
        Decimal(row["open"]), Decimal(row["low"]), Decimal(row["close"])
    ):
        raise InvalidOhlcGeometry(
            "KIS overseas row high is inconsistent",
            row,
        )
    if Decimal(row["low"]) > min(
        Decimal(row["open"]), Decimal(row["high"]), Decimal(row["close"])
    ):
        raise InvalidOhlcGeometry(
            "KIS overseas row low is inconsistent",
            row,
        )


def normalize_job(raw: Any, job_directory: Path) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise UsEodBlockedError("job must be one JSON object")
    shared.require_exact_fields(raw, JOB_FIELDS, "job")
    if raw["schema"] != JOB_SCHEMA:
        raise UsEodBlockedError(f"job.schema must be {JOB_SCHEMA}")
    as_of = shared.require_iso_date(raw["as_of"], "job.as_of")
    analysis_date = shared.require_iso_date(raw["analysis_date"], "job.analysis_date")
    history_start = shared.require_iso_date(
        raw["history_start_date"], "job.history_start_date"
    )
    if history_start >= analysis_date:
        raise UsEodBlockedError("history_start_date must be before analysis_date")
    if as_of < analysis_date:
        raise UsEodBlockedError("as_of cannot be before analysis_date")
    environment = str(raw["environment"]).lower()
    if environment not in shared.BASE_URLS:
        raise UsEodBlockedError("environment must be live or paper")
    output_directory = Path(str(raw["output_directory"])).expanduser()
    if not output_directory.is_absolute():
        output_directory = (job_directory / output_directory).resolve()
    minimum_sessions = shared.require_positive_integer(
        raw["minimum_sessions"], "job.minimum_sessions"
    )
    if minimum_sessions < 756:
        raise UsEodBlockedError("minimum_sessions must be at least 756")
    interval = shared.require_positive_integer(
        raw["request_interval_ms"], "job.request_interval_ms"
    )
    if environment == "paper" and interval < 1000:
        raise UsEodBlockedError("paper request_interval_ms must be at least 1000")
    if environment == "live" and interval < 100:
        raise UsEodBlockedError("live request_interval_ms must be at least 100")

    def sources_for(role: str) -> list[dict[str, Any]]:
        raw_sources = raw[role]
        if not isinstance(raw_sources, list):
            raise UsEodBlockedError(f"job.{role} must be an array")
        normalized: list[dict[str, Any]] = []
        seen: set[str] = set()
        expected_role = (
            "OFFICIAL_MASTER" if role == "official_sources" else "BROKER_MASTER"
        )
        for index, source in enumerate(raw_sources):
            if not isinstance(source, dict):
                raise UsEodBlockedError(f"job.{role}[{index}] must be an object")
            item = universe.normalize_source_descriptor(
                source, expected_role, as_of, job_directory, index
            )
            if item["exchange"] in seen:
                raise UsEodBlockedError(f"duplicate {role} exchange {item['exchange']}")
            seen.add(item["exchange"])
            normalized.append(item)
        missing = set(US_EXCHANGES) - seen
        if missing:
            raise UsEodBlockedError(
                f"job.{role} missing U.S. exchanges {sorted(missing)}"
            )
        return sorted(
            normalized, key=lambda item: universe.EXCHANGES.index(item["exchange"])
        )

    base_catalog = str(raw["base_eod_catalog"] or "").strip()
    if base_catalog:
        base_path = Path(base_catalog).expanduser()
        if not base_path.is_absolute():
            base_path = (job_directory / base_path).resolve()
        if not base_path.is_file():
            raise UsEodBlockedError(f"base_eod_catalog is not a file: {base_path}")
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
        "request_interval_ms": interval,
        "official_sources": sources_for("official_sources"),
        "broker_sources": sources_for("broker_sources"),
        "catalog_coverage_contract": coverage,
        "base_eod_catalog": base_catalog,
    }


def normalize_stock_row(raw: Mapping[str, Any]) -> dict[str, str]:
    try:
        day = datetime.strptime(str(raw["xymd"]), "%Y%m%d").date()
    except (KeyError, ValueError) as exc:
        raise UsEodBlockedError("KIS overseas row has invalid xymd") from exc
    output = {
        "date": day.isoformat(),
        "open": shared.decimal_text(raw.get("open"), "open"),
        "high": shared.decimal_text(raw.get("high"), "high"),
        "low": shared.decimal_text(raw.get("low"), "low"),
        "close": shared.decimal_text(raw.get("clos"), "clos"),
        "volume": shared.decimal_text(
            raw.get("tvol", "0") or "0", "tvol", allow_zero=True
        ),
    }
    output["adjusted_close"] = output["close"]
    validate_ohlc_geometry(output)
    return output


def fetch_stock_history(
    client: shared.KisReadClient,
    *,
    exchange: str,
    symbol: str,
    start: date,
    end: date,
    invalid_rows: Optional[list[dict[str, Any]]] = None,
) -> list[dict[str, str]]:
    cursor = end
    output: dict[str, dict[str, str]] = {}
    for _ in range(32):
        body = client.get(
            STOCK_PATH,
            STOCK_TR_ID,
            {
                "AUTH": "",
                "EXCD": EXCHANGE_CODES[exchange],
                "SYMB": symbol,
                "GUBN": "0",
                "BYMD": cursor.strftime("%Y%m%d"),
                "MODP": "1",
            },
        )
        rows = body.get("output2")
        if not isinstance(rows, list):
            raise UsEodBlockedError("KIS overseas output2 must be an array")
        if not rows:
            break
        page_dates: list[date] = []
        normalized: list[dict[str, str]] = []
        for raw_row in rows:
            if not isinstance(raw_row, dict):
                continue
            try:
                page_dates.append(
                    datetime.strptime(str(raw_row["xymd"]), "%Y%m%d").date()
                )
            except (KeyError, ValueError) as exc:
                raise UsEodBlockedError(
                    "KIS overseas row has invalid xymd"
                ) from exc
            try:
                normalized.append(normalize_stock_row(raw_row))
            except InvalidOhlcGeometry as exc:
                invalid_day = date.fromisoformat(exc.row["date"])
                if invalid_day == end:
                    raise UsEodBlockedError(
                        f"{symbol} completed cutoff row has invalid OHLC geometry: "
                        f"{exc.reason}"
                    ) from exc
                if invalid_rows is not None and start <= invalid_day <= end:
                    invalid_rows.append(
                        {
                            "date": exc.row["date"],
                            "reason": exc.reason,
                            "row": exc.row,
                            "row_sha256": shared.sha256_bytes(
                                shared.canonical_json(exc.row).encode("utf-8")
                            ),
                        }
                    )
        if not normalized:
            if page_dates:
                oldest = min(page_dates)
                if oldest <= start:
                    break
                next_cursor = oldest - timedelta(days=1)
                if next_cursor >= cursor:
                    raise UsEodBlockedError(
                        "KIS overseas pagination did not move backward"
                    )
                cursor = next_cursor
                continue
            raise UsEodBlockedError("KIS overseas page has no valid rows")
        for row in normalized:
            day = date.fromisoformat(row["date"])
            if start <= day <= end:
                existing = output.get(row["date"])
                if existing is not None and existing != row:
                    raise UsEodBlockedError(
                        f"KIS returned conflicting duplicate date {row['date']}"
                    )
                output[row["date"]] = row
        oldest = min(page_dates)
        if oldest <= start:
            break
        next_cursor = oldest - timedelta(days=1)
        if next_cursor >= cursor:
            raise UsEodBlockedError("KIS overseas pagination did not move backward")
        cursor = next_cursor
    else:
        raise UsEodBlockedError("KIS overseas history exceeded 32 pages")
    return [output[key] for key in sorted(output)]


def update_stock_file(
    client: shared.KisReadClient,
    *,
    exchange: str,
    symbol: str,
    path: Path,
    start: date,
    end: date,
    minimum_sessions: int,
) -> tuple[list[dict[str, str]], int, list[dict[str, Any]]]:
    invalid_rows: list[dict[str, Any]] = []
    cached: list[dict[str, str]] = []
    for row in shared.read_csv_rows(path):
        try:
            validate_ohlc_geometry(row)
        except InvalidOhlcGeometry as exc:
            invalid_day = date.fromisoformat(exc.row["date"])
            if invalid_day == end:
                raise UsEodBlockedError(
                    f"{symbol} cached cutoff row has invalid OHLC geometry: "
                    f"{exc.reason}"
                ) from exc
            if start <= invalid_day <= end:
                invalid_rows.append(
                    {
                        "date": exc.row["date"],
                        "reason": exc.reason,
                        "row": exc.row,
                        "row_sha256": shared.sha256_bytes(
                            shared.canonical_json(exc.row).encode("utf-8")
                        ),
                    }
                )
            continue
        cached.append(row)
    merged = {
        row["date"]: row
        for row in cached
        if start <= date.fromisoformat(row["date"]) <= end
    }
    before = client.request_count
    if end.isoformat() not in merged or len(merged) < minimum_sessions:
        for row in fetch_stock_history(
            client,
            exchange=exchange,
            symbol=symbol,
            start=start,
            end=end,
            invalid_rows=invalid_rows,
        ):
            merged[row["date"]] = row
    rows = [merged[key] for key in sorted(merged)]
    if end.isoformat() not in merged:
        raise UsEodBlockedError(f"{symbol} has no completed row on {end.isoformat()}")
    if len(rows) < minimum_sessions:
        raise UsEodBlockedError(
            f"{symbol} needs {minimum_sessions} sessions; found {len(rows)}"
        )
    shared.write_csv_rows(path, rows)
    return rows, client.request_count - before, invalid_rows


def load_invalid_row_audit(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise UsEodBlockedError("invalid-row audit is not readable JSON") from exc
    if (
        not isinstance(value, dict)
        or value.get("schema") != "qta-kis-us-invalid-eod-rows/v1"
        or not isinstance(value.get("records"), list)
    ):
        raise UsEodBlockedError("invalid-row audit has an unsupported schema")
    return [item for item in value["records"] if isinstance(item, dict)]


def fetch_yahoo_benchmark(symbol: str, start: date, end: date) -> list[dict[str, str]]:
    period1 = int(
        datetime.combine(start, datetime.min.time(), timezone.utc).timestamp()
    )
    period2 = int(
        datetime.combine(
            end + timedelta(days=1), datetime.min.time(), timezone.utc
        ).timestamp()
    )
    query = urlencode(
        {
            "period1": str(period1),
            "period2": str(period2),
            "interval": "1d",
            "events": "div,splits",
        }
    )
    request = Request(
        f"{YAHOO_CHART_URL}/{symbol}?{query}",
        headers={"User-Agent": "mrcha-skills/quant-stock-technical"},
    )
    try:
        with urlopen(request, timeout=30) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (
        HTTPError,
        URLError,
        TimeoutError,
        OSError,
        UnicodeDecodeError,
        json.JSONDecodeError,
    ) as exc:
        raise UsEodBlockedError(
            f"benchmark download failed for {symbol}: {type(exc).__name__}"
        ) from exc
    try:
        result = payload["chart"]["result"][0]
        timestamps = result["timestamp"]
        quote = result["indicators"]["quote"][0]
        adjusted = result["indicators"]["adjclose"][0]["adjclose"]
    except (KeyError, IndexError, TypeError) as exc:
        raise UsEodBlockedError(f"benchmark response malformed for {symbol}") from exc
    rows: list[dict[str, str]] = []
    for index, stamp in enumerate(timestamps):
        try:
            day = datetime.fromtimestamp(int(stamp), timezone.utc).date()
            close = Decimal(str(quote["close"][index]))
            adjusted_close = Decimal(str(adjusted[index]))
            ratio = adjusted_close / close
            row = {
                "date": day.isoformat(),
                "open": shared.decimal_text(
                    Decimal(str(quote["open"][index])) * ratio, "benchmark.open"
                ),
                "high": shared.decimal_text(
                    Decimal(str(quote["high"][index])) * ratio, "benchmark.high"
                ),
                "low": shared.decimal_text(
                    Decimal(str(quote["low"][index])) * ratio, "benchmark.low"
                ),
                "close": shared.decimal_text(adjusted_close, "benchmark.close"),
                "adjusted_close": shared.decimal_text(
                    adjusted_close, "benchmark.adjusted_close"
                ),
                "volume": shared.decimal_text(
                    quote["volume"][index] or "0",
                    "benchmark.volume",
                    allow_zero=True,
                ),
            }
        except (IndexError, TypeError, ArithmeticError):
            continue
        if start <= day <= end:
            rows.append(row)
    if len({row["date"] for row in rows}) != len(rows):
        raise UsEodBlockedError(f"benchmark contains duplicate dates for {symbol}")
    return sorted(rows, key=lambda row: row["date"])


def update_benchmark_file(
    *,
    symbol: str,
    path: Path,
    start: date,
    end: date,
    minimum_sessions: int,
    loader: Callable[[str, date, date], list[dict[str, str]]],
) -> list[dict[str, str]]:
    cached = shared.read_csv_rows(path)
    merged = {
        row["date"]: row
        for row in cached
        if start <= date.fromisoformat(row["date"]) <= end
    }
    if end.isoformat() not in merged or len(merged) < minimum_sessions:
        for row in loader(symbol, start, end):
            merged[row["date"]] = row
    rows = [merged[key] for key in sorted(merged)]
    if end.isoformat() not in merged:
        raise UsEodBlockedError(
            f"benchmark {symbol} has no completed row on {end.isoformat()}"
        )
    if len(rows) < minimum_sessions:
        raise UsEodBlockedError(
            f"benchmark {symbol} needs {minimum_sessions} sessions; found {len(rows)}"
        )
    shared.write_csv_rows(path, rows)
    return rows


def tick_size(price: Decimal) -> Decimal:
    if price <= 0:
        raise UsEodBlockedError("reference price must be positive")
    return Decimal("0.0001") if price < Decimal("1") else Decimal("0.01")


def build_bundle(
    job: dict[str, Any],
    *,
    client: Optional[shared.KisReadClient] = None,
    benchmark_loader: Callable[
        [str, date, date], list[dict[str, str]]
    ] = fetch_yahoo_benchmark,
) -> dict[str, Any]:
    output_root = Path(job["output_directory"])
    output_root.mkdir(parents=True, exist_ok=True)
    if client is None:
        key, secret, token = shared.load_api_credentials(job["environment"])
        client = shared.KisReadClient(
            environment=job["environment"],
            app_key=key,
            app_secret=secret,
            access_token=token,
            interval_ms=job["request_interval_ms"],
        )
    start = date.fromisoformat(job["history_start_date"])
    end = date.fromisoformat(job["analysis_date"])
    minimum = int(job["minimum_sessions"])
    official_descriptors = {
        item["exchange"]: item
        for item in job["official_sources"]
        if item["exchange"] in US_EXCHANGES
    }
    broker_descriptors = {
        item["exchange"]: item
        for item in job["broker_sources"]
        if item["exchange"] in US_EXCHANGES
    }
    official_rows = {
        exchange: {
            row.symbol: row
            for row in universe.parse_master_source(official_descriptors[exchange])
        }
        for exchange in US_EXCHANGES
    }
    broker_rows = {
        exchange: {
            row.symbol: row
            for row in universe.parse_master_source(broker_descriptors[exchange])
        }
        for exchange in US_EXCHANGES
    }

    benchmark_paths: dict[str, Path] = {}
    benchmark_results: dict[str, dict[str, Any]] = {}
    for exchange in US_EXCHANGES:
        path = output_root / "benchmarks" / f"{BENCHMARK_IDS[exchange]}.csv"
        rows = update_benchmark_file(
            symbol=BENCHMARK_SYMBOLS[exchange],
            path=path,
            start=start,
            end=end,
            minimum_sessions=minimum,
            loader=benchmark_loader,
        )
        benchmark_paths[exchange] = path.resolve()
        benchmark_results[exchange] = {
            "symbol": BENCHMARK_SYMBOLS[exchange],
            "provider": "YAHOO_CHART",
            "path": str(path.resolve()),
            "sha256": shared.sha256_file(path),
            "sessions": len(rows),
        }

    catalog = shared.load_base_catalog(job["base_eod_catalog"])
    failures: list[dict[str, str]] = []
    invalid_audit_path = output_root / "invalid-eod-rows.json"
    invalid_records = load_invalid_row_audit(invalid_audit_path)
    ready_count = 0
    eligible_count = 0
    cache_hits = 0
    coverage = {
        exchange: {
            "official": len(official_rows[exchange]),
            "eligible": 0,
            "ready": 0,
            "failed": 0,
        }
        for exchange in US_EXCHANGES
    }
    for exchange in US_EXCHANGES:
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
            instrument_type = shared.catalog_type(reasons)
            ticker_path = output_root / "stocks" / exchange / f"{symbol}.csv"
            data_path = ""
            latest_close = ""
            resolved_tick = ""
            if not reasons:
                eligible_count += 1
                coverage[exchange]["eligible"] += 1
                before = client.request_count
                try:
                    rows, _, observed_invalid = update_stock_file(
                        client,
                        exchange=exchange,
                        symbol=symbol,
                        path=ticker_path,
                        start=start,
                        end=end,
                        minimum_sessions=minimum,
                    )
                    if client.request_count != before or observed_invalid:
                        invalid_records = [
                            item
                            for item in invalid_records
                            if not (
                                item.get("exchange") == exchange
                                and item.get("symbol") == symbol
                            )
                        ]
                        invalid_records.extend(
                            {
                                "exchange": exchange,
                                "symbol": symbol,
                                **item,
                            }
                            for item in observed_invalid
                        )
                    if client.request_count == before:
                        cache_hits += 1
                    latest_close = rows[-1]["close"]
                    resolved_tick = format(tick_size(Decimal(latest_close)), "f")
                    data_path = str(ticker_path.resolve())
                    ready_count += 1
                    coverage[exchange]["ready"] += 1
                except shared.EodBlockedError as exc:
                    coverage[exchange]["failed"] += 1
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
                    f"US_NMS_CONSERVATIVE_{job['as_of']}" if resolved_tick else ""
                ),
                "tick_effective_date": job["analysis_date"] if resolved_tick else "",
                "tick_reference_price": latest_close,
                "resolved_tick_size": resolved_tick,
                "source_name": "KIS_OPEN_API_ADJUSTED_DAILY:HHDFS76240000",
            }

    rows = [
        catalog[key]
        for key in sorted(
            catalog,
            key=lambda item: (universe.EXCHANGES.index(item[0]), item[1]),
        )
    ]
    catalog_path = output_root / "eod-catalog.csv"
    shared.write_catalog(catalog_path, rows)
    invalid_records.sort(
        key=lambda item: (
            str(item.get("exchange", "")),
            str(item.get("symbol", "")),
            str(item.get("date", "")),
        )
    )
    invalid_records = [
        item
        for _, item in {
            (
                str(item.get("exchange", "")),
                str(item.get("symbol", "")),
                str(item.get("date", "")),
            ): item
            for item in invalid_records
        }.items()
    ]
    invalid_records.sort(
        key=lambda item: (
            str(item.get("exchange", "")),
            str(item.get("symbol", "")),
            str(item.get("date", "")),
        )
    )
    invalid_audit = {
        "schema": "qta-kis-us-invalid-eod-rows/v1",
        "analysis_date": job["analysis_date"],
        "records": invalid_records,
    }
    shared.atomic_write_json(invalid_audit_path, invalid_audit)
    build_spec = {
        "schema": universe.BUILD_SPEC_SCHEMA,
        "as_of": job["as_of"],
        "analysis_date": job["analysis_date"],
        "official_sources": shared.build_spec_sources(job["official_sources"]),
        "broker_sources": shared.build_spec_sources(job["broker_sources"]),
        "eod_catalog": {
            "source_id": f"kis-cross-market-eod-catalog-{job['as_of']}",
            "provider": "KIS",
            "as_of": job["as_of"],
            "path": str(catalog_path.resolve()),
            "encoding": "utf-8",
        },
        "catalog_coverage_contract": job["catalog_coverage_contract"],
    }
    build_spec_path = output_root / "universe-build-spec.json"
    shared.atomic_write_json(build_spec_path, build_spec)
    source_hashes = [
        {
            "source_id": item["source_id"],
            "exchange": item["exchange"],
            "role": role,
            "path": item["path"],
            "sha256": shared.sha256_file(Path(item["path"])),
        }
        for role, sources in (
            ("OFFICIAL_MASTER", job["official_sources"]),
            ("BROKER_MASTER", job["broker_sources"]),
        )
        for item in sources
    ]
    source_hashes.sort(key=lambda item: item["source_id"])
    without_hash = {
        "schema": RECEIPT_SCHEMA,
        "status": "READY",
        "as_of": job["as_of"],
        "analysis_date": job["analysis_date"],
        "environment": job["environment"],
        "api_mutation_count": 0,
        "adjusted_price_parameter": "MODP=1",
        "minimum_sessions": minimum,
        "eligible_symbols": eligible_count,
        "ready_symbols": ready_count,
        "cache_hits": cache_hits,
        "coverage_by_exchange": coverage,
        "failed_symbols": failures,
        "invalid_eod_rows": {
            "path": str(invalid_audit_path.resolve()),
            "sha256": shared.sha256_file(invalid_audit_path),
            "count": len(invalid_records),
            "policy": "EXCLUDED_WITHOUT_INTERPOLATION",
        },
        "request_count": client.request_count,
        "retry_count": client.retry_count,
        "benchmarks": benchmark_results,
        "source_hashes": source_hashes,
        "catalog": {
            "path": str(catalog_path.resolve()),
            "sha256": shared.sha256_file(catalog_path),
            "rows": len(rows),
        },
        "build_spec": {
            "path": str(build_spec_path.resolve()),
            "sha256": shared.sha256_file(build_spec_path),
        },
        "completed_at": datetime.now(timezone.utc).isoformat(),
    }
    receipt = {
        **without_hash,
        "receipt_hash": shared.sha256_bytes(
            shared.canonical_json(without_hash).encode("utf-8")
        ),
    }
    shared.atomic_write_json(output_root / "eod-bundle-receipt.json", receipt)
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
        raise UsEodBlockedError(
            f"source snapshot download failed: {url} ({type(exc).__name__})"
        ) from exc


def snapshot_sources(as_of: str, output_directory: Path) -> dict[str, Any]:
    shared.require_iso_date(as_of, "as_of")
    output_directory.mkdir(parents=True, exist_ok=True)
    official_paths: dict[str, Path] = {}
    broker_paths: dict[str, Path] = {}
    for exchange in US_EXCHANGES:
        official_path = output_directory / (
            "otherlisted.txt" if exchange == "NYSE" else "nasdaqlisted.txt"
        )
        shared.atomic_write(official_path, download_bytes(NASDAQ_URLS[exchange]))
        official_paths[exchange] = official_path
        payload = download_bytes(KIS_MASTER_URLS[exchange])
        with tempfile.TemporaryDirectory() as temporary:
            archive_path = Path(temporary) / "master.zip"
            archive_path.write_bytes(payload)
            try:
                with zipfile.ZipFile(archive_path) as archive:
                    expected = KIS_MASTER_NAMES[exchange]
                    matches = [
                        name
                        for name in archive.namelist()
                        if Path(name).name.lower() == expected.lower()
                    ]
                    if len(matches) != 1:
                        raise UsEodBlockedError(
                            f"{exchange} master archive must contain {expected}"
                        )
                    master_payload = archive.read(matches[0])
            except zipfile.BadZipFile as exc:
                raise UsEodBlockedError(
                    f"{exchange} master download is not a ZIP"
                ) from exc
        broker_path = output_directory / KIS_MASTER_NAMES[exchange]
        shared.atomic_write(broker_path, master_payload)
        broker_paths[exchange] = broker_path

    official_sources = [
        {
            "source_id": f"nasdaq-trader-{exchange.lower()}-{as_of}",
            "provider": "NASDAQ_TRADER",
            "exchange": exchange,
            "as_of": as_of,
            "path": str(official_paths[exchange].resolve()),
            "format": ("NASDAQ_LISTED" if exchange == "NASDAQ" else "NASDAQ_OTHER"),
            "encoding": "utf-8",
            "delimiter": "|",
            "skip_rows": 0,
            "columns": {},
            "normal_status_values": ["N"] if exchange == "NASDAQ" else [],
        }
        for exchange in US_EXCHANGES
    ]
    broker_sources = [
        {
            "source_id": f"kis-{exchange.lower()}-master-{as_of}",
            "provider": "KIS",
            "exchange": exchange,
            "as_of": as_of,
            "path": str(broker_paths[exchange].resolve()),
            "format": "KIS_OVERSEAS_MASTER",
            "encoding": "cp949",
            "delimiter": "\t",
            "skip_rows": 0,
            "columns": {},
            "normal_status_values": [],
        }
        for exchange in US_EXCHANGES
    ]
    for index, descriptor in enumerate(official_sources):
        normalized = universe.normalize_source_descriptor(
            descriptor, "OFFICIAL_MASTER", as_of, output_directory, index
        )
        universe.parse_master_source(normalized)
    for index, descriptor in enumerate(broker_sources):
        normalized = universe.normalize_source_descriptor(
            descriptor, "BROKER_MASTER", as_of, output_directory, index
        )
        universe.parse_master_source(normalized)
    receipt = {
        "schema": SNAPSHOT_SCHEMA,
        "as_of": as_of,
        "official_sources": official_sources,
        "broker_sources": broker_sources,
        "files": sorted(
            [
                {
                    "path": str(path.resolve()),
                    "sha256": shared.sha256_file(path),
                    "bytes": path.stat().st_size,
                }
                for path in (*official_paths.values(), *broker_paths.values())
            ],
            key=lambda item: item["path"],
        ),
        "api_mutation_count": 0,
        "completed_at": datetime.now(timezone.utc).isoformat(),
    }
    shared.atomic_write_json(output_directory / "source-snapshot.json", receipt)
    return receipt


def self_test() -> None:
    end = date(2026, 7, 24)
    raw = shared._synthetic_rows(end, 800, index=False)
    overseas = [
        {
            "xymd": item["stck_bsop_date"],
            "open": item["stck_oprc"],
            "high": item["stck_hgpr"],
            "low": item["stck_lwpr"],
            "clos": item["stck_clpr"],
            "tvol": item["acml_vol"],
        }
        for item in raw
    ]

    def transport(
        method: str,
        url: str,
        headers: Mapping[str, str],
        body: Optional[dict[str, Any]],
    ) -> tuple[int, dict[str, Any]]:
        del headers, body
        if method != "GET":
            raise AssertionError("self-test must be read-only")
        query = dict(item.split("=", 1) for item in url.split("?", 1)[1].split("&"))
        cursor = datetime.strptime(query["BYMD"], "%Y%m%d").date()
        selected = [
            row
            for row in overseas
            if datetime.strptime(row["xymd"], "%Y%m%d").date() <= cursor
        ][:100]
        return 200, {"rt_cd": "0", "output2": selected}

    client = shared.KisReadClient(
        environment="live",
        app_key="fixture",
        app_secret="fixture",
        interval_ms=100,
        access_token="fixture-token",
        transport=transport,
    )
    client.interval_seconds = 0
    rows = fetch_stock_history(
        client,
        exchange="NASDAQ",
        symbol="AAPL",
        start=end - timedelta(days=1200),
        end=end,
    )
    if len(rows) != 800 or rows[-1]["date"] != end.isoformat():
        raise AssertionError("U.S. pagination self-test failed")
    if tick_size(Decimal("0.5")) != Decimal("0.0001"):
        raise AssertionError("sub-dollar tick self-test failed")
    if tick_size(Decimal("10")) != Decimal("0.01"):
        raise AssertionError("dollar tick self-test failed")
    print(
        json.dumps(
            {
                "self_test": "PASS",
                "stock_sessions": len(rows),
                "requests": client.request_count,
                "api_mutation_count": 0,
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
            raise UsEodBlockedError(
                "use collect --job, snapshot-sources, or --self-test"
            )
    except (
        shared.EodBlockedError,
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

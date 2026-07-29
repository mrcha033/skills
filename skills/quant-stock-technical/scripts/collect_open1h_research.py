#!/usr/bin/env python3
"""Collect deterministic first-60-minute KIS bars for QTA research."""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from datetime import date
from decimal import Decimal, InvalidOperation, ROUND_HALF_EVEN
from pathlib import Path
from typing import Any, Mapping

import fetch_kis_kr_eod as shared
import screen_universe as screen


JOB_SCHEMA = "qta-open1h-research-job/v1"
BARS_SCHEMA = "qta-open1h-bars/v1"
SNAPSHOT_SCHEMA = "qta-open1h-snapshot/v1"
JOB_FIELDS = {
    "schema",
    "manifest_path",
    "market",
    "session_date",
    "output_directory",
    "environment",
    "request_interval_ms",
}
MARKET_CONTRACTS = {
    "KR": {
        "exchanges": ("KOSPI", "KOSDAQ"),
        "interval_minutes": 1,
        "window_start": "090000",
        "window_end": "100000",
        "expected_bars": 61,
        "path": (
            "/uapi/domestic-stock/v1/quotations/"
            "inquire-time-dailychartprice"
        ),
        "tr_id": "FHKST03010230",
    },
    "US": {
        "exchanges": ("NYSE", "NASDAQ"),
        "interval_minutes": 5,
        "window_start": "093000",
        "window_end": "103000",
        "expected_bars": 13,
        "path": (
            "/uapi/overseas-price/v1/quotations/"
            "inquire-time-itemchartprice"
        ),
        "tr_id": "HHDFS76950200",
    },
}
US_EXCHANGE_CODES = {"NYSE": "NYS", "NASDAQ": "NAS"}
BAR_FIELDS = {
    "time",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "turnover",
}
BARS_FIELDS = {
    "schema",
    "source",
    "market",
    "exchange",
    "canonical_symbol",
    "broker_symbol",
    "session_date",
    "interval_minutes",
    "window_start",
    "window_end",
    "bars",
    "bars_hash",
    "summary",
}
SUMMARY_FIELDS = {
    "bar_count",
    "start_open",
    "end_close",
    "window_high",
    "window_low",
    "window_volume",
    "window_turnover",
    "return_60m_bps",
    "maximum_excursion_bps",
    "minimum_excursion_bps",
}


class ResearchBlockedError(ValueError):
    """Raised when first-hour research data cannot be used without guessing."""


def exact_fields(value: Mapping[str, Any], expected: set[str], label: str) -> None:
    if set(value) != expected:
        raise ResearchBlockedError(
            f"{label} fields mismatch; missing={sorted(expected - set(value))}, "
            f"extra={sorted(set(value) - expected)}"
        )


def require_date(value: Any, field: str) -> str:
    if not isinstance(value, str):
        raise ResearchBlockedError(f"{field} must be YYYY-MM-DD")
    try:
        parsed = date.fromisoformat(value)
    except ValueError as exc:
        raise ResearchBlockedError(f"{field} must be YYYY-MM-DD") from exc
    if parsed.isoformat() != value:
        raise ResearchBlockedError(f"{field} must be YYYY-MM-DD")
    return value


def require_absolute_path(value: Any, field: str) -> Path:
    path = Path(str(value)).expanduser()
    if not path.is_absolute():
        raise ResearchBlockedError(f"{field} must be absolute")
    return path


def decimal_value(
    value: Any,
    field: str,
    *,
    allow_zero: bool = False,
) -> Decimal:
    if isinstance(value, bool):
        raise ResearchBlockedError(f"{field} must be decimal")
    try:
        parsed = Decimal(str(value).replace(",", "").strip())
    except (InvalidOperation, ValueError) as exc:
        raise ResearchBlockedError(f"{field} must be decimal") from exc
    if not parsed.is_finite() or (
        parsed < 0 if allow_zero else parsed <= 0
    ):
        qualifier = "nonnegative" if allow_zero else "positive"
        raise ResearchBlockedError(f"{field} must be {qualifier}")
    return parsed


def decimal_text(value: Decimal) -> str:
    rendered = format(value, "f")
    if "." in rendered:
        rendered = rendered.rstrip("0").rstrip(".")
    return rendered or "0"


def bps(numerator: Decimal, denominator: Decimal) -> str:
    value = (
        (numerator / denominator - Decimal(1)) * Decimal(10000)
    ).quantize(Decimal("0.01"), rounding=ROUND_HALF_EVEN)
    return format(value, "f")


def load_job(path: Path) -> dict[str, Any]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ResearchBlockedError(f"job must be readable JSON: {path}") from exc
    if not isinstance(raw, dict):
        raise ResearchBlockedError("job must contain one JSON object")
    exact_fields(raw, JOB_FIELDS, "job")
    if raw["schema"] != JOB_SCHEMA:
        raise ResearchBlockedError(f"job.schema must be {JOB_SCHEMA}")
    market = str(raw["market"]).upper()
    if market not in MARKET_CONTRACTS:
        raise ResearchBlockedError("job.market must be KR or US")
    session_date = require_date(raw["session_date"], "job.session_date")
    manifest_path = require_absolute_path(raw["manifest_path"], "job.manifest_path")
    if not manifest_path.is_file() or manifest_path.is_symlink():
        raise ResearchBlockedError(
            "job.manifest_path must be a regular non-symlink file"
        )
    output_directory = require_absolute_path(
        raw["output_directory"], "job.output_directory"
    )
    if output_directory.exists() and (
        not output_directory.is_dir() or output_directory.is_symlink()
    ):
        raise ResearchBlockedError(
            "job.output_directory must be a non-symlink directory"
        )
    environment = str(raw["environment"]).lower()
    if environment not in shared.BASE_URLS:
        raise ResearchBlockedError("job.environment must be live or paper")
    interval = raw["request_interval_ms"]
    if (
        isinstance(interval, bool)
        or not isinstance(interval, int)
        or interval < (1000 if environment == "paper" else 100)
    ):
        floor = 1000 if environment == "paper" else 100
        raise ResearchBlockedError(
            f"job.request_interval_ms must be an integer >= {floor}"
        )
    try:
        manifest_raw = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest = screen.normalize_manifest_v2(manifest_raw)
    except (
        OSError,
        json.JSONDecodeError,
        screen.ScreenBlockedError,
    ) as exc:
        raise ResearchBlockedError(f"manifest validation failed: {exc}") from exc
    if manifest["as_of"] != session_date:
        raise ResearchBlockedError(
            "manifest.as_of must equal the research session_date"
        )
    if manifest["analysis_date"] >= session_date:
        raise ResearchBlockedError(
            "manifest.analysis_date must precede the research session_date"
        )
    instruments = [
        instrument
        for instrument in manifest["instruments"]
        if instrument["market"] == market
    ]
    if not instruments:
        raise ResearchBlockedError("manifest has no instruments for job.market")
    return {
        "schema": JOB_SCHEMA,
        "manifest_path": str(manifest_path),
        "manifest": manifest,
        "market": market,
        "session_date": session_date,
        "output_directory": output_directory,
        "environment": environment,
        "request_interval_ms": interval,
        "instruments": sorted(
            instruments,
            key=lambda item: (
                MARKET_CONTRACTS[market]["exchanges"].index(item["exchange"]),
                item["canonical_symbol"],
            ),
        ),
    }


def normalize_bar(
    raw: Mapping[str, Any],
    *,
    market: str,
    session_date: str,
) -> dict[str, str] | None:
    compact_date = session_date.replace("-", "")
    if market == "KR":
        raw_date = str(raw.get("stck_bsop_date", "")).strip()
        raw_time = str(raw.get("stck_cntg_hour", "")).strip()
        if raw_date != compact_date:
            return None
        fields = {
            "open": raw.get("stck_oprc"),
            "high": raw.get("stck_hgpr"),
            "low": raw.get("stck_lwpr"),
            "close": raw.get("stck_prpr"),
            "volume": raw.get("cntg_vol", "0") or "0",
            "turnover": raw.get("acml_tr_pbmn", "0") or "0",
        }
    else:
        raw_date = str(raw.get("xymd", "")).strip()
        raw_time = str(raw.get("xhms", "")).strip()
        if raw_date != compact_date:
            return None
        fields = {
            "open": raw.get("open"),
            "high": raw.get("high"),
            "low": raw.get("low"),
            "close": raw.get("last"),
            "volume": raw.get("evol", "0") or "0",
            "turnover": raw.get("eamt", "0") or "0",
        }
    if len(raw_time) != 6 or not raw_time.isdigit():
        raise ResearchBlockedError("KIS minute row has invalid local time")
    output = {
        "time": raw_time,
        "open": decimal_text(decimal_value(fields["open"], "minute open")),
        "high": decimal_text(decimal_value(fields["high"], "minute high")),
        "low": decimal_text(decimal_value(fields["low"], "minute low")),
        "close": decimal_text(decimal_value(fields["close"], "minute close")),
        "volume": decimal_text(
            decimal_value(fields["volume"], "minute volume", allow_zero=True)
        ),
        "turnover": decimal_text(
            decimal_value(fields["turnover"], "minute turnover", allow_zero=True)
        ),
    }
    high = Decimal(output["high"])
    low = Decimal(output["low"])
    if high < max(
        Decimal(output["open"]),
        low,
        Decimal(output["close"]),
    ):
        raise ResearchBlockedError("KIS minute row high is inconsistent")
    if low > min(
        Decimal(output["open"]),
        high,
        Decimal(output["close"]),
    ):
        raise ResearchBlockedError("KIS minute row low is inconsistent")
    return output


def normalized_window_bars(
    raw_rows: Any,
    *,
    market: str,
    session_date: str,
) -> list[dict[str, str]]:
    if not isinstance(raw_rows, list) or any(
        not isinstance(item, dict) for item in raw_rows
    ):
        raise ResearchBlockedError("KIS minute output must be an object array")
    contract = MARKET_CONTRACTS[market]
    by_time: dict[str, dict[str, str]] = {}
    for raw in raw_rows:
        bar = normalize_bar(
            raw,
            market=market,
            session_date=session_date,
        )
        if bar is None:
            continue
        if not (
            contract["window_start"]
            <= bar["time"]
            <= contract["window_end"]
        ):
            continue
        existing = by_time.get(bar["time"])
        if existing is not None and existing != bar:
            raise ResearchBlockedError(
                f"conflicting duplicate minute bar at {bar['time']}"
            )
        by_time[bar["time"]] = bar
    bars = [by_time[key] for key in sorted(by_time)]
    if not bars:
        raise ResearchBlockedError("KIS response has no first-hour bars")
    return bars


def minute_of_day(value: str) -> int:
    if len(value) != 6 or not value.isdigit():
        raise ResearchBlockedError("bar time must be HHMMSS")
    hour = int(value[:2])
    minute = int(value[2:4])
    second = int(value[4:])
    if hour > 23 or minute > 59 or second != 0:
        raise ResearchBlockedError("bar time must be a whole valid minute")
    return hour * 60 + minute


def coverage_summary(
    bars: list[dict[str, str]],
    *,
    market: str,
) -> dict[str, Any]:
    contract = MARKET_CONTRACTS[market]
    first_time = bars[0]["time"]
    last_time = bars[-1]["time"]
    first_minute = minute_of_day(first_time)
    last_minute = minute_of_day(last_time)
    start_minute = minute_of_day(contract["window_start"])
    end_minute = minute_of_day(contract["window_end"])
    expected = int(contract["expected_bars"])
    interval = int(contract["interval_minutes"])
    if (
        first_time == contract["window_start"]
        and last_time == contract["window_end"]
        and len(bars) == expected
    ):
        status = "COMPLETE_GRID"
    elif (
        first_time == contract["window_start"]
        and last_time == contract["window_end"]
    ):
        status = "EXACT_ENDPOINTS_SPARSE"
    elif (
        first_minute <= start_minute + interval
        and last_minute >= end_minute - interval
    ):
        status = "NEAR_ENDPOINTS"
    else:
        status = "PARTIAL_WINDOW"
    return {
        "coverage_status": status,
        "first_bar_time": first_time,
        "last_bar_time": last_time,
        "expected_bar_count": expected,
        "missing_bar_count": max(0, expected - len(bars)),
        "coverage_fraction": format(
            Decimal(len(bars)) / Decimal(expected), "f"
        ),
        "observed_span_minutes": last_minute - first_minute,
    }


def summarize_bars(
    bars: list[dict[str, str]],
    *,
    market: str,
) -> dict[str, Any]:
    start_open = Decimal(bars[0]["open"])
    end_close = Decimal(bars[-1]["close"])
    window_high = max(Decimal(bar["high"]) for bar in bars)
    window_low = min(Decimal(bar["low"]) for bar in bars)
    window_volume = sum(
        (Decimal(bar["volume"]) for bar in bars), Decimal(0)
    )
    if market == "KR":
        # The domestic endpoint exposes cumulative traded value on each bar.
        window_turnover = Decimal(bars[-1]["turnover"])
    else:
        window_turnover = sum(
            (Decimal(bar["turnover"]) for bar in bars), Decimal(0)
        )
    return {
        "bar_count": len(bars),
        "start_open": decimal_text(start_open),
        "end_close": decimal_text(end_close),
        "window_high": decimal_text(window_high),
        "window_low": decimal_text(window_low),
        "window_volume": decimal_text(window_volume),
        "window_turnover": decimal_text(window_turnover),
        "return_60m_bps": bps(end_close, start_open),
        "maximum_excursion_bps": bps(window_high, start_open),
        "minimum_excursion_bps": bps(window_low, start_open),
    }


def fetch_raw_rows(
    client: shared.KisReadClient,
    *,
    market: str,
    exchange: str,
    broker_symbol: str,
    session_date: str,
) -> list[dict[str, Any]]:
    contract = MARKET_CONTRACTS[market]
    if market == "KR":
        body = client.get(
            contract["path"],
            contract["tr_id"],
            {
                "FID_COND_MRKT_DIV_CODE": "J",
                "FID_INPUT_ISCD": broker_symbol,
                "FID_INPUT_HOUR_1": contract["window_end"],
                "FID_INPUT_DATE_1": session_date.replace("-", ""),
                "FID_PW_DATA_INCU_YN": "N",
                "FID_FAKE_TICK_INCU_YN": "",
            },
        )
    else:
        body = client.get(
            contract["path"],
            contract["tr_id"],
            {
                "AUTH": "",
                "EXCD": US_EXCHANGE_CODES[exchange],
                "SYMB": broker_symbol,
                "NMIN": str(contract["interval_minutes"]),
                "PINC": "1",
                "NEXT": "",
                "NREC": "120",
                "FILL": "",
                "KEYB": "",
            },
        )
    rows = body.get("output2")
    if not isinstance(rows, list):
        raise ResearchBlockedError("KIS minute response output2 must be an array")
    return rows


def bars_artifact(
    *,
    instrument: dict[str, Any],
    market: str,
    session_date: str,
    raw_rows: list[dict[str, Any]],
) -> dict[str, Any]:
    contract = MARKET_CONTRACTS[market]
    bars = normalized_window_bars(
        raw_rows,
        market=market,
        session_date=session_date,
    )
    summary = summarize_bars(bars, market=market)
    output = {
        "schema": BARS_SCHEMA,
        "source": {
            "provider": "KIS_OPEN_API",
            "endpoint": contract["path"],
            "tr_id": contract["tr_id"],
            "read_only": True,
        },
        "market": market,
        "exchange": instrument["exchange"],
        "canonical_symbol": instrument["canonical_symbol"],
        "broker_symbol": instrument["broker_symbol"],
        "session_date": session_date,
        "interval_minutes": contract["interval_minutes"],
        "window_start": contract["window_start"],
        "window_end": contract["window_end"],
        "bars": bars,
        "bars_hash": shared.sha256_bytes(
            shared.canonical_json(bars).encode("utf-8")
        ),
        "summary": summary,
    }
    return normalize_bars_artifact(output)


def normalize_bars_artifact(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ResearchBlockedError("bars artifact must be an object")
    exact_fields(value, BARS_FIELDS, "bars artifact")
    if value["schema"] != BARS_SCHEMA:
        raise ResearchBlockedError(f"bars artifact schema must be {BARS_SCHEMA}")
    market = str(value["market"]).upper()
    if market not in MARKET_CONTRACTS:
        raise ResearchBlockedError("bars artifact market must be KR or US")
    exchange = str(value["exchange"]).upper()
    if exchange not in MARKET_CONTRACTS[market]["exchanges"]:
        raise ResearchBlockedError("bars artifact exchange does not match market")
    session_date = require_date(value["session_date"], "bars session_date")
    source = value["source"]
    if not isinstance(source, dict) or source != {
        "provider": "KIS_OPEN_API",
        "endpoint": MARKET_CONTRACTS[market]["path"],
        "tr_id": MARKET_CONTRACTS[market]["tr_id"],
        "read_only": True,
    }:
        raise ResearchBlockedError("bars source contract is invalid")
    if value["interval_minutes"] != MARKET_CONTRACTS[market]["interval_minutes"]:
        raise ResearchBlockedError("bars interval does not match market")
    if value["window_start"] != MARKET_CONTRACTS[market]["window_start"]:
        raise ResearchBlockedError("bars window_start does not match market")
    if value["window_end"] != MARKET_CONTRACTS[market]["window_end"]:
        raise ResearchBlockedError("bars window_end does not match market")
    bars = value["bars"]
    if not isinstance(bars, list):
        raise ResearchBlockedError("bars must be an array")
    for index, bar in enumerate(bars):
        if not isinstance(bar, dict):
            raise ResearchBlockedError(f"bars[{index}] must be an object")
        exact_fields(bar, BAR_FIELDS, f"bars[{index}]")
    normalized_bars = normalized_window_bars(
        [
            (
                {
                    "stck_bsop_date": session_date.replace("-", ""),
                    "stck_cntg_hour": bar["time"],
                    "stck_oprc": bar["open"],
                    "stck_hgpr": bar["high"],
                    "stck_lwpr": bar["low"],
                    "stck_prpr": bar["close"],
                    "cntg_vol": bar["volume"],
                    "acml_tr_pbmn": bar["turnover"],
                }
                if market == "KR"
                else {
                    "xymd": session_date.replace("-", ""),
                    "xhms": bar["time"],
                    "open": bar["open"],
                    "high": bar["high"],
                    "low": bar["low"],
                    "last": bar["close"],
                    "evol": bar["volume"],
                    "eamt": bar["turnover"],
                }
            )
            for bar in bars
        ],
        market=market,
        session_date=session_date,
    )
    expected_hash = shared.sha256_bytes(
        shared.canonical_json(normalized_bars).encode("utf-8")
    )
    if value["bars_hash"] != expected_hash:
        raise ResearchBlockedError("bars_hash does not match bars")
    summary = value["summary"]
    if not isinstance(summary, dict):
        raise ResearchBlockedError("bars summary must be an object")
    exact_fields(summary, SUMMARY_FIELDS, "bars summary")
    expected_summary = summarize_bars(normalized_bars, market=market)
    if summary != expected_summary:
        raise ResearchBlockedError("bars summary does not match bars")
    canonical_symbol = str(value["canonical_symbol"]).strip()
    broker_symbol = str(value["broker_symbol"]).strip()
    if not canonical_symbol or not broker_symbol:
        raise ResearchBlockedError("bars symbols must be non-empty")
    return {
        **value,
        "market": market,
        "exchange": exchange,
        "canonical_symbol": canonical_symbol,
        "broker_symbol": broker_symbol,
        "session_date": session_date,
        "bars": normalized_bars,
        "bars_hash": expected_hash,
        "summary": expected_summary,
    }


def record_from_artifact(path: Path, artifact: dict[str, Any]) -> dict[str, Any]:
    return {
        "market": artifact["market"],
        "exchange": artifact["exchange"],
        "canonical_symbol": artifact["canonical_symbol"],
        "broker_symbol": artifact["broker_symbol"],
        "session_date": artifact["session_date"],
        "interval_minutes": artifact["interval_minutes"],
        "bars_path": str(path.resolve()),
        "bars_file_sha256": shared.sha256_file(path),
        "bars_hash": artifact["bars_hash"],
        **coverage_summary(artifact["bars"], market=artifact["market"]),
        **artifact["summary"],
    }


def cache_path(
    output_directory: Path,
    instrument: dict[str, Any],
) -> Path:
    return (
        output_directory
        / "bars"
        / instrument["exchange"]
        / f"{instrument['canonical_symbol']}.json"
    )


def collect(
    job: dict[str, Any],
    *,
    client: shared.KisReadClient | None = None,
) -> dict[str, Any]:
    output_directory = job["output_directory"]
    output_directory.mkdir(parents=True, exist_ok=True)
    if client is None:
        key, secret, token = shared.load_api_credentials(job["environment"])
        client = shared.KisReadClient(
            environment=job["environment"],
            app_key=key,
            app_secret=secret,
            access_token=token,
            interval_ms=job["request_interval_ms"],
        )
    records: list[dict[str, Any]] = []
    failures: list[dict[str, str]] = []
    cache_hits = 0
    instruments = job["instruments"]
    for index, instrument in enumerate(instruments, start=1):
        path = cache_path(output_directory, instrument)
        try:
            if path.exists():
                artifact = normalize_bars_artifact(
                    json.loads(path.read_text(encoding="utf-8"))
                )
                if (
                    artifact["market"] != job["market"]
                    or artifact["exchange"] != instrument["exchange"]
                    or artifact["canonical_symbol"]
                    != instrument["canonical_symbol"]
                    or artifact["broker_symbol"] != instrument["broker_symbol"]
                    or artifact["session_date"] != job["session_date"]
                ):
                    raise ResearchBlockedError(
                        "cached bars identity does not match instrument"
                    )
                cache_hits += 1
            else:
                raw_rows = fetch_raw_rows(
                    client,
                    market=job["market"],
                    exchange=instrument["exchange"],
                    broker_symbol=instrument["broker_symbol"],
                    session_date=job["session_date"],
                )
                artifact = bars_artifact(
                    instrument=instrument,
                    market=job["market"],
                    session_date=job["session_date"],
                    raw_rows=raw_rows,
                )
                shared.atomic_write_json(path, artifact)
            records.append(record_from_artifact(path, artifact))
        except (
            OSError,
            json.JSONDecodeError,
            shared.EodBlockedError,
            ResearchBlockedError,
        ) as exc:
            failures.append(
                {
                    "market": job["market"],
                    "exchange": instrument["exchange"],
                    "canonical_symbol": instrument["canonical_symbol"],
                    "broker_symbol": instrument["broker_symbol"],
                    "reason": str(exc),
                }
            )
        if index % 100 == 0 or index == len(instruments):
            print(
                shared.canonical_json(
                    {
                        "progress": index,
                        "total": len(instruments),
                        "ready": len(records),
                        "blocked": len(failures),
                        "requests": client.request_count,
                    }
                ),
                file=sys.stderr,
                flush=True,
            )
    records.sort(key=lambda item: (item["exchange"], item["canonical_symbol"]))
    failures.sort(
        key=lambda item: (item["exchange"], item["canonical_symbol"])
    )
    coverage_counts = {
        status: sum(
            record["coverage_status"] == status for record in records
        )
        for status in (
            "COMPLETE_GRID",
            "EXACT_ENDPOINTS_SPARSE",
            "NEAR_ENDPOINTS",
            "PARTIAL_WINDOW",
        )
    }
    without_hash = {
        "schema": SNAPSHOT_SCHEMA,
        "source_skill": "quant-stock-technical",
        "source_contract": {
            "provider": "KIS_OPEN_API",
            "read_only": True,
            "market": job["market"],
            "tr_id": MARKET_CONTRACTS[job["market"]]["tr_id"],
            "endpoint": MARKET_CONTRACTS[job["market"]]["path"],
        },
        "manifest_path": job["manifest_path"],
        "manifest_hash": job["manifest"]["manifest_hash"],
        "analysis_date": job["manifest"]["analysis_date"],
        "session_date": job["session_date"],
        "market": job["market"],
        "window_start": MARKET_CONTRACTS[job["market"]]["window_start"],
        "window_end": MARKET_CONTRACTS[job["market"]]["window_end"],
        "interval_minutes": MARKET_CONTRACTS[job["market"]][
            "interval_minutes"
        ],
        "instrument_count": len(instruments),
        "ready_count": len(records),
        "blocked_count": len(failures),
        "cache_hit_count": cache_hits,
        "request_count": client.request_count,
        "retry_count": client.retry_count,
        "coverage_counts": coverage_counts,
        "records": records,
        "failures": failures,
        "api_mutation_count": 0,
        "live_enabled": False,
    }
    snapshot = {
        **without_hash,
        "snapshot_hash": shared.sha256_bytes(
            shared.canonical_json(without_hash).encode("utf-8")
        ),
    }
    shared.atomic_write_json(output_directory / "open1h-snapshot.json", snapshot)
    return snapshot


def synthetic_rows(
    *,
    market: str,
    session_date: str,
) -> list[dict[str, str]]:
    contract = MARKET_CONTRACTS[market]
    start_hour = 9
    start_minute = 0 if market == "KR" else 30
    rows: list[dict[str, str]] = []
    for index in range(contract["expected_bars"]):
        total_minutes = start_hour * 60 + start_minute + (
            index * contract["interval_minutes"]
        )
        stamp = f"{total_minutes // 60:02d}{total_minutes % 60:02d}00"
        opening = Decimal("100") + Decimal(index) / Decimal("10")
        row = {
            "open": decimal_text(opening),
            "high": decimal_text(opening + Decimal("1")),
            "low": decimal_text(opening - Decimal("1")),
            "close": decimal_text(opening + Decimal("0.5")),
            "volume": "10",
            "turnover": str(1000 + index),
        }
        if market == "KR":
            rows.append(
                {
                    "stck_bsop_date": session_date.replace("-", ""),
                    "stck_cntg_hour": stamp,
                    "stck_oprc": row["open"],
                    "stck_hgpr": row["high"],
                    "stck_lwpr": row["low"],
                    "stck_prpr": row["close"],
                    "cntg_vol": row["volume"],
                    "acml_tr_pbmn": row["turnover"],
                }
            )
        else:
            rows.append(
                {
                    "xymd": session_date.replace("-", ""),
                    "xhms": stamp,
                    "open": row["open"],
                    "high": row["high"],
                    "low": row["low"],
                    "last": row["close"],
                    "evol": row["volume"],
                    "eamt": row["turnover"],
                }
            )
    return list(reversed(rows))


def self_test() -> None:
    for market in ("KR", "US"):
        exchange = MARKET_CONTRACTS[market]["exchanges"][0]
        instrument = {
            "exchange": exchange,
            "canonical_symbol": "005930" if market == "KR" else "TEST",
            "broker_symbol": "005930" if market == "KR" else "TEST",
        }
        artifact = bars_artifact(
            instrument=instrument,
            market=market,
            session_date="2026-07-28",
            raw_rows=synthetic_rows(
                market=market,
                session_date="2026-07-28",
            ),
        )
        assert artifact["summary"]["bar_count"] == (
            MARKET_CONTRACTS[market]["expected_bars"]
        )
        assert Decimal(artifact["summary"]["return_60m_bps"]) > 0
        with tempfile.TemporaryDirectory(
            prefix="qta-open1h-research-"
        ) as temporary:
            path = Path(temporary) / "bars.json"
            shared.atomic_write_json(path, artifact)
            loaded = normalize_bars_artifact(
                json.loads(path.read_text(encoding="utf-8"))
            )
            assert loaded == artifact
    print(
        shared.canonical_json(
            {
                "self_test": "PASS",
                "schema": SNAPSHOT_SCHEMA,
                "markets": ["KR", "US"],
                "api_mutation_count": 0,
                "live_enabled": False,
            }
        )
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--self-test", action="store_true")
    subparsers = parser.add_subparsers(dest="command")
    collect_parser = subparsers.add_parser("collect")
    collect_parser.add_argument("--job", required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        if args.self_test:
            self_test()
            return 0
        if args.command != "collect":
            raise ResearchBlockedError("use collect --job or --self-test")
        job_path = require_absolute_path(args.job, "job path")
        result = collect(load_job(job_path))
    except (
        ResearchBlockedError,
        shared.EodBlockedError,
        screen.ScreenBlockedError,
        OSError,
        json.JSONDecodeError,
    ) as exc:
        print(
            shared.canonical_json(
                {
                    "status": "BLOCKED",
                    "reason": str(exc),
                    "api_mutation_count": 0,
                    "live_enabled": False,
                }
            ),
            file=sys.stderr,
        )
        return 2
    print(shared.canonical_json(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Build a deterministic four-exchange qta-universe-manifest/v2 offline."""

# Python 3.9 compatibility intentionally uses Optional instead of PEP 604 unions.
# ruff: noqa: UP045

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import sys
import tempfile
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Optional

MANIFEST_SCHEMA = "qta-universe-manifest/v2"
BUILD_SPEC_SCHEMA = "qta-universe-build-spec/v1"
TICK_CONTRACT_SCHEMA = "qta-tick-contract/v1"
TICK_CONTRACT_KIND = "RESOLVED_PRICE_LADDER"
CATALOG_COVERAGE_SCHEMA = "qta-catalog-coverage-contract/v1"

EXCHANGES = ("KOSPI", "KOSDAQ", "NYSE", "NASDAQ")
EXCHANGE_CONTRACTS: dict[str, dict[str, str]] = {
    "KOSPI": {
        "market": "KR",
        "benchmark_id": "KOSPI_COMPOSITE",
        "currency": "KRW",
        "venue": "KRX",
    },
    "KOSDAQ": {
        "market": "KR",
        "benchmark_id": "KOSDAQ_COMPOSITE",
        "currency": "KRW",
        "venue": "KRX",
    },
    "NYSE": {
        "market": "US",
        "benchmark_id": "NYSE_COMPOSITE",
        "currency": "USD",
        "venue": "NYSE",
    },
    "NASDAQ": {
        "market": "US",
        "benchmark_id": "NASDAQ_COMPOSITE",
        "currency": "USD",
        "venue": "NASD",
    },
}

INCLUDED_INSTRUMENT_TYPES = {"COMMON", "ADR", "REIT"}
EXCLUDED_CATALOG_TYPES = {
    "ETF": "instrument_etf",
    "ETN": "instrument_etn",
    "PREFERRED": "instrument_preferred",
    "SPAC": "instrument_spac",
    "UNIT": "instrument_unit",
    "RIGHT": "instrument_right",
    "WARRANT": "instrument_warrant",
    "TEST": "test_issue",
    "ABNORMAL": "abnormal_status",
}
CATALOG_TYPES = INCLUDED_INSTRUMENT_TYPES | set(EXCLUDED_CATALOG_TYPES)

BUILD_SPEC_FIELDS = {
    "schema",
    "as_of",
    "analysis_date",
    "official_sources",
    "broker_sources",
    "eod_catalog",
    "catalog_coverage_contract",
}
CATALOG_COVERAGE_FIELDS = {
    "schema",
    "minimum_ratio_by_exchange",
    "minimum_screenable_ratio_by_exchange",
}
SOURCE_FIELDS = {
    "source_id",
    "provider",
    "exchange",
    "as_of",
    "path",
    "format",
    "encoding",
    "delimiter",
    "skip_rows",
    "columns",
    "normal_status_values",
}
EOD_CATALOG_SPEC_FIELDS = {
    "source_id",
    "provider",
    "as_of",
    "path",
    "encoding",
}
EOD_CATALOG_FIELDS = (
    "exchange",
    "canonical_symbol",
    "data_symbol",
    "broker_symbol",
    "instrument_type",
    "benchmark_id",
    "ticker_csv",
    "benchmark_csv",
    "tick_rule_id",
    "tick_effective_date",
    "tick_reference_price",
    "resolved_tick_size",
    "source_name",
)
LOGICAL_COLUMNS = {
    "symbol",
    "name",
    "instrument_type",
    "status",
    "etf",
    "etn",
    "preferred",
    "spac",
    "unit",
    "right",
    "warrant",
    "test_issue",
    "abnormal",
    "etp",
    "trading_halt",
    "liquidation",
    "administrative",
}
FORMATS = {
    "KRX_CSV",
    "KRX_KIND_HTML",
    "KIS_CSV",
    "KIS_FIXED_WIDTH",
    "KIS_KRX_MASTER",
    "KIS_OVERSEAS_MASTER",
    "NASDAQ_LISTED",
    "NASDAQ_OTHER",
}
TRUE_VALUES = {"1", "T", "TRUE", "Y", "YES"}
FALSE_VALUES = {"", "0", "F", "FALSE", "N", "NO"}
HEX64 = re.compile(r"^[0-9a-f]{64}$")

# KIS official stocks_info master layouts. The downloaded domestic rows contain
# a variable-width Korean name prefix followed by these fixed ASCII tails.
KIS_KOSPI_WIDTHS = (
    2,
    1,
    4,
    4,
    4,
    *([1] * 26),
    9,
    5,
    5,
    1,
    1,
    1,
    2,
    1,
    1,
    1,
    2,
    2,
    2,
    3,
    1,
    3,
    12,
    12,
    8,
    15,
    21,
    2,
    7,
    1,
    1,
    1,
    1,
    1,
    9,
    9,
    9,
    5,
    9,
    8,
    9,
    3,
    1,
    1,
    1,
)
KIS_KOSDAQ_WIDTHS = (
    2,
    1,
    4,
    4,
    4,
    1,
    1,
    1,
    1,
    1,
    1,
    1,
    1,
    1,
    1,
    1,
    1,
    1,
    1,
    1,
    1,
    1,
    1,
    1,
    1,
    1,
    9,
    5,
    5,
    1,
    1,
    1,
    2,
    1,
    1,
    1,
    2,
    2,
    2,
    3,
    1,
    3,
    12,
    12,
    8,
    15,
    21,
    2,
    7,
    1,
    1,
    1,
    1,
    9,
    9,
    9,
    5,
    9,
    8,
    9,
    3,
    1,
    1,
    1,
)
KIS_KRX_LAYOUTS = {
    "KOSPI": {
        "tail_length": 227,
        "widths": KIS_KOSPI_WIDTHS,
        "etp_index": 12,
        "spac_index": 19,
        "halt_index": 34,
        "liquidation_index": 35,
        "admin_index": 36,
        "preferred_index": 54,
    },
    "KOSDAQ": {
        "tail_length": 221,
        "widths": KIS_KOSDAQ_WIDTHS,
        "etp_index": 8,
        "spac_index": 14,
        "halt_index": 29,
        "liquidation_index": 30,
        "admin_index": 31,
        "preferred_index": 49,
    },
}


class UniverseBlockedError(ValueError):
    """Raised when the frozen universe build contract is incomplete or invalid."""


@dataclass(frozen=True)
class MasterRow:
    exchange: str
    symbol: str
    name: str
    instrument_type: str
    status: str
    flags: Mapping[str, str]
    source_id: str


@dataclass(frozen=True)
class CatalogRow:
    exchange: str
    canonical_symbol: str
    data_symbol: str
    broker_symbol: str
    instrument_type: str
    benchmark_id: str
    ticker_csv: str
    benchmark_csv: str
    tick_rule_id: str
    tick_effective_date: str
    tick_reference_price: str
    resolved_tick_size: str
    source_name: str


def canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def sha256_json(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def load_json_object(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise UniverseBlockedError(f"{path} must contain one JSON object")
    return value


def require_exact_fields(
    value: Mapping[str, Any], expected: Iterable[str], label: str
) -> None:
    expected_set = set(expected)
    actual = set(value)
    if actual != expected_set:
        missing = sorted(expected_set - actual)
        extra = sorted(actual - expected_set)
        raise UniverseBlockedError(
            f"{label} fields mismatch; missing={missing}, extra={extra}"
        )


def require_iso_date(value: Any, label: str) -> str:
    if not isinstance(value, str):
        raise UniverseBlockedError(f"{label} must be a YYYY-MM-DD string")
    rendered = value
    try:
        date.fromisoformat(rendered)
    except ValueError as exc:
        raise UniverseBlockedError(f"{label} must be YYYY-MM-DD") from exc
    return rendered


def require_nonempty(value: Any, label: str) -> str:
    if not isinstance(value, str):
        raise UniverseBlockedError(f"{label} must be a non-empty string")
    rendered = value.strip()
    if not rendered:
        raise UniverseBlockedError(f"{label} must be a non-empty string")
    return rendered


def normalized_symbol(value: Any, label: str) -> str:
    return require_nonempty(value, label).upper()


def decimal_string(value: Any, label: str, positive: bool = False) -> str:
    if isinstance(value, bool):
        raise UniverseBlockedError(f"{label} must be a decimal string or number")
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise UniverseBlockedError(
            f"{label} must be a decimal string or number"
        ) from exc
    if not parsed.is_finite():
        raise UniverseBlockedError(f"{label} must be finite")
    if positive and parsed <= 0:
        raise UniverseBlockedError(f"{label} must be positive")
    return format(parsed, "f")


def canonical_ratio_string(value: Any, label: str) -> str:
    if isinstance(value, bool):
        raise UniverseBlockedError(f"{label} must be a decimal string or number")
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise UniverseBlockedError(
            f"{label} must be a decimal string or number"
        ) from exc
    if not parsed.is_finite() or parsed <= 0 or parsed > 1:
        raise UniverseBlockedError(f"{label} must be greater than 0 and at most 1")
    rendered = format(parsed.normalize(), "f")
    return rendered.rstrip("0").rstrip(".") if "." in rendered else rendered


def meets_minimum_coverage_ratio(
    numerator: int,
    official_rows: int,
    minimum: str,
) -> bool:
    if official_rows <= 0:
        return False
    return Decimal(numerator) / Decimal(official_rows) >= Decimal(minimum)


def normalize_catalog_coverage_contract(raw: Any) -> dict[str, Any]:
    label = "catalog_coverage_contract"
    if not isinstance(raw, dict):
        raise UniverseBlockedError(f"{label} must be an object")
    require_exact_fields(raw, CATALOG_COVERAGE_FIELDS, label)
    if raw["schema"] != CATALOG_COVERAGE_SCHEMA:
        raise UniverseBlockedError(f"{label}.schema must be {CATALOG_COVERAGE_SCHEMA}")
    normalized_ratios: dict[str, dict[str, str]] = {}
    for field in (
        "minimum_ratio_by_exchange",
        "minimum_screenable_ratio_by_exchange",
    ):
        ratios = raw[field]
        if not isinstance(ratios, dict) or set(ratios) != set(EXCHANGES):
            raise UniverseBlockedError(
                f"{label}.{field} must contain exactly {list(EXCHANGES)}"
            )
        normalized_ratios[field] = {
            exchange: canonical_ratio_string(
                ratios[exchange],
                f"{label}.{field}.{exchange}",
            )
            for exchange in EXCHANGES
        }
    return {
        "schema": CATALOG_COVERAGE_SCHEMA,
        **normalized_ratios,
    }


def resolve_input_path(raw: Any, base_directory: Path, label: str) -> Path:
    rendered = require_nonempty(raw, label)
    path = Path(rendered)
    if not path.is_absolute():
        path = base_directory / path
    path = path.resolve()
    if not path.is_file():
        raise UniverseBlockedError(f"{label} does not exist or is not a file: {path}")
    return path


def normalize_source_descriptor(
    raw: Any,
    role: str,
    build_as_of: str,
    spec_directory: Path,
    index: int,
) -> dict[str, Any]:
    label = f"{role.lower()}_sources[{index}]"
    if not isinstance(raw, dict):
        raise UniverseBlockedError(f"{label} must be an object")
    require_exact_fields(raw, SOURCE_FIELDS, label)

    source_id = require_nonempty(raw["source_id"], f"{label}.source_id")
    provider = require_nonempty(raw["provider"], f"{label}.provider").upper()
    exchange = str(raw["exchange"]).upper()
    if exchange not in EXCHANGES:
        raise UniverseBlockedError(f"{label}.exchange must be one of {EXCHANGES}")
    source_as_of = require_iso_date(raw["as_of"], f"{label}.as_of")
    if source_as_of != build_as_of:
        raise UniverseBlockedError(
            f"{label}.as_of {source_as_of} does not match build as_of {build_as_of}"
        )
    source_format = str(raw["format"]).upper()
    if source_format not in FORMATS:
        raise UniverseBlockedError(f"{label}.format must be one of {sorted(FORMATS)}")
    encoding = require_nonempty(raw["encoding"], f"{label}.encoding")
    delimiter = str(raw["delimiter"])
    if len(delimiter) > 1:
        raise UniverseBlockedError(f"{label}.delimiter must have at most one character")
    skip_rows = raw["skip_rows"]
    if isinstance(skip_rows, bool) or not isinstance(skip_rows, int) or skip_rows < 0:
        raise UniverseBlockedError(f"{label}.skip_rows must be an integer >= 0")
    columns = raw["columns"]
    if not isinstance(columns, dict):
        raise UniverseBlockedError(f"{label}.columns must be an object")
    unknown_columns = set(columns) - LOGICAL_COLUMNS
    if unknown_columns:
        raise UniverseBlockedError(
            f"{label}.columns has unknown logical fields: {sorted(unknown_columns)}"
        )
    statuses = raw["normal_status_values"]
    if not isinstance(statuses, list) or any(
        not isinstance(item, str) for item in statuses
    ):
        raise UniverseBlockedError(
            f"{label}.normal_status_values must be a string array"
        )
    normal_status_values = sorted({item.strip().upper() for item in statuses})

    if role == "OFFICIAL_MASTER":
        expected_provider = (
            "KRX" if exchange in {"KOSPI", "KOSDAQ"} else "NASDAQ_TRADER"
        )
        if provider != expected_provider:
            raise UniverseBlockedError(
                f"{label}.provider must be {expected_provider} for {exchange}"
            )
        expected_formats = (
            {"KRX_CSV", "KRX_KIND_HTML"}
            if exchange in {"KOSPI", "KOSDAQ"}
            else {"NASDAQ_LISTED" if exchange == "NASDAQ" else "NASDAQ_OTHER"}
        )
        if source_format not in expected_formats:
            raise UniverseBlockedError(
                f"{label}.format {source_format} is invalid for {exchange}"
            )
    elif role == "BROKER_MASTER":
        if provider != "KIS":
            raise UniverseBlockedError(f"{label}.provider must be KIS")
        if source_format not in {
            "KIS_CSV",
            "KIS_FIXED_WIDTH",
            "KIS_KRX_MASTER",
            "KIS_OVERSEAS_MASTER",
        }:
            raise UniverseBlockedError(
                f"{label}.format is not a supported KIS master format"
            )
        if source_format == "KIS_KRX_MASTER" and exchange not in {"KOSPI", "KOSDAQ"}:
            raise UniverseBlockedError(
                f"{label}.format KIS_KRX_MASTER is domestic only"
            )
        if source_format == "KIS_OVERSEAS_MASTER" and exchange not in {
            "NYSE",
            "NASDAQ",
        }:
            raise UniverseBlockedError(
                f"{label}.format KIS_OVERSEAS_MASTER is U.S. only"
            )
    else:
        raise UniverseBlockedError(f"unsupported source role: {role}")

    if source_format in {
        "NASDAQ_LISTED",
        "NASDAQ_OTHER",
        "KRX_KIND_HTML",
        "KIS_KRX_MASTER",
        "KIS_OVERSEAS_MASTER",
    }:
        if columns:
            raise UniverseBlockedError(
                f"{label}.columns must be empty for built-in master formats"
            )
    else:
        if "symbol" not in columns:
            raise UniverseBlockedError(f"{label}.columns.symbol is required")
        if source_format == "KIS_FIXED_WIDTH":
            for logical, bounds in columns.items():
                if (
                    not isinstance(bounds, list)
                    or len(bounds) != 2
                    or any(
                        isinstance(item, bool) or not isinstance(item, int)
                        for item in bounds
                    )
                    or bounds[0] < 0
                    or bounds[1] <= bounds[0]
                ):
                    raise UniverseBlockedError(
                        f"{label}.columns.{logical} must be [start,end] "
                        "zero-based half-open integers"
                    )
        else:
            for logical, header in columns.items():
                require_nonempty(header, f"{label}.columns.{logical}")

    status_is_available = source_format == "NASDAQ_LISTED" or "status" in columns
    if status_is_available and not normal_status_values:
        raise UniverseBlockedError(
            f"{label}.normal_status_values cannot be empty when status is mapped"
        )
    if not status_is_available and normal_status_values:
        raise UniverseBlockedError(
            f"{label}.normal_status_values must be empty without a status field"
        )

    path = resolve_input_path(raw["path"], spec_directory, f"{label}.path")
    return {
        "source_id": source_id,
        "provider": provider,
        "exchange": exchange,
        "as_of": source_as_of,
        "path": str(path),
        "format": source_format,
        "encoding": encoding,
        "delimiter": delimiter,
        "skip_rows": skip_rows,
        "columns": columns,
        "normal_status_values": normal_status_values,
        "role": role,
    }


def normalize_build_spec(raw: dict[str, Any], spec_directory: Path) -> dict[str, Any]:
    require_exact_fields(raw, BUILD_SPEC_FIELDS, "build spec")
    if raw["schema"] != BUILD_SPEC_SCHEMA:
        raise UniverseBlockedError(f"unsupported build spec schema: {raw['schema']!r}")
    as_of = require_iso_date(raw["as_of"], "build spec.as_of")
    analysis_date = require_iso_date(raw["analysis_date"], "build spec.analysis_date")
    if date.fromisoformat(analysis_date) > date.fromisoformat(as_of):
        raise UniverseBlockedError("analysis_date cannot be later than as_of")

    official_raw = raw["official_sources"]
    broker_raw = raw["broker_sources"]
    if not isinstance(official_raw, list) or not isinstance(broker_raw, list):
        raise UniverseBlockedError("official_sources and broker_sources must be arrays")
    official = [
        normalize_source_descriptor(
            item, "OFFICIAL_MASTER", as_of, spec_directory, index
        )
        for index, item in enumerate(official_raw)
    ]
    broker = [
        normalize_source_descriptor(item, "BROKER_MASTER", as_of, spec_directory, index)
        for index, item in enumerate(broker_raw)
    ]

    official_by_exchange = {item["exchange"]: item for item in official}
    if len(official_by_exchange) != len(official):
        raise UniverseBlockedError(
            "official_sources may contain only one source per exchange"
        )
    if set(official_by_exchange) != set(EXCHANGES):
        raise UniverseBlockedError(
            f"official_sources must contain exactly {list(EXCHANGES)}"
        )
    broker_by_exchange = {item["exchange"]: item for item in broker}
    if len(broker_by_exchange) != len(broker):
        raise UniverseBlockedError(
            "broker_sources may contain only one source per exchange"
        )
    if set(broker_by_exchange) != set(EXCHANGES):
        raise UniverseBlockedError(
            f"broker_sources must contain exactly {list(EXCHANGES)}"
        )

    source_ids = [item["source_id"] for item in official + broker]
    if len(source_ids) != len(set(source_ids)):
        raise UniverseBlockedError("master source_id values must be unique")

    catalog_raw = raw["eod_catalog"]
    if not isinstance(catalog_raw, dict):
        raise UniverseBlockedError("eod_catalog must be an object")
    require_exact_fields(catalog_raw, EOD_CATALOG_SPEC_FIELDS, "eod_catalog")
    catalog_as_of = require_iso_date(catalog_raw["as_of"], "eod_catalog.as_of")
    if catalog_as_of != as_of:
        raise UniverseBlockedError(
            f"eod_catalog.as_of {catalog_as_of} does not match build as_of {as_of}"
        )
    catalog = {
        "source_id": require_nonempty(
            catalog_raw["source_id"], "eod_catalog.source_id"
        ),
        "provider": require_nonempty(catalog_raw["provider"], "eod_catalog.provider"),
        "as_of": catalog_as_of,
        "path": str(
            resolve_input_path(catalog_raw["path"], spec_directory, "eod_catalog.path")
        ),
        "encoding": require_nonempty(catalog_raw["encoding"], "eod_catalog.encoding"),
    }
    if catalog["source_id"] in source_ids:
        raise UniverseBlockedError("eod_catalog.source_id must be unique")
    coverage_contract = normalize_catalog_coverage_contract(
        raw["catalog_coverage_contract"]
    )

    return {
        "schema": BUILD_SPEC_SCHEMA,
        "as_of": as_of,
        "analysis_date": analysis_date,
        "official_sources": sorted(
            official, key=lambda item: EXCHANGES.index(item["exchange"])
        ),
        "broker_sources": sorted(
            broker, key=lambda item: EXCHANGES.index(item["exchange"])
        ),
        "eod_catalog": catalog,
        "catalog_coverage_contract": coverage_contract,
    }


def normalize_delimited_row(
    raw: Mapping[str, Any], columns: Mapping[str, Any]
) -> dict[str, str]:
    output: dict[str, str] = {}
    for logical, header in columns.items():
        value = raw.get(str(header))
        output[logical] = "" if value is None else str(value).strip()
    return output


def normalize_fixed_width_row(
    line: bytes, columns: Mapping[str, Any], encoding: str
) -> dict[str, str]:
    return {
        logical: line[bounds[0] : bounds[1]].decode(encoding).strip()
        for logical, bounds in columns.items()
    }


def row_from_fields(
    fields: Mapping[str, str], descriptor: Mapping[str, Any], row_number: int
) -> MasterRow:
    label = f"{descriptor['source_id']} row {row_number}"
    symbol = normalized_symbol(fields.get("symbol", ""), f"{label}.symbol")
    return MasterRow(
        exchange=str(descriptor["exchange"]),
        symbol=symbol,
        name=str(fields.get("name", "")).strip(),
        instrument_type=str(fields.get("instrument_type", "")).strip(),
        status=str(fields.get("status", "")).strip(),
        flags={
            logical: str(fields.get(logical, "")).strip()
            for logical in (
                "etf",
                "etn",
                "preferred",
                "spac",
                "unit",
                "right",
                "warrant",
                "test_issue",
                "abnormal",
                "etp",
                "trading_halt",
                "liquidation",
                "administrative",
            )
            if logical in fields
        },
        source_id=str(descriptor["source_id"]),
    )


def parse_csv_source(descriptor: Mapping[str, Any]) -> list[MasterRow]:
    path = Path(str(descriptor["path"]))
    delimiter = str(descriptor["delimiter"])
    if len(delimiter) != 1:
        raise UniverseBlockedError(
            f"{descriptor['source_id']}.delimiter must be exactly one character"
        )
    with path.open("r", encoding=str(descriptor["encoding"]), newline="") as handle:
        for _ in range(int(descriptor["skip_rows"])):
            next(handle, None)
        reader = csv.DictReader(handle, delimiter=delimiter)
        if reader.fieldnames is None:
            raise UniverseBlockedError(f"{descriptor['source_id']} has no header row")
        required_headers = {str(header) for header in descriptor["columns"].values()}
        missing_headers = required_headers - set(reader.fieldnames)
        if missing_headers:
            raise UniverseBlockedError(
                f"{descriptor['source_id']} missing headers: {sorted(missing_headers)}"
            )
        rows: list[MasterRow] = []
        for row_number, raw in enumerate(
            reader, start=2 + int(descriptor["skip_rows"])
        ):
            fields = normalize_delimited_row(raw, descriptor["columns"])
            if not any(fields.values()):
                continue
            rows.append(row_from_fields(fields, descriptor, row_number))
    return rows


def parse_fixed_width_source(descriptor: Mapping[str, Any]) -> list[MasterRow]:
    path = Path(str(descriptor["path"]))
    lines = path.read_bytes().splitlines()
    rows: list[MasterRow] = []
    skip_rows = int(descriptor["skip_rows"])
    for row_number, line in enumerate(lines[skip_rows:], start=skip_rows + 1):
        if not line.strip():
            continue
        fields = normalize_fixed_width_row(
            line,
            descriptor["columns"],
            str(descriptor["encoding"]),
        )
        rows.append(row_from_fields(fields, descriptor, row_number))
    return rows


def split_fixed_fields(value: str, widths: Sequence[int]) -> list[str]:
    output: list[str] = []
    offset = 0
    for width in widths:
        output.append(value[offset : offset + width].strip())
        offset += width
    if len(value) != offset:
        raise UniverseBlockedError(
            f"fixed-width tail has {len(value)} characters; expected exactly {offset}"
        )
    return output


def parse_kis_krx_master(descriptor: Mapping[str, Any]) -> list[MasterRow]:
    exchange = str(descriptor["exchange"])
    layout = KIS_KRX_LAYOUTS[exchange]
    path = Path(str(descriptor["path"]))
    lines = path.read_text(encoding=str(descriptor["encoding"])).splitlines()
    rows: list[MasterRow] = []
    skip_rows = int(descriptor["skip_rows"])
    for row_number, line in enumerate(lines[skip_rows:], start=skip_rows + 1):
        if not line.strip():
            continue
        tail_length = int(layout["tail_length"])
        if len(line) <= tail_length + 21:
            raise UniverseBlockedError(
                f"{descriptor['source_id']} row {row_number} is shorter than the "
                "KIS domestic master layout"
            )
        prefix = line[:-tail_length]
        tail = line[-tail_length:]
        symbol = prefix[0:9].rstrip()
        standard_symbol = prefix[9:21].rstrip()
        name = prefix[21:].strip()
        if not symbol or not standard_symbol or not name:
            raise UniverseBlockedError(
                f"{descriptor['source_id']} row {row_number} has an incomplete "
                "KIS domestic prefix"
            )
        parts = split_fixed_fields(tail, layout["widths"])
        group_code = parts[0].upper()
        etp_code = parts[int(layout["etp_index"])].upper()
        flags = {
            "etf": "Y" if etp_code in {"1", "2", "5"} else "N",
            "etn": "Y" if etp_code in {"3", "4"} else "N",
            "etp": ("Y" if etp_code not in {"", "0", "1", "2", "3", "4", "5"} else "N"),
            "spac": parts[int(layout["spac_index"])],
            "preferred": (
                "Y" if parts[int(layout["preferred_index"])] not in {"", "0"} else "N"
            ),
            "trading_halt": parts[int(layout["halt_index"])],
            "liquidation": parts[int(layout["liquidation_index"])],
            "administrative": parts[int(layout["admin_index"])],
            "warrant": "Y" if group_code in {"EW", "SW"} else "N",
            "right": "Y" if group_code == "SR" else "N",
        }
        rows.append(
            row_from_fields(
                {
                    "symbol": symbol,
                    "name": name,
                    "instrument_type": group_code,
                    **flags,
                },
                descriptor,
                row_number,
            )
        )
    return rows


def parse_kis_overseas_master(
    descriptor: Mapping[str, Any],
) -> list[MasterRow]:
    path = Path(str(descriptor["path"]))
    exchange = str(descriptor["exchange"])
    expected_exchange_code = "NAS" if exchange == "NASDAQ" else "NYS"
    lines = path.read_bytes().splitlines()
    rows: list[MasterRow] = []
    skip_rows = int(descriptor["skip_rows"])
    for row_number, raw_line in enumerate(lines[skip_rows:], start=skip_rows + 1):
        if not raw_line.strip():
            continue
        fields = raw_line.decode(str(descriptor["encoding"])).split("\t")
        if len(fields) != 24:
            raise UniverseBlockedError(
                f"{descriptor['source_id']} row {row_number} has {len(fields)} "
                "columns; expected 24"
            )
        if fields[2].strip().upper() != expected_exchange_code:
            raise UniverseBlockedError(
                f"{descriptor['source_id']} row {row_number} exchange code "
                f"{fields[2]!r} does not match {expected_exchange_code}"
            )
        symbol = fields[4].strip()
        korean_name = fields[6].strip()
        english_name = fields[7].strip()
        security_type = fields[8].strip()
        dr_flag = fields[17].strip().upper()
        classification = fields[22].strip()
        if dr_flag not in {"Y", "N"}:
            raise UniverseBlockedError(
                f"{descriptor['source_id']} row {row_number} has invalid DR flag "
                f"{dr_flag!r}"
            )
        is_etp = security_type == "3"
        flags = {
            "etf": ("Y" if is_etp and classification in {"001", "005"} else "N"),
            "etn": ("Y" if is_etp and classification in {"002", "006"} else "N"),
            "etp": (
                "Y"
                if is_etp and classification not in {"001", "002", "005", "006"}
                else "N"
            ),
            "warrant": "Y" if security_type == "4" else "N",
        }
        if security_type == "1":
            normalized_type = "INDEX"
        elif security_type == "2" and dr_flag == "Y":
            normalized_type = "DR"
        elif security_type == "2":
            normalized_type = "STOCK"
        elif security_type == "3":
            normalized_type = "ETP"
        elif security_type == "4":
            normalized_type = "WARRANT"
        else:
            normalized_type = f"UNKNOWN_SECURITY_TYPE_{security_type or 'BLANK'}"
        rows.append(
            row_from_fields(
                {
                    "symbol": symbol,
                    "name": english_name or korean_name,
                    "instrument_type": normalized_type,
                    **flags,
                },
                descriptor,
                row_number,
            )
        )
    return rows


class KindTableParser(HTMLParser):
    """Collect text cells from the offline KIND HTML-table Excel export."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.rows: list[list[str]] = []
        self._row: Optional[list[str]] = None
        self._cell_parts: Optional[list[str]] = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, Optional[str]]]) -> None:
        del attrs
        lowered = tag.lower()
        if lowered == "tr":
            self._row = []
        elif lowered in {"th", "td"} and self._row is not None:
            self._cell_parts = []

    def handle_data(self, data: str) -> None:
        if self._cell_parts is not None:
            self._cell_parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        lowered = tag.lower()
        if lowered in {"th", "td"} and self._cell_parts is not None:
            if self._row is not None:
                self._row.append(" ".join("".join(self._cell_parts).split()))
            self._cell_parts = None
        elif lowered == "tr" and self._row is not None:
            if self._row:
                self.rows.append(self._row)
            self._row = None
            self._cell_parts = None


def parse_kind_html_source(descriptor: Mapping[str, Any]) -> list[MasterRow]:
    path = Path(str(descriptor["path"]))
    parser = KindTableParser()
    parser.feed(path.read_text(encoding=str(descriptor["encoding"])))
    required_headers = {"회사명", "시장구분", "종목코드"}
    header_index: Optional[int] = None
    headers: list[str] = []
    for index, cells in enumerate(parser.rows):
        if required_headers.issubset(set(cells)):
            header_index = index
            headers = cells
            break
    if header_index is None:
        raise UniverseBlockedError(
            f"{descriptor['source_id']} KIND table is missing headers "
            f"{sorted(required_headers)}"
        )
    if len(headers) != len(set(headers)):
        raise UniverseBlockedError(
            f"{descriptor['source_id']} KIND table has duplicate headers"
        )
    market_values = {
        "KOSPI": {"유가", "유가증권"},
        "KOSDAQ": {"코스닥"},
    }
    exchange = str(descriptor["exchange"])
    expected_values = market_values[exchange]
    rows: list[MasterRow] = []
    for row_number, cells in enumerate(
        parser.rows[header_index + 1 :], start=header_index + 2
    ):
        if len(cells) != len(headers):
            raise UniverseBlockedError(
                f"{descriptor['source_id']} KIND row {row_number} has "
                f"{len(cells)} cells; expected {len(headers)}"
            )
        raw = dict(zip(headers, cells))
        if raw["시장구분"].strip() not in expected_values:
            continue
        fields = {
            "symbol": raw["종목코드"].strip(),
            "name": raw["회사명"].strip(),
        }
        rows.append(row_from_fields(fields, descriptor, row_number))
    return rows


def parse_nasdaq_source(descriptor: Mapping[str, Any]) -> list[MasterRow]:
    path = Path(str(descriptor["path"]))
    source_format = str(descriptor["format"])
    with path.open("r", encoding=str(descriptor["encoding"]), newline="") as handle:
        reader = csv.DictReader(handle, delimiter="|")
        if reader.fieldnames is None:
            raise UniverseBlockedError(
                f"{descriptor['source_id']} has no Nasdaq header"
            )
        if source_format == "NASDAQ_LISTED":
            required = {
                "Symbol",
                "Security Name",
                "ETF",
                "Test Issue",
                "Financial Status",
            }
        else:
            required = {
                "ACT Symbol",
                "Security Name",
                "Exchange",
                "ETF",
                "Test Issue",
            }
        missing = required - set(reader.fieldnames)
        if missing:
            raise UniverseBlockedError(
                f"{descriptor['source_id']} missing Nasdaq headers: {sorted(missing)}"
            )
        rows: list[MasterRow] = []
        for row_number, raw in enumerate(reader, start=2):
            first_value = next(iter(raw.values()), "")
            if str(first_value).startswith("File Creation Time:"):
                continue
            if (
                source_format == "NASDAQ_OTHER"
                and str(raw.get("Exchange", "")).strip().upper() != "N"
            ):
                continue
            symbol_header = (
                "Symbol" if source_format == "NASDAQ_LISTED" else "ACT Symbol"
            )
            fields = {
                "symbol": str(raw.get(symbol_header, "") or "").strip(),
                "name": str(raw.get("Security Name", "") or "").strip(),
                "etf": str(raw.get("ETF", "") or "").strip(),
                "test_issue": str(raw.get("Test Issue", "") or "").strip(),
            }
            if source_format == "NASDAQ_LISTED":
                fields["status"] = str(raw.get("Financial Status", "") or "").strip()
            if not fields["symbol"]:
                raise UniverseBlockedError(
                    f"{descriptor['source_id']} row {row_number} has no symbol"
                )
            rows.append(row_from_fields(fields, descriptor, row_number))
    return rows


def parse_master_source(descriptor: Mapping[str, Any]) -> list[MasterRow]:
    source_format = str(descriptor["format"])
    if source_format in {"KRX_CSV", "KIS_CSV"}:
        rows = parse_csv_source(descriptor)
    elif source_format == "KIS_FIXED_WIDTH":
        rows = parse_fixed_width_source(descriptor)
    elif source_format == "KIS_KRX_MASTER":
        rows = parse_kis_krx_master(descriptor)
    elif source_format == "KIS_OVERSEAS_MASTER":
        rows = parse_kis_overseas_master(descriptor)
    elif source_format == "KRX_KIND_HTML":
        rows = parse_kind_html_source(descriptor)
    else:
        rows = parse_nasdaq_source(descriptor)
    if not rows:
        raise UniverseBlockedError(f"{descriptor['source_id']} contains no rows")
    by_symbol: dict[str, MasterRow] = {}
    for row in rows:
        if row.symbol in by_symbol:
            if by_symbol[row.symbol] == row:
                continue
            raise UniverseBlockedError(
                f"{descriptor['source_id']} contains conflicting duplicate "
                f"symbol {row.symbol}"
            )
        by_symbol[row.symbol] = row
    return [by_symbol[symbol] for symbol in sorted(by_symbol)]


def parse_bool_flag(value: str, label: str) -> bool:
    normalized = value.strip().upper()
    if normalized in TRUE_VALUES:
        return True
    if normalized in FALSE_VALUES:
        return False
    raise UniverseBlockedError(
        f"{label} must be one of {sorted(TRUE_VALUES | FALSE_VALUES)}"
    )


def metadata_exclusion_reasons(
    row: MasterRow, descriptor: Mapping[str, Any]
) -> list[str]:
    reasons: list[str] = []
    flag_reasons = {
        "etf": "instrument_etf",
        "etn": "instrument_etn",
        "preferred": "instrument_preferred",
        "spac": "instrument_spac",
        "unit": "instrument_unit",
        "right": "instrument_right",
        "warrant": "instrument_warrant",
        "test_issue": "test_issue",
        "abnormal": "abnormal_status",
        "etp": "instrument_etp",
        "trading_halt": "trading_halt",
        "liquidation": "liquidation",
        "administrative": "administrative_issue",
    }
    for flag, reason in flag_reasons.items():
        if flag in row.flags and parse_bool_flag(
            row.flags[flag], f"{row.source_id}:{row.symbol}.{flag}"
        ):
            reasons.append(reason)

    normal_statuses = set(descriptor["normal_status_values"])
    if normal_statuses and row.status.strip().upper() not in normal_statuses:
        reasons.append("abnormal_status")

    raw_type = row.instrument_type.strip().upper()
    raw_type_reasons = {
        "EF": "instrument_etf",
        "FE": "instrument_etf",
        "EN": "instrument_etn",
        "PF": "instrument_preferred",
        "EW": "instrument_warrant",
        "SW": "instrument_warrant",
        "SR": "instrument_right",
        "ETF": "instrument_etf",
        "ETN": "instrument_etn",
        "WARRANT": "instrument_warrant",
        "INDEX": "unsupported_instrument_type",
        "MF": "unsupported_instrument_type",
        "SC": "unsupported_instrument_type",
        "IF": "unsupported_instrument_type",
        "BC": "unsupported_instrument_type",
    }
    if raw_type in raw_type_reasons:
        reasons.append(raw_type_reasons[raw_type])
    elif raw_type == "ETP" and not {
        "instrument_etf",
        "instrument_etn",
    }.intersection(reasons):
        reasons.append("instrument_etp")
    elif raw_type.startswith("UNKNOWN_SECURITY_TYPE_"):
        reasons.append("unsupported_instrument_type")

    searchable = f"{row.instrument_type} {row.name}".upper()
    token_patterns = (
        (r"\bETF\b|상장지수펀드", "instrument_etf"),
        (r"\bETN\b|상장지수증권", "instrument_etn"),
        (r"\bPREFERRED\b|우선주", "instrument_preferred"),
        (r"\bSPAC\b|\bACQUISITION CORP(?:ORATION)?\b|기업인수목적", "instrument_spac"),
        (r"\bUNITS?\b", "instrument_unit"),
        (r"\bRIGHTS?\b", "instrument_right"),
        (r"\bWARRANTS?\b", "instrument_warrant"),
        (r"\bTEST (?:ISSUE|STOCK)\b", "test_issue"),
    )
    for pattern, reason in token_patterns:
        if re.search(pattern, searchable):
            reasons.append(reason)
    return sorted(set(reasons))


def parse_eod_catalog(
    descriptor: Mapping[str, Any],
) -> tuple[dict[tuple[str, str], CatalogRow], Path]:
    path = Path(str(descriptor["path"]))
    with path.open("r", encoding=str(descriptor["encoding"]), newline="") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames is None:
            raise UniverseBlockedError("eod_catalog has no header")
        if tuple(reader.fieldnames) != EOD_CATALOG_FIELDS:
            raise UniverseBlockedError(
                "eod_catalog headers must be exactly, in order: "
                + ",".join(EOD_CATALOG_FIELDS)
            )
        output: dict[tuple[str, str], CatalogRow] = {}
        for row_number, raw in enumerate(reader, start=2):
            if not any(str(value or "").strip() for value in raw.values()):
                continue
            if None in raw:
                raise UniverseBlockedError(
                    f"eod_catalog row {row_number} has extra cells"
                )
            cells = {
                field: str(raw[field] or "").strip() for field in EOD_CATALOG_FIELDS
            }
            exchange = cells["exchange"].upper()
            if exchange not in EXCHANGES:
                raise UniverseBlockedError(
                    f"eod_catalog row {row_number}.exchange must be one of {EXCHANGES}"
                )
            canonical_symbol = normalized_symbol(
                cells["canonical_symbol"],
                f"eod_catalog row {row_number}.canonical_symbol",
            )
            data_symbol = cells["data_symbol"]
            broker_symbol = cells["broker_symbol"].upper()
            instrument_type = cells["instrument_type"].upper()
            catalog_row = CatalogRow(
                exchange=exchange,
                canonical_symbol=canonical_symbol,
                data_symbol=data_symbol,
                broker_symbol=broker_symbol,
                instrument_type=instrument_type,
                benchmark_id=cells["benchmark_id"].upper(),
                ticker_csv=cells["ticker_csv"],
                benchmark_csv=cells["benchmark_csv"],
                tick_rule_id=cells["tick_rule_id"],
                tick_effective_date=cells["tick_effective_date"],
                tick_reference_price=cells["tick_reference_price"],
                resolved_tick_size=cells["resolved_tick_size"],
                source_name=cells["source_name"],
            )
            key = (exchange, canonical_symbol)
            if key in output:
                raise UniverseBlockedError(
                    f"duplicate eod_catalog row: {exchange}:{canonical_symbol}"
                )
            output[key] = catalog_row
    if not output:
        raise UniverseBlockedError("eod_catalog contains no rows")
    return output, path.parent


def optional_data_path(raw: str, catalog_directory: Path) -> Optional[Path]:
    if not raw:
        return None
    path = Path(raw)
    if not path.is_absolute():
        path = catalog_directory / path
    path = path.resolve()
    if not path.is_file():
        return None
    return path


def normalize_tick_contract(
    row: CatalogRow, analysis_date: str
) -> tuple[Optional[dict[str, str]], Optional[str]]:
    fields = (
        row.tick_rule_id,
        row.tick_effective_date,
        row.tick_reference_price,
        row.resolved_tick_size,
    )
    if any(not value for value in fields):
        return None, "missing_tick_contract"
    try:
        effective_date = require_iso_date(
            row.tick_effective_date, "tick_effective_date"
        )
        if date.fromisoformat(effective_date) > date.fromisoformat(analysis_date):
            raise UniverseBlockedError(
                "tick_effective_date cannot be later than analysis_date"
            )
        reference_price = decimal_string(
            row.tick_reference_price, "tick_reference_price", positive=True
        )
        resolved_tick_size = decimal_string(
            row.resolved_tick_size, "resolved_tick_size", positive=True
        )
    except UniverseBlockedError:
        return None, "invalid_tick_contract"
    return (
        {
            "schema": TICK_CONTRACT_SCHEMA,
            "kind": TICK_CONTRACT_KIND,
            "rule_id": row.tick_rule_id,
            "effective_date": effective_date,
            "reference_price": reference_price,
            "resolved_tick_size": resolved_tick_size,
        },
        None,
    )


def catalog_exclusion_reasons(
    row: CatalogRow,
    catalog_directory: Path,
    analysis_date: str,
) -> tuple[
    list[str],
    Optional[Path],
    Optional[Path],
    Optional[dict[str, str]],
]:
    reasons: list[str] = []
    if row.instrument_type in EXCLUDED_CATALOG_TYPES:
        reasons.append(EXCLUDED_CATALOG_TYPES[row.instrument_type])
    elif row.instrument_type not in INCLUDED_INSTRUMENT_TYPES:
        reasons.append("unsupported_instrument_type")
    if not row.data_symbol:
        reasons.append("missing_data_symbol")
    if not row.broker_symbol:
        reasons.append("missing_broker_symbol")
    if row.benchmark_id != EXCHANGE_CONTRACTS[row.exchange]["benchmark_id"]:
        reasons.append("invalid_benchmark_id")
    if not row.source_name:
        reasons.append("missing_eod_source")

    ticker_path = optional_data_path(row.ticker_csv, catalog_directory)
    if ticker_path is None:
        reasons.append("missing_ticker_csv")
    benchmark_path = optional_data_path(row.benchmark_csv, catalog_directory)
    if benchmark_path is None:
        reasons.append("missing_benchmark_csv")
    tick_contract, tick_reason = normalize_tick_contract(row, analysis_date)
    if tick_reason is not None:
        reasons.append(tick_reason)
    return (
        sorted(set(reasons)),
        ticker_path,
        benchmark_path,
        tick_contract,
    )


def source_hash_entry(
    descriptor: Mapping[str, Any], role: str, exchange: str
) -> dict[str, str]:
    path = Path(str(descriptor["path"]))
    entry = {
        "source_id": str(descriptor["source_id"]),
        "role": role,
        "provider": str(descriptor["provider"]),
        "exchange": exchange,
        "as_of": str(descriptor["as_of"]),
        "path": str(path.resolve()),
        "sha256": sha256_file(path),
    }
    if not HEX64.fullmatch(entry["sha256"]):
        raise AssertionError("sha256_file returned an invalid digest")
    return entry


def exclusion_record(
    exchange: str,
    canonical_symbol: str,
    broker_symbol: Optional[str],
    name: str,
    reasons: Sequence[str],
    official_source_id: str,
    broker_source_id: Optional[str],
) -> dict[str, Any]:
    normalized_reasons = sorted(set(reasons))
    if not normalized_reasons:
        raise AssertionError("an exclusion must have at least one reason")
    return {
        "exchange": exchange,
        "canonical_symbol": canonical_symbol,
        "broker_symbol": broker_symbol,
        "name": name,
        "reasons": normalized_reasons,
        "official_source_id": official_source_id,
        "broker_source_id": broker_source_id,
    }


def build_manifest(raw_spec: dict[str, Any], spec_directory: Path) -> dict[str, Any]:
    spec = normalize_build_spec(raw_spec, spec_directory)
    official_descriptors = {item["exchange"]: item for item in spec["official_sources"]}
    broker_descriptors = {item["exchange"]: item for item in spec["broker_sources"]}
    official_rows = {
        exchange: {
            row.symbol: row
            for row in parse_master_source(official_descriptors[exchange])
        }
        for exchange in EXCHANGES
    }
    broker_rows = {
        exchange: {
            row.symbol: row for row in parse_master_source(broker_descriptors[exchange])
        }
        for exchange in broker_descriptors
    }
    catalog, catalog_directory = parse_eod_catalog(spec["eod_catalog"])

    source_hashes = [
        source_hash_entry(item, "OFFICIAL_MASTER", item["exchange"])
        for item in spec["official_sources"]
    ]
    source_hashes.extend(
        source_hash_entry(item, "BROKER_MASTER", item["exchange"])
        for item in spec["broker_sources"]
    )
    source_hashes.append(
        source_hash_entry(spec["eod_catalog"], "EOD_CATALOG", "GLOBAL")
    )
    source_hashes.sort(key=lambda item: item["source_id"])

    instruments: list[dict[str, Any]] = []
    exclusions: list[dict[str, Any]] = []
    path_hash_cache: dict[Path, str] = {}

    for exchange in EXCHANGES:
        official_descriptor = official_descriptors[exchange]
        broker_descriptor = broker_descriptors[exchange]
        broker_by_symbol = broker_rows[exchange]
        for canonical_symbol in sorted(official_rows[exchange]):
            official_row = official_rows[exchange][canonical_symbol]
            catalog_row = catalog.get((exchange, canonical_symbol))
            reasons = metadata_exclusion_reasons(official_row, official_descriptor)
            broker_row: Optional[MasterRow] = None
            broker_symbol: Optional[str] = None
            broker_source_id: Optional[str] = str(broker_descriptor["source_id"])
            ticker_path: Optional[Path] = None
            benchmark_path: Optional[Path] = None
            tick_contract: Optional[dict[str, str]] = None

            if catalog_row is None:
                reasons.append("missing_eod_mapping")
            else:
                broker_symbol = catalog_row.broker_symbol or None
                (
                    catalog_reasons,
                    ticker_path,
                    benchmark_path,
                    tick_contract,
                ) = catalog_exclusion_reasons(
                    catalog_row,
                    catalog_directory,
                    spec["analysis_date"],
                )
                reasons.extend(catalog_reasons)
                if catalog_row.broker_symbol:
                    broker_row = broker_by_symbol.get(catalog_row.broker_symbol.upper())
                    if broker_row is None:
                        reasons.append("not_kis_tradable")
                    else:
                        reasons.extend(
                            metadata_exclusion_reasons(broker_row, broker_descriptor)
                        )

            reasons = sorted(set(reasons))
            if reasons:
                exclusions.append(
                    exclusion_record(
                        exchange=exchange,
                        canonical_symbol=canonical_symbol,
                        broker_symbol=broker_symbol,
                        name=official_row.name,
                        reasons=reasons,
                        official_source_id=official_row.source_id,
                        broker_source_id=broker_source_id,
                    )
                )
                continue

            if catalog_row is None:
                raise AssertionError("catalog row unexpectedly missing")
            if catalog_row.instrument_type not in INCLUDED_INSTRUMENT_TYPES:
                raise AssertionError("excluded catalog type reached inclusion path")
            if ticker_path is None or benchmark_path is None or tick_contract is None:
                raise AssertionError("validated EOD inputs unexpectedly missing")
            for data_path in (ticker_path, benchmark_path):
                if data_path not in path_hash_cache:
                    path_hash_cache[data_path] = sha256_file(data_path)

            contract = EXCHANGE_CONTRACTS[exchange]
            instruments.append(
                {
                    "market": contract["market"],
                    "exchange": exchange,
                    "canonical_symbol": canonical_symbol,
                    "data_symbol": catalog_row.data_symbol,
                    "broker_symbol": catalog_row.broker_symbol,
                    "instrument_type": catalog_row.instrument_type,
                    "benchmark_id": catalog_row.benchmark_id,
                    "currency": contract["currency"],
                    "venue": contract["venue"],
                    "ticker_csv": str(ticker_path),
                    "benchmark_csv": str(benchmark_path),
                    "tick_contract": tick_contract,
                    "source_name": catalog_row.source_name,
                    "ticker_csv_sha256": path_hash_cache[ticker_path],
                    "benchmark_csv_sha256": path_hash_cache[benchmark_path],
                    "broker_tradability_verified": True,
                    "official_source_id": official_row.source_id,
                    "broker_source_id": broker_source_id,
                }
            )

        official_symbols = set(official_rows[exchange])
        for catalog_key, catalog_row in sorted(catalog.items()):
            catalog_exchange, canonical_symbol = catalog_key
            if catalog_exchange != exchange or canonical_symbol in official_symbols:
                continue
            exclusions.append(
                exclusion_record(
                    exchange=exchange,
                    canonical_symbol=canonical_symbol,
                    broker_symbol=catalog_row.broker_symbol or None,
                    name="",
                    reasons=["not_official_exchange_member"],
                    official_source_id=str(official_descriptor["source_id"]),
                    broker_source_id=str(broker_descriptor["source_id"]),
                )
            )

    instruments.sort(
        key=lambda item: (
            EXCHANGES.index(item["exchange"]),
            item["canonical_symbol"],
            item["broker_symbol"],
        )
    )
    exclusions.sort(
        key=lambda item: (
            EXCHANGES.index(item["exchange"]),
            item["canonical_symbol"],
            tuple(item["reasons"]),
        )
    )

    by_exchange: dict[str, dict[str, int]] = {}
    for exchange in EXCHANGES:
        catalog_mapped = sum(
            (exchange, symbol) in catalog for symbol in official_rows[exchange]
        )
        by_exchange[exchange] = {
            "official": len(official_rows[exchange]),
            "broker": len(broker_rows.get(exchange, {})),
            "catalog_mapped": catalog_mapped,
            "included": sum(item["exchange"] == exchange for item in instruments),
            "excluded": sum(item["exchange"] == exchange for item in exclusions),
        }
    counts = {
        "official_rows": sum(item["official"] for item in by_exchange.values()),
        "broker_rows": sum(item["broker"] for item in by_exchange.values()),
        "catalog_mapped_rows": sum(
            item["catalog_mapped"] for item in by_exchange.values()
        ),
        "included": sum(item["included"] for item in by_exchange.values()),
        "excluded": sum(item["excluded"] for item in by_exchange.values()),
        "by_exchange": by_exchange,
    }
    coverage_minimums = spec["catalog_coverage_contract"]["minimum_ratio_by_exchange"]
    coverage_ready = all(
        meets_minimum_coverage_ratio(
            by_exchange[exchange]["catalog_mapped"],
            by_exchange[exchange]["official"],
            coverage_minimums[exchange],
        )
        for exchange in EXCHANGES
    )
    screenable_minimums = spec["catalog_coverage_contract"][
        "minimum_screenable_ratio_by_exchange"
    ]
    screenable_ready = all(
        meets_minimum_coverage_ratio(
            by_exchange[exchange]["included"],
            by_exchange[exchange]["official"],
            screenable_minimums[exchange],
        )
        for exchange in EXCHANGES
    )
    build_status = (
        "READY"
        if coverage_ready
        and screenable_ready
        and all(by_exchange[exchange]["included"] > 0 for exchange in EXCHANGES)
        else "BLOCKED"
    )
    output: dict[str, Any] = {
        "schema": MANIFEST_SCHEMA,
        "build_status": build_status,
        "as_of": spec["as_of"],
        "analysis_date": spec["analysis_date"],
        "catalog_coverage_contract": spec["catalog_coverage_contract"],
        "source_hashes": source_hashes,
        "instruments": instruments,
        "exclusions": exclusions,
        "counts": counts,
    }
    output["manifest_hash"] = sha256_json(output)
    return output


def write_csv(
    path: Path, fieldnames: Sequence[str], rows: Sequence[Mapping[str, Any]]
) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def synthetic_kis_krx_line(
    exchange: str,
    symbol: str,
    name: str,
    *,
    group_code: str = "ST",
    etp_code: str = "0",
    spac: str = "N",
    preferred: str = "0",
    trading_halt: str = "N",
    liquidation: str = "N",
    administrative: str = "N",
) -> str:
    layout = KIS_KRX_LAYOUTS[exchange]
    widths = layout["widths"]
    values = [""] * len(widths)
    values[0] = group_code
    values[int(layout["etp_index"])] = etp_code
    values[int(layout["spac_index"])] = spac
    values[int(layout["halt_index"])] = trading_halt
    values[int(layout["liquidation_index"])] = liquidation
    values[int(layout["admin_index"])] = administrative
    values[int(layout["preferred_index"])] = preferred
    tail = "".join(
        str(value).ljust(width)[:width] for value, width in zip(values, widths)
    )
    tail = tail.ljust(int(layout["tail_length"]))
    standard_symbol = f"KR7{symbol}".ljust(12)[:12]
    return f"{symbol.ljust(9)}{standard_symbol}{name}{tail}"


def synthetic_kis_overseas_line(
    exchange: str,
    symbol: str,
    security_type: str,
    dr_flag: str,
    classification: str = "",
) -> str:
    fields = [""] * 24
    fields[0] = "US"
    fields[1] = "22" if exchange == "NASDAQ" else "21"
    fields[2] = "NAS" if exchange == "NASDAQ" else "NYS"
    fields[3] = exchange
    fields[4] = symbol
    fields[5] = f"{fields[2]}{symbol}"
    fields[6] = symbol
    fields[7] = f"{symbol} SYNTHETIC"
    fields[8] = security_type
    fields[9] = "USD"
    fields[17] = dr_flag
    fields[22] = classification
    return "\t".join(fields)


def self_test() -> None:
    with tempfile.TemporaryDirectory(prefix="qta-universe-v2-") as directory:
        root = Path(directory)
        master_paths: dict[str, Path] = {}
        broker_paths: dict[str, Path] = {}
        kind_path = root / "kind-corpList.xls"
        kind_path.write_text(
            "<html><body><table>"
            "<tr><th>회사명</th><th>시장구분</th><th>종목코드</th></tr>"
            "<tr><td>KOSPI Common</td><td>유가</td><td>000001</td></tr>"
            "<tr><td>KOSPI ETF</td><td>유가</td><td>000002</td></tr>"
            "<tr><td>KOSDAQ Common</td><td>코스닥</td><td>100001</td></tr>"
            "<tr><td>KOSDAQ Missing</td><td>코스닥</td><td>100002</td></tr>"
            "<tr><td>KOSDAQ Alpha</td><td>코스닥</td><td>0156T0</td></tr>"
            "<tr><td>KONEX Ignored</td><td>코넥스</td><td>900001</td></tr>"
            "</table></body></html>",
            encoding="cp949",
        )
        for exchange in ("KOSPI", "KOSDAQ"):
            master_paths[exchange] = kind_path
            broker_path = root / f"{exchange.lower()}_code.mst"
            broker_path.write_text(
                synthetic_kis_krx_line(
                    exchange,
                    "000001" if exchange == "KOSPI" else "100001",
                    f"{exchange} Common",
                )
                + "\n",
                encoding="cp949",
            )
            broker_paths[exchange] = broker_path

        nasdaq_path = root / "nasdaqlisted.txt"
        nasdaq_path.write_text(
            "Symbol|Security Name|Market Category|Test Issue|Financial Status|"
            "Round Lot Size|ETF|NextShares\n"
            "NDAQ1|Nasdaq Common|Q|N|N|100|N|N\n"
            "NDAQW|Nasdaq Warrants|Q|N|N|100|N|N\n"
            "File Creation Time: 0726202600|||||||\n",
            encoding="utf-8",
        )
        nyse_path = root / "otherlisted.txt"
        nyse_path.write_text(
            "ACT Symbol|Security Name|Exchange|CQS Symbol|ETF|Round Lot Size|"
            "Test Issue|NASDAQ Symbol\n"
            "NYSE1|NYSE ADR|N|NYSE1|N|100|N|NYSE1\n"
            "NYSEX|Other Market|A|NYSEX|N|100|N|NYSEX\n"
            "File Creation Time: 0726202600|||||||\n",
            encoding="utf-8",
        )
        master_paths["NASDAQ"] = nasdaq_path
        master_paths["NYSE"] = nyse_path

        for exchange, symbol in (("NASDAQ", "NDAQ1"), ("NYSE", "NYSE1")):
            broker_path = root / (
                "nasmst.cod" if exchange == "NASDAQ" else "nysmst.cod"
            )
            broker_path.write_text(
                synthetic_kis_overseas_line(
                    exchange,
                    symbol,
                    "2",
                    "Y" if exchange == "NYSE" else "N",
                )
                + "\n",
                encoding="cp949",
            )
            broker_paths[exchange] = broker_path

        ticker_paths: dict[str, Path] = {}
        benchmark_paths: dict[str, Path] = {}
        included_symbols = {
            "KOSPI": "000001",
            "KOSDAQ": "100001",
            "NYSE": "NYSE1",
            "NASDAQ": "NDAQ1",
        }
        for exchange, symbol in included_symbols.items():
            ticker_path = root / f"{exchange.lower()}-{symbol}.csv"
            ticker_path.write_text(
                "date,adjusted_close\n2026-07-24,100\n", encoding="utf-8"
            )
            ticker_paths[exchange] = ticker_path
            benchmark_path = root / f"{exchange.lower()}-benchmark.csv"
            benchmark_path.write_text(
                "date,adjusted_close\n2026-07-24,100\n", encoding="utf-8"
            )
            benchmark_paths[exchange] = benchmark_path

        catalog_path = root / "eod-catalog.csv"
        catalog_rows: list[dict[str, str]] = []
        for exchange, symbol in included_symbols.items():
            catalog_rows.append(
                {
                    "exchange": exchange,
                    "canonical_symbol": symbol,
                    "data_symbol": (
                        f"{symbol}.KS"
                        if exchange == "KOSPI"
                        else f"{symbol}.KQ"
                        if exchange == "KOSDAQ"
                        else symbol
                    ),
                    "broker_symbol": symbol,
                    "instrument_type": "ADR" if exchange == "NYSE" else "COMMON",
                    "benchmark_id": EXCHANGE_CONTRACTS[exchange]["benchmark_id"],
                    "ticker_csv": str(ticker_paths[exchange]),
                    "benchmark_csv": str(benchmark_paths[exchange]),
                    "tick_rule_id": f"SELFTEST-{exchange}",
                    "tick_effective_date": "2026-01-01",
                    "tick_reference_price": "100",
                    "resolved_tick_size": (
                        "1" if exchange in {"KOSPI", "KOSDAQ"} else "0.01"
                    ),
                    "source_name": "synthetic-adjusted-eod",
                }
            )
        catalog_rows.extend(
            [
                {
                    "exchange": "KOSPI",
                    "canonical_symbol": "000002",
                    "data_symbol": "",
                    "broker_symbol": "",
                    "instrument_type": "ETF",
                    "benchmark_id": "KOSPI_COMPOSITE",
                    "ticker_csv": "",
                    "benchmark_csv": "",
                    "tick_rule_id": "SELFTEST-KOSPI",
                    "tick_effective_date": "2026-01-01",
                    "tick_reference_price": "100",
                    "resolved_tick_size": "1",
                    "source_name": "",
                },
                {
                    "exchange": "NASDAQ",
                    "canonical_symbol": "NDAQW",
                    "data_symbol": "",
                    "broker_symbol": "",
                    "instrument_type": "WARRANT",
                    "benchmark_id": "NASDAQ_COMPOSITE",
                    "ticker_csv": "",
                    "benchmark_csv": "",
                    "tick_rule_id": "SELFTEST-NASDAQ",
                    "tick_effective_date": "2026-01-01",
                    "tick_reference_price": "1",
                    "resolved_tick_size": "0.01",
                    "source_name": "",
                },
                {
                    "exchange": "KOSDAQ",
                    "canonical_symbol": "100002",
                    "data_symbol": "100002.KQ",
                    "broker_symbol": "",
                    "instrument_type": "COMMON",
                    "benchmark_id": "KOSDAQ_COMPOSITE",
                    "ticker_csv": "",
                    "benchmark_csv": "",
                    "tick_rule_id": "",
                    "tick_effective_date": "",
                    "tick_reference_price": "",
                    "resolved_tick_size": "",
                    "source_name": "synthetic-adjusted-eod",
                },
                {
                    "exchange": "KOSDAQ",
                    "canonical_symbol": "0156T0",
                    "data_symbol": "0156T0.KQ",
                    "broker_symbol": "0156T0",
                    "instrument_type": "COMMON",
                    "benchmark_id": "KOSDAQ_COMPOSITE",
                    "ticker_csv": str(ticker_paths["KOSDAQ"]),
                    "benchmark_csv": str(benchmark_paths["KOSDAQ"]),
                    "tick_rule_id": "SELFTEST-KOSDAQ",
                    "tick_effective_date": "2026-01-01",
                    "tick_reference_price": "100",
                    "resolved_tick_size": "0",
                    "source_name": "synthetic-adjusted-eod",
                },
            ]
        )
        write_csv(catalog_path, EOD_CATALOG_FIELDS, catalog_rows)

        official_sources: list[dict[str, Any]] = []
        broker_sources: list[dict[str, Any]] = []
        for exchange in EXCHANGES:
            if exchange in {"KOSPI", "KOSDAQ"}:
                official_format = "KRX_KIND_HTML"
                official_columns = {}
                official_delimiter = ""
                official_statuses = []
            elif exchange == "NASDAQ":
                official_format = "NASDAQ_LISTED"
                official_columns = {}
                official_delimiter = "|"
                official_statuses = ["N"]
            else:
                official_format = "NASDAQ_OTHER"
                official_columns = {}
                official_delimiter = "|"
                official_statuses = []
            official_sources.append(
                {
                    "source_id": f"official-{exchange.lower()}",
                    "provider": (
                        "KRX" if exchange in {"KOSPI", "KOSDAQ"} else "NASDAQ_TRADER"
                    ),
                    "exchange": exchange,
                    "as_of": "2026-07-26",
                    "path": str(master_paths[exchange]),
                    "format": official_format,
                    "encoding": (
                        "cp949" if official_format == "KRX_KIND_HTML" else "utf-8"
                    ),
                    "delimiter": official_delimiter,
                    "skip_rows": 0,
                    "columns": official_columns,
                    "normal_status_values": official_statuses,
                }
            )
            broker_sources.append(
                {
                    "source_id": f"kis-{exchange.lower()}",
                    "provider": "KIS",
                    "exchange": exchange,
                    "as_of": "2026-07-26",
                    "path": str(broker_paths[exchange]),
                    "format": (
                        "KIS_KRX_MASTER"
                        if exchange in {"KOSPI", "KOSDAQ"}
                        else "KIS_OVERSEAS_MASTER"
                    ),
                    "encoding": "cp949",
                    "delimiter": ("" if exchange in {"KOSPI", "KOSDAQ"} else "\t"),
                    "skip_rows": 0,
                    "columns": {},
                    "normal_status_values": [],
                }
            )

        spec = {
            "schema": BUILD_SPEC_SCHEMA,
            "as_of": "2026-07-26",
            "analysis_date": "2026-07-24",
            "catalog_coverage_contract": {
                "schema": CATALOG_COVERAGE_SCHEMA,
                "minimum_ratio_by_exchange": {exchange: "1" for exchange in EXCHANGES},
                "minimum_screenable_ratio_by_exchange": {
                    exchange: "0.3" for exchange in EXCHANGES
                },
            },
            "official_sources": official_sources,
            "broker_sources": broker_sources,
            "eod_catalog": {
                "source_id": "eod-catalog",
                "provider": "synthetic-eod",
                "as_of": "2026-07-26",
                "path": str(catalog_path),
                "encoding": "utf-8",
            },
        }
        first = build_manifest(spec, root)
        second = build_manifest(
            {
                **spec,
                "official_sources": list(reversed(official_sources)),
                "broker_sources": list(reversed(broker_sources)),
            },
            root,
        )
        assert first == second
        assert first["build_status"] == "READY"
        assert first["counts"]["included"] == 4
        assert first["counts"]["by_exchange"]["KOSPI"]["excluded"] == 1
        assert first["counts"]["by_exchange"]["KOSDAQ"]["excluded"] == 2
        assert first["counts"]["by_exchange"]["NASDAQ"]["excluded"] == 1
        assert (
            first["counts"]["catalog_mapped_rows"] == first["counts"]["official_rows"]
        )
        assert all(
            first["counts"]["by_exchange"][exchange]["catalog_mapped"]
            == first["counts"]["by_exchange"][exchange]["official"]
            for exchange in EXCHANGES
        )
        assert any(
            item["canonical_symbol"] == "000002" and "instrument_etf" in item["reasons"]
            for item in first["exclusions"]
        )
        assert any(
            item["canonical_symbol"] == "NDAQW"
            and "instrument_warrant" in item["reasons"]
            for item in first["exclusions"]
        )
        assert any(
            item["canonical_symbol"] == "0156T0"
            and "invalid_tick_contract" in item["reasons"]
            for item in first["exclusions"]
        )
        assert any(
            item["canonical_symbol"] == "100002"
            and {
                "missing_tick_contract",
                "missing_ticker_csv",
                "missing_benchmark_csv",
            }.issubset(set(item["reasons"]))
            for item in first["exclusions"]
        )
        assert all(item["broker_tradability_verified"] for item in first["instruments"])
        assert all(
            set(item["tick_contract"])
            == {
                "schema",
                "kind",
                "rule_id",
                "effective_date",
                "reference_price",
                "resolved_tick_size",
            }
            for item in first["instruments"]
        )
        expected_hash = sha256_json(
            {key: value for key, value in first.items() if key != "manifest_hash"}
        )
        assert first["manifest_hash"] == expected_hash
        relaxed_manifest = build_manifest(
            {
                **spec,
                "catalog_coverage_contract": {
                    "schema": CATALOG_COVERAGE_SCHEMA,
                    "minimum_ratio_by_exchange": {
                        exchange: "0.5" for exchange in EXCHANGES
                    },
                    "minimum_screenable_ratio_by_exchange": {
                        exchange: "0.3" for exchange in EXCHANGES
                    },
                },
            },
            root,
        )
        assert relaxed_manifest["build_status"] == "READY"
        assert relaxed_manifest["manifest_hash"] != first["manifest_hash"]

        strict_screenable_manifest = build_manifest(
            {
                **spec,
                "catalog_coverage_contract": {
                    "schema": CATALOG_COVERAGE_SCHEMA,
                    "minimum_ratio_by_exchange": {
                        exchange: "1" for exchange in EXCHANGES
                    },
                    "minimum_screenable_ratio_by_exchange": {
                        exchange: "0.6" for exchange in EXCHANGES
                    },
                },
            },
            root,
        )
        assert (
            strict_screenable_manifest["counts"]["catalog_mapped_rows"]
            == strict_screenable_manifest["counts"]["official_rows"]
        )
        assert strict_screenable_manifest["build_status"] == "BLOCKED"
        assert strict_screenable_manifest["manifest_hash"] != first["manifest_hash"]

        minimal_catalog_path = root / "minimal-eod-catalog.csv"
        write_csv(minimal_catalog_path, EOD_CATALOG_FIELDS, catalog_rows[:4])
        minimal_manifest = build_manifest(
            {
                **spec,
                "eod_catalog": {
                    **spec["eod_catalog"],
                    "path": str(minimal_catalog_path),
                },
            },
            root,
        )
        assert minimal_manifest["counts"]["included"] == 4
        assert minimal_manifest["counts"]["catalog_mapped_rows"] == 4
        assert minimal_manifest["build_status"] == "BLOCKED"
        assert minimal_manifest["manifest_hash"] != first["manifest_hash"]
        print(
            canonical_json(
                {
                    "self_test": "PASS",
                    "schema": MANIFEST_SCHEMA,
                    "manifest_hash": first["manifest_hash"],
                    "counts": first["counts"],
                }
            )
        )


def emit(value: Mapping[str, Any], output: Optional[str]) -> None:
    rendered = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        indent=2,
        allow_nan=False,
    )
    if output:
        output_path = Path(output).resolve()
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(rendered + "\n", encoding="utf-8")
        print(
            canonical_json(
                {
                    "schema": value.get("schema"),
                    "build_status": value.get("build_status"),
                    "output": str(output_path),
                    "manifest_hash": value.get("manifest_hash"),
                    "counts": value.get("counts"),
                }
            )
        )
    else:
        print(rendered)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--build-spec")
    parser.add_argument("--output")
    parser.add_argument("--self-test", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.self_test:
        self_test()
        return 0
    if not args.build_spec:
        print(
            canonical_json(
                {
                    "schema": "qta-universe-build-error/v1",
                    "build_status": "BLOCKED",
                    "reason": "--build-spec is required",
                }
            )
        )
        return 2
    spec_path = Path(args.build_spec).resolve()
    try:
        manifest = build_manifest(
            load_json_object(spec_path),
            spec_path.parent,
        )
    except (
        UniverseBlockedError,
        OSError,
        UnicodeError,
        json.JSONDecodeError,
    ) as exc:
        error = {
            "schema": "qta-universe-build-error/v1",
            "build_status": "BLOCKED",
            "reason": str(exc),
        }
        emit(error, None)
        return 2
    emit(manifest, args.output)
    return 0 if manifest["build_status"] == "READY" else 2


if __name__ == "__main__":
    sys.exit(main())

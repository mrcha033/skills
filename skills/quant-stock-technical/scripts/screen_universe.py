#!/usr/bin/env python3
"""Run qta-1.0.0 across a frozen v1/v2 universe and apply its selector."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

import analyze_stock as qta

MANIFEST_SCHEMA = "qta-universe-manifest/v1"
SCREEN_SCHEMA = "qta-screen/v1"
SELECTOR_VERSION = "qta-screen-1.0.0"
SUPPORTED_MARKETS = {"KR", "US"}
MANIFEST_SCHEMA_V2 = "qta-universe-manifest/v2"
SCREEN_SCHEMA_V2 = "qta-screen/v2"
SELECTOR_VERSION_V2 = "qta-screen-1.1.0"
CATALOG_COVERAGE_SCHEMA = "qta-catalog-coverage-contract/v1"
EXCHANGE_ORDER = ("KOSPI", "KOSDAQ", "NYSE", "NASDAQ")
SUPPORTED_EXCHANGES = {"KOSPI", "KOSDAQ", "NYSE", "NASDAQ"}
SUPPORTED_SETUP_STATUSES = {"READY", "CONDITIONAL"}
EXCHANGE_CONTRACTS = {
    "KOSPI": {
        "market": "KR",
        "currency": "KRW",
        "venue": "KRX",
        "benchmark_id": "KOSPI_COMPOSITE",
    },
    "KOSDAQ": {
        "market": "KR",
        "currency": "KRW",
        "venue": "KRX",
        "benchmark_id": "KOSDAQ_COMPOSITE",
    },
    "NYSE": {
        "market": "US",
        "currency": "USD",
        "venue": "NYSE",
        "benchmark_id": "NYSE_COMPOSITE",
    },
    "NASDAQ": {
        "market": "US",
        "currency": "USD",
        "venue": "NASD",
        "benchmark_id": "NASDAQ_COMPOSITE",
    },
}
SUPPORTED_INSTRUMENT_TYPES = {"COMMON", "ADR", "REIT"}
SOURCE_HASH_FIELDS = {
    "source_id",
    "role",
    "provider",
    "exchange",
    "as_of",
    "path",
    "sha256",
}
EXCLUSION_FIELDS = {
    "exchange",
    "canonical_symbol",
    "broker_symbol",
    "name",
    "reasons",
    "official_source_id",
    "broker_source_id",
}
COUNT_FIELDS = {
    "official_rows",
    "broker_rows",
    "catalog_mapped_rows",
    "included",
    "excluded",
    "by_exchange",
}
EXCHANGE_COUNT_FIELDS = {
    "official",
    "broker",
    "catalog_mapped",
    "included",
    "excluded",
}
CATALOG_COVERAGE_FIELDS = {
    "schema",
    "minimum_ratio_by_exchange",
    "minimum_screenable_ratio_by_exchange",
}
MANIFEST_V2_FIELDS = {
    "schema",
    "build_status",
    "as_of",
    "analysis_date",
    "catalog_coverage_contract",
    "source_hashes",
    "instruments",
    "exclusions",
    "counts",
    "manifest_hash",
}
INSTRUMENT_V2_FIELDS = {
    "market",
    "exchange",
    "canonical_symbol",
    "data_symbol",
    "broker_symbol",
    "instrument_type",
    "benchmark_id",
    "currency",
    "venue",
    "ticker_csv",
    "benchmark_csv",
    "tick_contract",
    "source_name",
    "ticker_csv_sha256",
    "benchmark_csv_sha256",
    "broker_tradability_verified",
    "official_source_id",
    "broker_source_id",
}
TICK_CONTRACT_FIELDS = {
    "schema",
    "kind",
    "rule_id",
    "effective_date",
    "reference_price",
    "resolved_tick_size",
}
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")


class ScreenBlockedError(ValueError):
    """Raised when a frozen screen contract is invalid."""


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
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_object(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ScreenBlockedError(f"{path} must contain a JSON object")
    return value


def as_decimal(value: Any, field: str) -> Decimal:
    if isinstance(value, bool):
        raise ScreenBlockedError(f"{field} must be a decimal string or number")
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ScreenBlockedError(f"{field} must be a decimal string or number") from exc
    if not parsed.is_finite():
        raise ScreenBlockedError(f"{field} must be finite")
    return parsed


def as_nonempty_string(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ScreenBlockedError(f"{field} must be a non-empty string")
    return value.strip()


def as_string(value: Any, field: str) -> str:
    if not isinstance(value, str):
        raise ScreenBlockedError(f"{field} must be a string")
    return value


def as_iso_date(value: Any, field: str) -> str:
    rendered = as_nonempty_string(value, field)
    try:
        date.fromisoformat(rendered)
    except ValueError as exc:
        raise ScreenBlockedError(f"{field} must be YYYY-MM-DD") from exc
    return rendered


def positive_decimal_string(value: Any, field: str) -> str:
    parsed = as_decimal(value, field)
    if parsed <= 0:
        raise ScreenBlockedError(f"{field} must be positive")
    return format(parsed, "f")


def canonical_coverage_ratio(value: Any, field: str) -> str:
    parsed = as_decimal(value, field)
    if parsed <= 0 or parsed > 1:
        raise ScreenBlockedError(f"{field} must be greater than 0 and at most 1")
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
    field = "catalog_coverage_contract"
    if not isinstance(raw, dict) or set(raw) != CATALOG_COVERAGE_FIELDS:
        raise ScreenBlockedError(
            f"{field} fields must be exactly {sorted(CATALOG_COVERAGE_FIELDS)}"
        )
    if raw["schema"] != CATALOG_COVERAGE_SCHEMA:
        raise ScreenBlockedError(f"{field}.schema must be {CATALOG_COVERAGE_SCHEMA}")
    normalized_ratios: dict[str, dict[str, str]] = {}
    for ratio_field in (
        "minimum_ratio_by_exchange",
        "minimum_screenable_ratio_by_exchange",
    ):
        ratios = raw[ratio_field]
        if not isinstance(ratios, dict) or set(ratios) != SUPPORTED_EXCHANGES:
            raise ScreenBlockedError(
                f"{field}.{ratio_field} must contain exactly "
                "KOSPI, KOSDAQ, NYSE, NASDAQ"
            )
        normalized_ratios[ratio_field] = {
            exchange: canonical_coverage_ratio(
                ratios[exchange],
                f"{field}.{ratio_field}.{exchange}",
            )
            for exchange in EXCHANGE_ORDER
        }
    return {
        "schema": CATALOG_COVERAGE_SCHEMA,
        **normalized_ratios,
    }


def normalize_manifest_v1(manifest: dict[str, Any]) -> dict[str, Any]:
    if set(manifest) != {"schema", "analysis_date", "instruments"}:
        raise ScreenBlockedError(
            "manifest fields must be exactly schema, analysis_date, instruments"
        )
    if manifest["schema"] != MANIFEST_SCHEMA:
        raise ScreenBlockedError(f"unsupported manifest schema: {manifest['schema']!r}")
    try:
        date.fromisoformat(str(manifest["analysis_date"]))
    except ValueError as exc:
        raise ScreenBlockedError("analysis_date must be YYYY-MM-DD") from exc
    instruments = manifest["instruments"]
    if not isinstance(instruments, list) or not instruments:
        raise ScreenBlockedError("instruments must be a non-empty array")

    required = {
        "market",
        "ticker",
        "ticker_csv",
        "benchmark_csv",
        "tick_size",
        "source_name",
    }
    normalized: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for index, raw in enumerate(instruments):
        if not isinstance(raw, dict) or set(raw) != required:
            raise ScreenBlockedError(
                f"instruments[{index}] fields must be exactly {sorted(required)}"
            )
        market = str(raw["market"]).upper()
        ticker = str(raw["ticker"]).upper()
        if market not in SUPPORTED_MARKETS:
            raise ScreenBlockedError(f"instruments[{index}].market must be KR or US")
        if not ticker:
            raise ScreenBlockedError(f"instruments[{index}].ticker is empty")
        key = (market, ticker)
        if key in seen:
            raise ScreenBlockedError(f"duplicate instrument: {market}:{ticker}")
        seen.add(key)
        tick_size = as_decimal(raw["tick_size"], f"instruments[{index}].tick_size")
        if tick_size <= 0:
            raise ScreenBlockedError(f"instruments[{index}].tick_size must be positive")
        normalized.append(
            {
                "market": market,
                "ticker": ticker,
                "ticker_csv": str(raw["ticker_csv"]),
                "benchmark_csv": str(raw["benchmark_csv"]),
                "tick_size": format(tick_size, "f"),
                "source_name": str(raw["source_name"]),
            }
        )
    normalized.sort(key=lambda item: (item["market"], item["ticker"]))
    return {
        "schema": MANIFEST_SCHEMA,
        "analysis_date": str(manifest["analysis_date"]),
        "instruments": normalized,
    }


def normalize_tick_contract(
    raw: Any,
    field: str,
    analysis_date: str,
) -> dict[str, str]:
    if not isinstance(raw, dict) or set(raw) != TICK_CONTRACT_FIELDS:
        raise ScreenBlockedError(
            f"{field} fields must be exactly {sorted(TICK_CONTRACT_FIELDS)}"
        )
    if raw["schema"] != "qta-tick-contract/v1":
        raise ScreenBlockedError(f"{field}.schema must be qta-tick-contract/v1")
    if raw["kind"] != "RESOLVED_PRICE_LADDER":
        raise ScreenBlockedError(f"{field}.kind must be RESOLVED_PRICE_LADDER")
    effective_date = as_iso_date(raw["effective_date"], f"{field}.effective_date")
    if effective_date > analysis_date:
        raise ScreenBlockedError(
            f"{field}.effective_date must not be after analysis_date"
        )
    return {
        "schema": "qta-tick-contract/v1",
        "kind": "RESOLVED_PRICE_LADDER",
        "rule_id": as_nonempty_string(raw["rule_id"], f"{field}.rule_id"),
        "effective_date": effective_date,
        "reference_price": positive_decimal_string(
            raw["reference_price"], f"{field}.reference_price"
        ),
        "resolved_tick_size": positive_decimal_string(
            raw["resolved_tick_size"], f"{field}.resolved_tick_size"
        ),
    }


def exact_sha256(value: Any, field: str) -> str:
    rendered = as_nonempty_string(value, field)
    if SHA256_PATTERN.fullmatch(rendered) is None:
        raise ScreenBlockedError(f"{field} must be 64 lowercase hexadecimal characters")
    return rendered


def exact_upper_string(value: Any, field: str) -> str:
    rendered = as_nonempty_string(value, field)
    if rendered != rendered.upper():
        raise ScreenBlockedError(f"{field} must be uppercase")
    return rendered


def nonnegative_integer(value: Any, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ScreenBlockedError(f"{field} must be an integer >= 0")
    return value


def normalize_eligible_setup_statuses(raw: Any) -> list[str]:
    if (
        not isinstance(raw, list)
        or not raw
        or any(not isinstance(item, str) for item in raw)
        or not set(raw).issubset(SUPPORTED_SETUP_STATUSES)
    ):
        raise ScreenBlockedError(
            "eligible_setup_statuses must be a non-empty subset of "
            "READY and CONDITIONAL"
        )
    return sorted(set(raw))


def normalize_source_hashes(raw: Any, manifest_as_of: str) -> list[dict[str, str]]:
    if not isinstance(raw, list) or not raw:
        raise ScreenBlockedError("source_hashes must be a non-empty array")
    normalized: list[dict[str, str]] = []
    seen: set[str] = set()
    for index, source in enumerate(raw):
        field = f"source_hashes[{index}]"
        if not isinstance(source, dict) or set(source) != SOURCE_HASH_FIELDS:
            raise ScreenBlockedError(
                f"{field} fields must be exactly {sorted(SOURCE_HASH_FIELDS)}"
            )
        source_id = as_nonempty_string(source["source_id"], f"{field}.source_id")
        if source_id in seen:
            raise ScreenBlockedError(f"duplicate source_id: {source_id}")
        seen.add(source_id)
        exchange = exact_upper_string(source["exchange"], f"{field}.exchange")
        if exchange not in SUPPORTED_EXCHANGES | {"GLOBAL"}:
            raise ScreenBlockedError(
                f"{field}.exchange must be GLOBAL or one of "
                f"{sorted(SUPPORTED_EXCHANGES)}"
            )
        source_date = as_iso_date(source["as_of"], f"{field}.as_of")
        if source_date != manifest_as_of:
            raise ScreenBlockedError(f"{field}.as_of must equal manifest as_of")
        source_path = Path(as_nonempty_string(source["path"], f"{field}.path"))
        if not source_path.is_absolute():
            raise ScreenBlockedError(f"{field}.path must be absolute")
        normalized.append(
            {
                "source_id": source_id,
                "role": as_nonempty_string(source["role"], f"{field}.role"),
                "provider": as_nonempty_string(source["provider"], f"{field}.provider"),
                "exchange": exchange,
                "as_of": source_date,
                "path": str(source_path),
                "sha256": exact_sha256(source["sha256"], f"{field}.sha256"),
            }
        )
    if normalized != sorted(normalized, key=lambda item: item["source_id"]):
        raise ScreenBlockedError("source_hashes must be sorted by source_id")
    return normalized


def normalize_exclusions(raw: Any) -> list[dict[str, Any]]:
    if not isinstance(raw, list):
        raise ScreenBlockedError("exclusions must be an array")
    normalized: list[dict[str, Any]] = []
    for index, exclusion in enumerate(raw):
        field = f"exclusions[{index}]"
        if not isinstance(exclusion, dict) or set(exclusion) != EXCLUSION_FIELDS:
            raise ScreenBlockedError(
                f"{field} fields must be exactly {sorted(EXCLUSION_FIELDS)}"
            )
        exchange = exact_upper_string(exclusion["exchange"], f"{field}.exchange")
        if exchange not in SUPPORTED_EXCHANGES:
            raise ScreenBlockedError(
                f"{field}.exchange must be one of {sorted(SUPPORTED_EXCHANGES)}"
            )
        reasons = exclusion["reasons"]
        if (
            not isinstance(reasons, list)
            or not reasons
            or any(not isinstance(reason, str) or not reason for reason in reasons)
            or reasons != sorted(set(reasons))
        ):
            raise ScreenBlockedError(
                f"{field}.reasons must be a non-empty sorted unique string array"
            )
        broker_symbol = exclusion["broker_symbol"]
        if broker_symbol is not None:
            broker_symbol = as_nonempty_string(broker_symbol, f"{field}.broker_symbol")
        broker_source_id = exclusion["broker_source_id"]
        if broker_source_id is not None:
            broker_source_id = as_nonempty_string(
                broker_source_id, f"{field}.broker_source_id"
            )
        normalized.append(
            {
                "exchange": exchange,
                "canonical_symbol": as_nonempty_string(
                    exclusion["canonical_symbol"], f"{field}.canonical_symbol"
                ),
                "broker_symbol": broker_symbol,
                "name": as_string(exclusion["name"], f"{field}.name"),
                "reasons": reasons,
                "official_source_id": as_nonempty_string(
                    exclusion["official_source_id"],
                    f"{field}.official_source_id",
                ),
                "broker_source_id": broker_source_id,
            }
        )
    if normalized != sorted(
        normalized,
        key=lambda item: (
            EXCHANGE_ORDER.index(item["exchange"]),
            item["canonical_symbol"],
            item["reasons"],
        ),
    ):
        raise ScreenBlockedError(
            "exclusions must follow KOSPI, KOSDAQ, NYSE, NASDAQ order and then "
            "canonical_symbol, reasons"
        )
    return normalized


def normalize_counts(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict) or set(raw) != COUNT_FIELDS:
        raise ScreenBlockedError(
            f"counts fields must be exactly {sorted(COUNT_FIELDS)}"
        )
    by_exchange = raw["by_exchange"]
    if not isinstance(by_exchange, dict) or set(by_exchange) != SUPPORTED_EXCHANGES:
        raise ScreenBlockedError(
            "counts.by_exchange must contain exactly KOSPI, KOSDAQ, NYSE, NASDAQ"
        )
    normalized_by_exchange: dict[str, dict[str, int]] = {}
    for exchange in sorted(SUPPORTED_EXCHANGES):
        counts = by_exchange[exchange]
        field = f"counts.by_exchange.{exchange}"
        if not isinstance(counts, dict) or set(counts) != EXCHANGE_COUNT_FIELDS:
            raise ScreenBlockedError(
                f"{field} fields must be exactly {sorted(EXCHANGE_COUNT_FIELDS)}"
            )
        normalized_by_exchange[exchange] = {
            key: nonnegative_integer(counts[key], f"{field}.{key}")
            for key in sorted(EXCHANGE_COUNT_FIELDS)
        }
    normalized: dict[str, Any] = {
        field: nonnegative_integer(raw[field], f"counts.{field}")
        for field in (
            "official_rows",
            "broker_rows",
            "catalog_mapped_rows",
            "included",
            "excluded",
        )
    }
    normalized["by_exchange"] = normalized_by_exchange
    total_mappings = {
        "official_rows": "official",
        "broker_rows": "broker",
        "catalog_mapped_rows": "catalog_mapped",
        "included": "included",
        "excluded": "excluded",
    }
    for total_field, exchange_field in total_mappings.items():
        expected = sum(
            counts[exchange_field] for counts in normalized_by_exchange.values()
        )
        if normalized[total_field] != expected:
            raise ScreenBlockedError(
                f"counts.{total_field} must equal the by_exchange sum"
            )
    return normalized


def normalize_instruments_v2(
    raw: Any,
    analysis_date: str,
    source_ids: set[str],
) -> list[dict[str, Any]]:
    if not isinstance(raw, list) or not raw:
        raise ScreenBlockedError("instruments must be a non-empty array")
    normalized: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for index, instrument in enumerate(raw):
        field = f"instruments[{index}]"
        if not isinstance(instrument, dict) or set(instrument) != INSTRUMENT_V2_FIELDS:
            raise ScreenBlockedError(
                f"{field} fields must be exactly {sorted(INSTRUMENT_V2_FIELDS)}"
            )
        exchange = exact_upper_string(instrument["exchange"], f"{field}.exchange")
        if exchange not in SUPPORTED_EXCHANGES:
            raise ScreenBlockedError(
                f"{field}.exchange must be one of {sorted(SUPPORTED_EXCHANGES)}"
            )
        exchange_contract = EXCHANGE_CONTRACTS[exchange]
        market = exact_upper_string(instrument["market"], f"{field}.market")
        if market != exchange_contract["market"]:
            raise ScreenBlockedError(
                f"{field}.market must be {exchange_contract['market']} for {exchange}"
            )
        canonical_symbol = as_nonempty_string(
            instrument["canonical_symbol"], f"{field}.canonical_symbol"
        )
        key = (market, canonical_symbol)
        if key in seen:
            raise ScreenBlockedError(
                f"duplicate canonical instrument: {market}:{canonical_symbol}"
            )
        seen.add(key)
        instrument_type = exact_upper_string(
            instrument["instrument_type"], f"{field}.instrument_type"
        )
        if instrument_type not in SUPPORTED_INSTRUMENT_TYPES:
            raise ScreenBlockedError(
                f"{field}.instrument_type must be one of "
                f"{sorted(SUPPORTED_INSTRUMENT_TYPES)}"
            )
        if market == "KR" and instrument_type == "ADR":
            raise ScreenBlockedError(f"{field}.instrument_type ADR requires market US")
        for contract_field in ("benchmark_id", "currency", "venue"):
            actual = exact_upper_string(
                instrument[contract_field], f"{field}.{contract_field}"
            )
            if actual != exchange_contract[contract_field]:
                raise ScreenBlockedError(
                    f"{field}.{contract_field} must be "
                    f"{exchange_contract[contract_field]} for {exchange}"
                )
        ticker_path = Path(
            as_nonempty_string(instrument["ticker_csv"], f"{field}.ticker_csv")
        )
        benchmark_path = Path(
            as_nonempty_string(instrument["benchmark_csv"], f"{field}.benchmark_csv")
        )
        if not ticker_path.is_absolute() or not benchmark_path.is_absolute():
            raise ScreenBlockedError(
                f"{field}.ticker_csv and benchmark_csv must be absolute"
            )
        if instrument["broker_tradability_verified"] is not True:
            raise ScreenBlockedError(
                f"{field}.broker_tradability_verified must be true"
            )
        official_source_id = as_nonempty_string(
            instrument["official_source_id"], f"{field}.official_source_id"
        )
        broker_source_id = as_nonempty_string(
            instrument["broker_source_id"], f"{field}.broker_source_id"
        )
        if official_source_id not in source_ids or broker_source_id not in source_ids:
            raise ScreenBlockedError(
                f"{field} references an unknown official_source_id or broker_source_id"
            )
        normalized.append(
            {
                "market": market,
                "exchange": exchange,
                "canonical_symbol": canonical_symbol,
                "data_symbol": as_nonempty_string(
                    instrument["data_symbol"], f"{field}.data_symbol"
                ),
                "broker_symbol": as_nonempty_string(
                    instrument["broker_symbol"], f"{field}.broker_symbol"
                ),
                "instrument_type": instrument_type,
                "benchmark_id": exchange_contract["benchmark_id"],
                "currency": exchange_contract["currency"],
                "venue": exchange_contract["venue"],
                "ticker_csv": str(ticker_path),
                "benchmark_csv": str(benchmark_path),
                "tick_contract": normalize_tick_contract(
                    instrument["tick_contract"],
                    f"{field}.tick_contract",
                    analysis_date,
                ),
                "source_name": as_nonempty_string(
                    instrument["source_name"], f"{field}.source_name"
                ),
                "ticker_csv_sha256": exact_sha256(
                    instrument["ticker_csv_sha256"],
                    f"{field}.ticker_csv_sha256",
                ),
                "benchmark_csv_sha256": exact_sha256(
                    instrument["benchmark_csv_sha256"],
                    f"{field}.benchmark_csv_sha256",
                ),
                "broker_tradability_verified": True,
                "official_source_id": official_source_id,
                "broker_source_id": broker_source_id,
            }
        )
    return normalized


def normalize_manifest_v2(manifest: dict[str, Any]) -> dict[str, Any]:
    if set(manifest) != MANIFEST_V2_FIELDS:
        raise ScreenBlockedError(
            f"manifest fields must be exactly {sorted(MANIFEST_V2_FIELDS)}"
        )
    if manifest["schema"] != MANIFEST_SCHEMA_V2:
        raise ScreenBlockedError(f"unsupported manifest schema: {manifest['schema']!r}")
    supplied_hash = exact_sha256(manifest["manifest_hash"], "manifest_hash")
    hash_input = {
        key: value for key, value in manifest.items() if key != "manifest_hash"
    }
    if sha256_json(hash_input) != supplied_hash:
        raise ScreenBlockedError(
            "manifest_hash does not match canonical manifest content"
        )
    as_of = as_iso_date(manifest["as_of"], "as_of")
    analysis_date = as_iso_date(manifest["analysis_date"], "analysis_date")
    if analysis_date > as_of:
        raise ScreenBlockedError("analysis_date must not be after as_of")
    if manifest["build_status"] not in {"READY", "BLOCKED"}:
        raise ScreenBlockedError("build_status must be READY or BLOCKED")
    if manifest["build_status"] == "BLOCKED":
        raise ScreenBlockedError("manifest build_status is BLOCKED")
    coverage_contract = normalize_catalog_coverage_contract(
        manifest["catalog_coverage_contract"]
    )
    source_hashes = normalize_source_hashes(manifest["source_hashes"], as_of)
    source_ids = {source["source_id"] for source in source_hashes}
    instruments = normalize_instruments_v2(
        manifest["instruments"],
        analysis_date,
        source_ids,
    )
    exclusions = normalize_exclusions(manifest["exclusions"])
    for exclusion in exclusions:
        if exclusion["official_source_id"] not in source_ids:
            raise ScreenBlockedError(
                "exclusion references an unknown official_source_id"
            )
        if (
            exclusion["broker_source_id"] is not None
            and exclusion["broker_source_id"] not in source_ids
        ):
            raise ScreenBlockedError("exclusion references an unknown broker_source_id")
    counts = normalize_counts(manifest["counts"])
    if counts["included"] != len(instruments):
        raise ScreenBlockedError("counts.included must equal instruments length")
    if counts["excluded"] != len(exclusions):
        raise ScreenBlockedError("counts.excluded must equal exclusions length")
    for exchange in sorted(SUPPORTED_EXCHANGES):
        included = sum(instrument["exchange"] == exchange for instrument in instruments)
        excluded = sum(exclusion["exchange"] == exchange for exclusion in exclusions)
        missing_catalog = sum(
            exclusion["exchange"] == exchange
            and "missing_eod_mapping" in exclusion["reasons"]
            for exclusion in exclusions
        )
        official = counts["by_exchange"][exchange]["official"]
        catalog_mapped = counts["by_exchange"][exchange]["catalog_mapped"]
        if catalog_mapped > official:
            raise ScreenBlockedError(
                f"counts.by_exchange.{exchange}.catalog_mapped cannot exceed official"
            )
        if catalog_mapped != official - missing_catalog:
            raise ScreenBlockedError(
                f"counts.by_exchange.{exchange}.catalog_mapped does not match "
                "official rows without missing_eod_mapping"
            )
        if counts["by_exchange"][exchange]["included"] > official:
            raise ScreenBlockedError(
                f"counts.by_exchange.{exchange}.included cannot exceed official"
            )
        if counts["by_exchange"][exchange]["included"] != included:
            raise ScreenBlockedError(
                f"counts.by_exchange.{exchange}.included does not match instruments"
            )
        if counts["by_exchange"][exchange]["excluded"] != excluded:
            raise ScreenBlockedError(
                f"counts.by_exchange.{exchange}.excluded does not match exclusions"
            )
    expected_status = (
        "READY"
        if all(
            counts["by_exchange"][exchange]["included"] > 0
            and meets_minimum_coverage_ratio(
                counts["by_exchange"][exchange]["catalog_mapped"],
                counts["by_exchange"][exchange]["official"],
                coverage_contract["minimum_ratio_by_exchange"][exchange],
            )
            and meets_minimum_coverage_ratio(
                counts["by_exchange"][exchange]["included"],
                counts["by_exchange"][exchange]["official"],
                coverage_contract["minimum_screenable_ratio_by_exchange"][exchange],
            )
            for exchange in SUPPORTED_EXCHANGES
        )
        else "BLOCKED"
    )
    if manifest["build_status"] != expected_status:
        raise ScreenBlockedError(
            "build_status must be READY only when every exchange has included rows "
            "and meets its catalog mapping and screenable coverage contracts"
        )
    normalized = {
        "schema": MANIFEST_SCHEMA_V2,
        "build_status": "READY",
        "as_of": as_of,
        "analysis_date": analysis_date,
        "catalog_coverage_contract": coverage_contract,
        "source_hashes": source_hashes,
        "instruments": instruments,
        "exclusions": exclusions,
        "counts": counts,
    }
    normalized["manifest_hash"] = sha256_json(normalized)
    if normalized["manifest_hash"] != supplied_hash:
        raise ScreenBlockedError("manifest content is not in normalized canonical form")
    return normalized


def normalize_manifest(manifest: dict[str, Any]) -> dict[str, Any]:
    schema = manifest.get("schema")
    if schema == MANIFEST_SCHEMA:
        return normalize_manifest_v1(manifest)
    if schema == MANIFEST_SCHEMA_V2:
        return normalize_manifest_v2(manifest)
    raise ScreenBlockedError(f"unsupported manifest schema: {schema!r}")


def normalize_selector_v1(selector: dict[str, Any]) -> dict[str, Any]:
    required = {
        "selector_version",
        "min_total_score",
        "eligible_setup_statuses",
        "top_k_by_market",
        "max_blocked_fraction",
    }
    if set(selector) != required:
        raise ScreenBlockedError(f"selector fields must be exactly {sorted(required)}")
    if selector["selector_version"] != SELECTOR_VERSION:
        raise ScreenBlockedError(
            f"unsupported selector version: {selector['selector_version']!r}"
        )
    minimum = as_decimal(selector["min_total_score"], "min_total_score")
    if minimum < 0 or minimum > 100:
        raise ScreenBlockedError("min_total_score must be between 0 and 100")
    maximum_blocked = as_decimal(
        selector["max_blocked_fraction"], "max_blocked_fraction"
    )
    if maximum_blocked < 0 or maximum_blocked > 1:
        raise ScreenBlockedError("max_blocked_fraction must be between 0 and 1")

    normalized_statuses = normalize_eligible_setup_statuses(
        selector["eligible_setup_statuses"]
    )

    top_k = selector["top_k_by_market"]
    if not isinstance(top_k, dict) or set(top_k) != SUPPORTED_MARKETS:
        raise ScreenBlockedError("top_k_by_market must contain exactly KR and US")
    normalized_top_k: dict[str, int] = {}
    for market in sorted(SUPPORTED_MARKETS):
        value = top_k[market]
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ScreenBlockedError(
                f"top_k_by_market.{market} must be an integer >= 0"
            )
        normalized_top_k[market] = value

    return {
        "selector_version": SELECTOR_VERSION,
        "min_total_score": format(minimum, "f"),
        "eligible_setup_statuses": normalized_statuses,
        "top_k_by_market": normalized_top_k,
        "max_blocked_fraction": format(maximum_blocked, "f"),
    }


def normalize_selector_v2(selector: dict[str, Any]) -> dict[str, Any]:
    required = {
        "selector_version",
        "min_total_score",
        "eligible_setup_statuses",
        "top_k_by_exchange",
        "min_selected_by_exchange",
        "max_blocked_fraction",
    }
    if set(selector) != required:
        raise ScreenBlockedError(f"selector fields must be exactly {sorted(required)}")
    if selector["selector_version"] != SELECTOR_VERSION_V2:
        raise ScreenBlockedError(
            f"unsupported selector version: {selector['selector_version']!r}"
        )
    minimum = as_decimal(selector["min_total_score"], "min_total_score")
    if minimum < 0 or minimum > 100:
        raise ScreenBlockedError("min_total_score must be between 0 and 100")
    maximum_blocked = as_decimal(
        selector["max_blocked_fraction"], "max_blocked_fraction"
    )
    if maximum_blocked < 0 or maximum_blocked > 1:
        raise ScreenBlockedError("max_blocked_fraction must be between 0 and 1")
    normalized_statuses = normalize_eligible_setup_statuses(
        selector["eligible_setup_statuses"]
    )
    top_k = selector["top_k_by_exchange"]
    if not isinstance(top_k, dict) or set(top_k) != SUPPORTED_EXCHANGES:
        raise ScreenBlockedError(
            "top_k_by_exchange must contain exactly KOSPI, KOSDAQ, NYSE, NASDAQ"
        )
    normalized_top_k: dict[str, int] = {}
    for exchange in sorted(SUPPORTED_EXCHANGES):
        normalized_top_k[exchange] = nonnegative_integer(
            top_k[exchange],
            f"top_k_by_exchange.{exchange}",
        )
    minimum_selected = selector["min_selected_by_exchange"]
    if (
        not isinstance(minimum_selected, dict)
        or set(minimum_selected) != SUPPORTED_EXCHANGES
    ):
        raise ScreenBlockedError(
            "min_selected_by_exchange must contain exactly KOSPI, KOSDAQ, NYSE, NASDAQ"
        )
    normalized_minimum_selected: dict[str, int] = {}
    for exchange in sorted(SUPPORTED_EXCHANGES):
        value = nonnegative_integer(
            minimum_selected[exchange],
            f"min_selected_by_exchange.{exchange}",
        )
        if value > normalized_top_k[exchange]:
            raise ScreenBlockedError(
                f"min_selected_by_exchange.{exchange} must be <= "
                f"top_k_by_exchange.{exchange}"
            )
        normalized_minimum_selected[exchange] = value
    return {
        "selector_version": SELECTOR_VERSION_V2,
        "min_total_score": format(minimum, "f"),
        "eligible_setup_statuses": normalized_statuses,
        "top_k_by_exchange": normalized_top_k,
        "min_selected_by_exchange": normalized_minimum_selected,
        "max_blocked_fraction": format(maximum_blocked, "f"),
    }


def normalize_selector(selector: dict[str, Any]) -> dict[str, Any]:
    version = selector.get("selector_version")
    if version == SELECTOR_VERSION:
        return normalize_selector_v1(selector)
    if version == SELECTOR_VERSION_V2:
        return normalize_selector_v2(selector)
    raise ScreenBlockedError(f"unsupported selector version: {version!r}")


def instrument_market(instrument: dict[str, Any]) -> str:
    return str(instrument.get("market"))


def instrument_ticker(instrument: dict[str, Any]) -> str:
    if "ticker" in instrument:
        return str(instrument["ticker"])
    return str(instrument["canonical_symbol"])


def instrument_tick_size(instrument: dict[str, Any]) -> str:
    if "tick_size" in instrument:
        return str(instrument["tick_size"])
    return str(instrument["tick_contract"]["resolved_tick_size"])


def blocked_payload(instrument: dict[str, Any], reason: str) -> dict[str, Any]:
    return {
        "source_skill": "quant-stock-technical",
        "result_schema": "quant-stock-technical/v1",
        "calculation_status": "BLOCKED",
        "reason": reason,
        "method_version": qta.METHOD_VERSION,
        "market": instrument_market(instrument),
        "ticker": instrument_ticker(instrument),
    }


def analyze_instrument(
    instrument: dict[str, Any],
    analysis_date: date,
    manifest_directory: Path,
) -> dict[str, Any]:
    ticker_path = Path(instrument["ticker_csv"])
    benchmark_path = Path(instrument["benchmark_csv"])
    if not ticker_path.is_absolute():
        ticker_path = manifest_directory / ticker_path
    if not benchmark_path.is_absolute():
        benchmark_path = manifest_directory / benchmark_path
    try:
        if "ticker_csv_sha256" in instrument:
            if sha256_file(ticker_path) != instrument["ticker_csv_sha256"]:
                raise ScreenBlockedError("ticker_csv_sha256 does not match ticker_csv")
            if sha256_file(benchmark_path) != instrument["benchmark_csv_sha256"]:
                raise ScreenBlockedError(
                    "benchmark_csv_sha256 does not match benchmark_csv"
                )
        ticker_rows, benchmark_rows = qta.align(
            qta.read_csv(str(ticker_path)),
            qta.read_csv(str(benchmark_path)),
            analysis_date,
        )
        return qta.calculate(
            ticker_rows,
            benchmark_rows,
            instrument_market(instrument),
            instrument_ticker(instrument),
            float(Decimal(instrument_tick_size(instrument))),
            instrument["source_name"],
        )
    except (qta.BlockedError, OSError, ValueError) as exc:
        return blocked_payload(instrument, str(exc))


def score(payload: dict[str, Any], path: str) -> Decimal:
    value: Any = payload
    for part in path.split("."):
        if not isinstance(value, dict) or part not in value:
            raise ScreenBlockedError(
                f"{payload.get('market')}:{payload.get('ticker')} missing {path}"
            )
        value = value[part]
    return as_decimal(value, path)


def ranking_key(payload: dict[str, Any]) -> tuple[Any, ...]:
    return (
        -score(payload, "total_score"),
        -score(payload, "medium.score"),
        -score(payload, "long.score"),
        -score(payload, "short.score"),
        score(payload, "risk.score"),
        str(payload["market"]),
        str(payload["ticker"]),
    )


def finalize_screen(
    manifest: dict[str, Any],
    selector: dict[str, Any],
    results: list[dict[str, Any]],
) -> dict[str, Any]:
    instrument_by_key = {
        (item["market"], item["ticker"]): {
            "market": item["market"],
            "ticker": item["ticker"],
            "tick_size": item["tick_size"],
        }
        for item in manifest["instruments"]
    }
    result_by_key: dict[tuple[str, str], dict[str, Any]] = {}
    for result in results:
        key = (str(result.get("market")), str(result.get("ticker")))
        if key in result_by_key:
            raise ScreenBlockedError(f"duplicate calculation result: {key}")
        result_by_key[key] = result

    ordered_results: list[dict[str, Any]] = []
    for instrument in manifest["instruments"]:
        key = (instrument["market"], instrument["ticker"])
        if key not in result_by_key:
            raise ScreenBlockedError(f"missing calculation result: {key}")
        ordered_results.append(result_by_key[key])

    blocked_count = sum(
        result.get("calculation_status") != "READY" for result in ordered_results
    )
    blocked_fraction = Decimal(blocked_count) / Decimal(len(ordered_results))
    maximum_blocked = Decimal(selector["max_blocked_fraction"])
    screen_status = "READY" if blocked_fraction <= maximum_blocked else "BLOCKED"

    decisions: list[dict[str, Any]] = []
    eligible_by_market: dict[str, list[dict[str, Any]]] = {
        market: [] for market in SUPPORTED_MARKETS
    }
    statuses = set(selector["eligible_setup_statuses"])
    minimum = Decimal(selector["min_total_score"])
    for payload in ordered_results:
        reasons: list[str] = []
        if payload.get("calculation_status") != "READY":
            reasons.append("calculation_not_ready")
        else:
            if payload.get("setup_status") not in statuses:
                reasons.append("setup_status_ineligible")
            if score(payload, "total_score") < minimum:
                reasons.append("score_below_minimum")
        eligible = not reasons
        if eligible:
            market = str(payload["market"])
            if market not in eligible_by_market:
                raise ScreenBlockedError(f"result market is unsupported: {market}")
            eligible_by_market[market].append(payload)
        decisions.append(
            {
                "market": payload.get("market"),
                "ticker": payload.get("ticker"),
                "instrument": instrument_by_key[
                    (str(payload.get("market")), str(payload.get("ticker")))
                ],
                "eligible": eligible,
                "reasons": reasons,
                "qta": payload,
            }
        )

    selected: dict[str, list[dict[str, Any]]] = {}
    selected_keys: set[tuple[str, str]] = set()
    for market in sorted(SUPPORTED_MARKETS):
        ranked = sorted(eligible_by_market[market], key=ranking_key)
        chosen = ranked[: selector["top_k_by_market"][market]]
        selected[market] = [
            {
                "rank": index,
                "instrument": instrument_by_key[(market, str(payload["ticker"]))],
                "qta": payload,
            }
            for index, payload in enumerate(chosen, start=1)
        ]
        selected_keys.update((market, str(payload["ticker"])) for payload in chosen)

    for decision in decisions:
        decision["selected"] = (
            decision["market"],
            decision["ticker"],
        ) in selected_keys

    output = {
        "source_skill": "quant-stock-technical",
        "schema": SCREEN_SCHEMA,
        "screen_status": screen_status,
        "method_version": qta.METHOD_VERSION,
        "selector_version": SELECTOR_VERSION,
        "analysis_date": manifest["analysis_date"],
        "manifest_hash": sha256_json(manifest),
        "selector_hash": sha256_json(selector),
        "blocked_count": blocked_count,
        "blocked_fraction": format(blocked_fraction, "f"),
        "instrument_count": len(ordered_results),
        "selector": selector,
        "selected": selected if screen_status == "READY" else {"KR": [], "US": []},
        "decisions": decisions,
    }
    output["screen_hash"] = sha256_json(output)
    return output


def finalize_screen_v2(
    manifest: dict[str, Any],
    selector: dict[str, Any],
    results: list[dict[str, Any]],
) -> dict[str, Any]:
    instrument_by_key = {
        (instrument["market"], instrument["canonical_symbol"]): instrument
        for instrument in manifest["instruments"]
    }
    result_by_key: dict[tuple[str, str], dict[str, Any]] = {}
    for result in results:
        key = (str(result.get("market")), str(result.get("ticker")))
        if key in result_by_key:
            raise ScreenBlockedError(f"duplicate calculation result: {key}")
        result_by_key[key] = result

    ordered_results: list[dict[str, Any]] = []
    for instrument in manifest["instruments"]:
        key = (instrument["market"], instrument["canonical_symbol"])
        if key not in result_by_key:
            raise ScreenBlockedError(f"missing calculation result: {key}")
        ordered_results.append(result_by_key[key])
    if len(result_by_key) != len(ordered_results):
        raise ScreenBlockedError("calculation results include unknown instruments")

    blocked_count = sum(
        result.get("calculation_status") != "READY" for result in ordered_results
    )
    blocked_fraction = Decimal(blocked_count) / Decimal(len(ordered_results))
    maximum_blocked = Decimal(selector["max_blocked_fraction"])

    statuses = set(selector["eligible_setup_statuses"])
    minimum = Decimal(selector["min_total_score"])
    eligible_by_exchange: dict[str, list[dict[str, Any]]] = {
        exchange: [] for exchange in SUPPORTED_EXCHANGES
    }
    decision_by_key: dict[tuple[str, str], dict[str, Any]] = {}
    decisions: list[dict[str, Any]] = []
    for payload in ordered_results:
        key = (str(payload.get("market")), str(payload.get("ticker")))
        instrument = instrument_by_key[key]
        reasons: list[str] = []
        if payload.get("calculation_status") != "READY":
            reasons.append("calculation_not_ready")
        else:
            if payload.get("setup_status") not in statuses:
                reasons.append("setup_status_ineligible")
            if score(payload, "total_score") < minimum:
                reasons.append("score_below_minimum")
        eligible = not reasons
        if eligible:
            eligible_by_exchange[instrument["exchange"]].append(payload)
        decision = {
            "market": instrument["market"],
            "exchange": instrument["exchange"],
            "canonical_symbol": instrument["canonical_symbol"],
            "instrument": instrument,
            "eligible": eligible,
            "reasons": reasons,
            "exchange_rank": None,
            "selected": False,
            "qta": payload,
        }
        decisions.append(decision)
        decision_by_key[key] = decision

    selected: dict[str, list[dict[str, Any]]] = {
        exchange: [] for exchange in sorted(SUPPORTED_EXCHANGES)
    }
    ranked_by_exchange: dict[str, list[dict[str, Any]]] = {}
    for exchange in sorted(SUPPORTED_EXCHANGES):
        ranked = sorted(eligible_by_exchange[exchange], key=ranking_key)
        ranked_by_exchange[exchange] = ranked
        for exchange_rank, payload in enumerate(ranked, start=1):
            key = (str(payload["market"]), str(payload["ticker"]))
            decision_by_key[key]["exchange_rank"] = exchange_rank
    minimum_selection_met = all(
        len(ranked_by_exchange[exchange])
        >= selector["min_selected_by_exchange"][exchange]
        for exchange in SUPPORTED_EXCHANGES
    )
    screen_status = (
        "READY"
        if blocked_fraction <= maximum_blocked and minimum_selection_met
        else "BLOCKED"
    )

    selected_keys: set[tuple[str, str]] = set()
    if screen_status == "READY":
        for exchange in sorted(SUPPORTED_EXCHANGES):
            ranked = ranked_by_exchange[exchange]
            chosen = ranked[: selector["top_k_by_exchange"][exchange]]
            selected[exchange] = [
                {
                    "exchange_rank": exchange_rank,
                    "instrument": instrument_by_key[
                        (str(payload["market"]), str(payload["ticker"]))
                    ],
                    "qta": payload,
                }
                for exchange_rank, payload in enumerate(chosen, start=1)
            ]
            selected_keys.update(
                (str(payload["market"]), str(payload["ticker"])) for payload in chosen
            )
    for key in selected_keys:
        decision_by_key[key]["selected"] = True

    output = {
        "source_skill": "quant-stock-technical",
        "schema": SCREEN_SCHEMA_V2,
        "screen_status": screen_status,
        "method_version": qta.METHOD_VERSION,
        "selector_version": SELECTOR_VERSION_V2,
        "analysis_date": manifest["analysis_date"],
        "manifest_hash": manifest["manifest_hash"],
        "selector_hash": sha256_json(selector),
        "blocked_count": blocked_count,
        "blocked_fraction": format(blocked_fraction, "f"),
        "instrument_count": len(ordered_results),
        "selector": selector,
        "selected": selected,
        "decisions": decisions,
    }
    output["screen_hash"] = sha256_json(output)
    return output


def build_screen(
    manifest: dict[str, Any],
    selector: dict[str, Any],
    manifest_directory: Path,
) -> dict[str, Any]:
    normalized_manifest = normalize_manifest(manifest)
    normalized_selector = normalize_selector(selector)
    if (
        normalized_manifest["schema"] == MANIFEST_SCHEMA
        and normalized_selector["selector_version"] != SELECTOR_VERSION
    ):
        raise ScreenBlockedError("qta-universe-manifest/v1 requires qta-screen-1.0.0")
    if (
        normalized_manifest["schema"] == MANIFEST_SCHEMA_V2
        and normalized_selector["selector_version"] != SELECTOR_VERSION_V2
    ):
        raise ScreenBlockedError("qta-universe-manifest/v2 requires qta-screen-1.1.0")
    analysis_date = date.fromisoformat(normalized_manifest["analysis_date"])
    results = [
        analyze_instrument(instrument, analysis_date, manifest_directory)
        for instrument in normalized_manifest["instruments"]
    ]
    if normalized_manifest["schema"] == MANIFEST_SCHEMA:
        return finalize_screen(normalized_manifest, normalized_selector, results)
    return finalize_screen_v2(normalized_manifest, normalized_selector, results)


def synthetic_result(
    market: str,
    ticker: str,
    total: str,
    medium: str,
    long: str,
    short: str,
    risk: str,
) -> dict[str, Any]:
    return {
        "source_skill": "quant-stock-technical",
        "result_schema": "quant-stock-technical/v1",
        "calculation_status": "READY",
        "setup_status": "READY",
        "method_version": qta.METHOD_VERSION,
        "analysis_date": "2026-07-25",
        "market": market,
        "ticker": ticker,
        "source_name": "screen-self-test",
        "shared_sessions": 1000,
        "score_basis": "ticker-relative historical percentile; not probability of profit",
        "short": {"opinion": "긍정", "score": float(short)},
        "medium": {"opinion": "긍정", "score": float(medium)},
        "long": {"opinion": "긍정", "score": float(long)},
        "risk": {"score": float(risk), "counterpoint": "self-test"},
        "entry_price": 100.0,
        "stop_price": 90.0,
        "take_profit_price": 120.0,
        "total_score": float(total),
    }


def self_test() -> None:
    raw_instruments = [
        {
            "market": "US",
            "ticker": "BBB",
            "ticker_csv": "bbb.csv",
            "benchmark_csv": "bench.csv",
            "tick_size": "0.01",
            "source_name": "self-test",
        },
        {
            "market": "US",
            "ticker": "AAA",
            "ticker_csv": "aaa.csv",
            "benchmark_csv": "bench.csv",
            "tick_size": "0.01",
            "source_name": "self-test",
        },
    ]
    selector = normalize_selector(
        {
            "selector_version": SELECTOR_VERSION,
            "min_total_score": "60",
            "eligible_setup_statuses": ["READY"],
            "top_k_by_market": {"KR": 0, "US": 2},
            "max_blocked_fraction": "0",
        }
    )
    manifest_a = normalize_manifest(
        {
            "schema": MANIFEST_SCHEMA,
            "analysis_date": "2026-07-25",
            "instruments": raw_instruments,
        }
    )
    manifest_b = normalize_manifest(
        {
            "schema": MANIFEST_SCHEMA,
            "analysis_date": "2026-07-25",
            "instruments": list(reversed(raw_instruments)),
        }
    )
    results = [
        synthetic_result("US", "BBB", "70", "70", "70", "70", "30"),
        synthetic_result("US", "AAA", "70", "70", "70", "70", "30"),
    ]
    first = finalize_screen(manifest_a, selector, results)
    second = finalize_screen(manifest_b, selector, list(reversed(results)))
    assert first == second
    assert [item["qta"]["ticker"] for item in first["selected"]["US"]] == [
        "AAA",
        "BBB",
    ]
    assert first["screen_status"] == "READY"
    assert len(first["screen_hash"]) == 64
    assert normalize_eligible_setup_statuses(["READY", "CONDITIONAL", "READY"]) == [
        "CONDITIONAL",
        "READY",
    ]
    try:
        normalize_selector({**selector, "eligible_setup_statuses": ["BLOCKED"]})
    except ScreenBlockedError as exc:
        assert "subset of READY and CONDITIONAL" in str(exc)
    else:
        raise AssertionError("qta-screen-1.0.0 accepted an unknown setup status")

    exchanges = ["KOSDAQ", "KOSPI", "NASDAQ", "NYSE"]
    source_hashes = []
    for source_id in sorted(
        f"{role}-{exchange}"
        for exchange in exchanges
        for role in ("broker", "official")
    ):
        role, exchange = source_id.split("-", maxsplit=1)
        source_hashes.append(
            {
                "source_id": source_id,
                "role": role,
                "provider": "self-test",
                "exchange": exchange,
                "as_of": "2026-07-26",
                "path": f"/tmp/{source_id}.txt",
                "sha256": "0" * 64,
            }
        )
    instrument_specs = [
        ("KOSDAQ", "KR", "100002", "1", "KOSDAQ_COMPOSITE", "KRW", "KRX"),
        ("KOSPI", "KR", "100001", "1", "KOSPI_COMPOSITE", "KRW", "KRX"),
        ("NASDAQ", "US", "AAA", "0.01", "NASDAQ_COMPOSITE", "USD", "NASD"),
        ("NASDAQ", "US", "CCC", "0.01", "NASDAQ_COMPOSITE", "USD", "NASD"),
        ("NYSE", "US", "BBB", "0.01", "NYSE_COMPOSITE", "USD", "NYSE"),
    ]
    instruments_v2 = []
    for (
        exchange,
        market,
        symbol,
        tick_size,
        benchmark_id,
        currency,
        venue,
    ) in instrument_specs:
        instruments_v2.append(
            {
                "market": market,
                "exchange": exchange,
                "canonical_symbol": symbol,
                "data_symbol": symbol,
                "broker_symbol": symbol,
                "instrument_type": "COMMON",
                "benchmark_id": benchmark_id,
                "currency": currency,
                "venue": venue,
                "ticker_csv": f"/tmp/{exchange}-{symbol}.csv",
                "benchmark_csv": f"/tmp/{exchange}-benchmark.csv",
                "tick_contract": {
                    "schema": "qta-tick-contract/v1",
                    "kind": "RESOLVED_PRICE_LADDER",
                    "rule_id": f"{exchange}_SELF_TEST",
                    "effective_date": "2026-07-25",
                    "reference_price": "100",
                    "resolved_tick_size": tick_size,
                },
                "source_name": "screen-self-test",
                "ticker_csv_sha256": "1" * 64,
                "benchmark_csv_sha256": "2" * 64,
                "broker_tradability_verified": True,
                "official_source_id": f"official-{exchange}",
                "broker_source_id": f"broker-{exchange}",
            }
        )
    by_exchange = {}
    for exchange in exchanges:
        included = sum(spec[0] == exchange for spec in instrument_specs)
        by_exchange[exchange] = {
            "official": included,
            "broker": included,
            "catalog_mapped": included,
            "included": included,
            "excluded": 0,
        }
    manifest_v2_without_hash = {
        "schema": MANIFEST_SCHEMA_V2,
        "build_status": "READY",
        "as_of": "2026-07-26",
        "analysis_date": "2026-07-25",
        "catalog_coverage_contract": {
            "schema": CATALOG_COVERAGE_SCHEMA,
            "minimum_ratio_by_exchange": {exchange: "1" for exchange in EXCHANGE_ORDER},
            "minimum_screenable_ratio_by_exchange": {
                exchange: "1" for exchange in EXCHANGE_ORDER
            },
        },
        "source_hashes": source_hashes,
        "instruments": instruments_v2,
        "exclusions": [],
        "counts": {
            "official_rows": len(instrument_specs),
            "broker_rows": len(instrument_specs),
            "catalog_mapped_rows": len(instrument_specs),
            "included": len(instrument_specs),
            "excluded": 0,
            "by_exchange": by_exchange,
        },
    }
    normalized_manifest_v2 = normalize_manifest(
        {
            **manifest_v2_without_hash,
            "manifest_hash": sha256_json(manifest_v2_without_hash),
        }
    )
    selector_v2 = normalize_selector(
        {
            "selector_version": SELECTOR_VERSION_V2,
            "min_total_score": "60",
            "eligible_setup_statuses": ["READY"],
            "top_k_by_exchange": {
                "KOSDAQ": 1,
                "KOSPI": 1,
                "NASDAQ": 1,
                "NYSE": 1,
            },
            "min_selected_by_exchange": {
                "KOSDAQ": 1,
                "KOSPI": 1,
                "NASDAQ": 1,
                "NYSE": 1,
            },
            "max_blocked_fraction": "0",
        }
    )
    try:
        normalize_selector(
            {**selector_v2, "eligible_setup_statuses": ["READY", "UNKNOWN"]}
        )
    except ScreenBlockedError as exc:
        assert "subset of READY and CONDITIONAL" in str(exc)
    else:
        raise AssertionError("qta-screen-1.1.0 accepted an unknown setup status")
    results_v2 = [
        synthetic_result(market, symbol, "70", "70", "70", "70", "30")
        for _, market, symbol, _, _, _, _ in reversed(instrument_specs)
    ]
    screen_v2_a = finalize_screen_v2(
        normalized_manifest_v2,
        selector_v2,
        results_v2,
    )
    screen_v2_b = finalize_screen_v2(
        normalized_manifest_v2,
        selector_v2,
        list(reversed(results_v2)),
    )
    assert screen_v2_a == screen_v2_b
    assert screen_v2_a["schema"] == SCREEN_SCHEMA_V2
    assert set(screen_v2_a["selected"]) == SUPPORTED_EXCHANGES
    assert all(
        selected[0]["exchange_rank"] == 1
        for selected in screen_v2_a["selected"].values()
    )
    assert all(
        set(selected[0]["instrument"]) == INSTRUMENT_V2_FIELDS
        for selected in screen_v2_a["selected"].values()
    )
    decision_v2_by_symbol = {
        decision["canonical_symbol"]: decision for decision in screen_v2_a["decisions"]
    }
    assert decision_v2_by_symbol["AAA"]["exchange_rank"] == 1
    assert decision_v2_by_symbol["AAA"]["selected"]
    assert decision_v2_by_symbol["CCC"]["exchange_rank"] == 2
    assert not decision_v2_by_symbol["CCC"]["selected"]
    coverage_selector_v2 = normalize_selector(
        {
            "selector_version": SELECTOR_VERSION_V2,
            "min_total_score": "60",
            "eligible_setup_statuses": ["READY"],
            "top_k_by_exchange": {
                "KOSDAQ": 1,
                "KOSPI": 1,
                "NASDAQ": 1,
                "NYSE": 2,
            },
            "min_selected_by_exchange": {
                "KOSDAQ": 1,
                "KOSPI": 1,
                "NASDAQ": 1,
                "NYSE": 2,
            },
            "max_blocked_fraction": "1",
        }
    )
    coverage_blocked_v2 = finalize_screen_v2(
        normalized_manifest_v2,
        coverage_selector_v2,
        results_v2,
    )
    assert coverage_blocked_v2["blocked_fraction"] == "0"
    assert coverage_blocked_v2["screen_status"] == "BLOCKED"
    assert all(not selected for selected in coverage_blocked_v2["selected"].values())
    tampered_manifest_v2 = {
        **manifest_v2_without_hash,
        "analysis_date": "2026-07-24",
        "manifest_hash": sha256_json(manifest_v2_without_hash),
    }
    try:
        normalize_manifest(tampered_manifest_v2)
    except ScreenBlockedError:
        pass
    else:
        raise AssertionError("tampered qta-universe-manifest/v2 was accepted")

    sparse_by_exchange = {
        exchange: dict(values) for exchange, values in by_exchange.items()
    }
    sparse_by_exchange["KOSPI"]["official"] += 1
    sparse_by_exchange["KOSPI"]["excluded"] += 1
    sparse_without_hash = {
        **manifest_v2_without_hash,
        "catalog_coverage_contract": {
            "schema": CATALOG_COVERAGE_SCHEMA,
            "minimum_ratio_by_exchange": {exchange: "1" for exchange in EXCHANGE_ORDER},
            "minimum_screenable_ratio_by_exchange": {
                exchange: "0.5" for exchange in EXCHANGE_ORDER
            },
        },
        "exclusions": [
            {
                "exchange": "KOSPI",
                "canonical_symbol": "999999",
                "broker_symbol": None,
                "name": "Missing Catalog",
                "reasons": ["missing_eod_mapping"],
                "official_source_id": "official-KOSPI",
                "broker_source_id": "broker-KOSPI",
            }
        ],
        "counts": {
            **manifest_v2_without_hash["counts"],
            "official_rows": len(instrument_specs) + 1,
            "excluded": 1,
            "by_exchange": sparse_by_exchange,
        },
    }
    try:
        normalize_manifest(
            {
                **sparse_without_hash,
                "manifest_hash": sha256_json(sparse_without_hash),
            }
        )
    except ScreenBlockedError as exc:
        assert "catalog mapping and screenable coverage contracts" in str(exc)
    else:
        raise AssertionError("sparse full-coverage manifest was accepted as READY")

    unscreenable_by_exchange = {
        exchange: dict(values) for exchange, values in by_exchange.items()
    }
    unscreenable_by_exchange["KOSPI"]["official"] += 1
    unscreenable_by_exchange["KOSPI"]["catalog_mapped"] += 1
    unscreenable_by_exchange["KOSPI"]["excluded"] += 1
    unscreenable_without_hash = {
        **manifest_v2_without_hash,
        "exclusions": [
            {
                "exchange": "KOSPI",
                "canonical_symbol": "999998",
                "broker_symbol": None,
                "name": "Not Tradable",
                "reasons": ["not_broker_tradable"],
                "official_source_id": "official-KOSPI",
                "broker_source_id": "broker-KOSPI",
            }
        ],
        "counts": {
            **manifest_v2_without_hash["counts"],
            "official_rows": len(instrument_specs) + 1,
            "catalog_mapped_rows": len(instrument_specs) + 1,
            "excluded": 1,
            "by_exchange": unscreenable_by_exchange,
        },
    }
    try:
        normalize_manifest(
            {
                **unscreenable_without_hash,
                "manifest_hash": sha256_json(unscreenable_without_hash),
            }
        )
    except ScreenBlockedError as exc:
        assert "catalog mapping and screenable coverage contracts" in str(exc)
    else:
        raise AssertionError("under-screenable manifest was accepted as READY")
    assert (
        normalize_exclusions(
            [
                {
                    "exchange": "KOSPI",
                    "canonical_symbol": "999999",
                    "broker_symbol": None,
                    "name": "",
                    "reasons": ["not_broker_tradable"],
                    "official_source_id": "official-KOSPI",
                    "broker_source_id": None,
                }
            ]
        )[0]["name"]
        == ""
    )
    print(
        canonical_json(
            {
                "self_test": "PASS",
                "selector_version": SELECTOR_VERSION,
                "screen_hash": first["screen_hash"],
            }
        )
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest")
    parser.add_argument("--selector")
    parser.add_argument("--output")
    parser.add_argument("--self-test", action="store_true")
    return parser.parse_args()


def emit(value: dict[str, Any], output: str | None) -> None:
    rendered = json.dumps(
        value, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False
    )
    if output:
        Path(output).write_text(rendered + "\n", encoding="utf-8")
    print(rendered)


def main() -> int:
    args = parse_args()
    if args.self_test:
        self_test()
        return 0
    if not args.manifest or not args.selector:
        emit(
            {
                "source_skill": "quant-stock-technical",
                "schema": SCREEN_SCHEMA,
                "screen_status": "BLOCKED",
                "reason": "--manifest and --selector are required",
            },
            args.output,
        )
        return 2
    manifest_path = Path(args.manifest).resolve()
    failure_schema = SCREEN_SCHEMA
    try:
        manifest = load_object(manifest_path)
        if manifest.get("schema") == MANIFEST_SCHEMA_V2:
            failure_schema = SCREEN_SCHEMA_V2
        output = build_screen(
            manifest,
            load_object(Path(args.selector).resolve()),
            manifest_path.parent,
        )
    except (ScreenBlockedError, OSError, ValueError, json.JSONDecodeError) as exc:
        emit(
            {
                "source_skill": "quant-stock-technical",
                "schema": failure_schema,
                "screen_status": "BLOCKED",
                "reason": str(exc),
            },
            args.output,
        )
        return 2
    emit(output, args.output)
    return 0 if output["screen_status"] == "READY" else 2


if __name__ == "__main__":
    sys.exit(main())

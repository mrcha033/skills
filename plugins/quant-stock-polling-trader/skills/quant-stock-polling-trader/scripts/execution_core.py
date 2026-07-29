#!/usr/bin/env python3
"""Deterministic planning primitives and durable order-intent ledger."""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import sqlite3
import sys
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import ROUND_DOWN, Decimal, InvalidOperation
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

EXECUTION_VERSION = "open1h-exec-2.0.0"
PLAN_SCHEMA = "qta-order-plan/v2"
RISK_SCHEMA = "qta-risk-policy/v1"
EXECUTION_POLICY_SCHEMA = "qta-execution-policy/v1"
MARKET_SESSION_SCHEMA = "qta-market-session/v1"
ACCOUNT_SCHEMA = "qta-account-snapshot/v2"
EXPOSURE_SCHEMA = "qta-exposure-snapshot/v1"
EXPOSURE_SCHEMA_V2 = "qta-exposure-snapshot/v2"
SCREEN_SCHEMA = "qta-screen/v1"
SCREEN_SCHEMA_V2 = "qta-screen/v2"
MARKET_CURRENCY = {"KR": "KRW", "US": "USD"}
V2_SELECTOR_VERSION = "qta-screen-1.1.0"
QTA_METHOD_V1 = "qta-1.0.0"
QTA_METHOD_V2 = "qta-2.0.0"
SUPPORTED_QTA_METHODS = {QTA_METHOD_V1, QTA_METHOD_V2}
V2_EXCHANGES_BY_MARKET = {
    "KR": ("KOSPI", "KOSDAQ"),
    "US": ("NYSE", "NASDAQ"),
}
V2_EXCHANGE_CONTRACTS = {
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
V2_INSTRUMENT_FIELDS = {
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
V2_TICK_CONTRACT_FIELDS = {
    "schema",
    "kind",
    "rule_id",
    "effective_date",
    "reference_price",
    "resolved_tick_size",
}
V2_INSTRUMENT_TYPES = {"COMMON", "ADR", "REIT"}
V2_SCREEN_FIELDS = {
    "source_skill",
    "schema",
    "screen_status",
    "method_version",
    "selector_version",
    "analysis_date",
    "manifest_hash",
    "selector_hash",
    "blocked_count",
    "blocked_fraction",
    "instrument_count",
    "selector",
    "selected",
    "decisions",
    "screen_hash",
}
V2_SELECTOR_FIELDS = {
    "selector_version",
    "min_total_score",
    "eligible_setup_statuses",
    "top_k_by_exchange",
    "min_selected_by_exchange",
    "max_blocked_fraction",
}
V2_DECISION_FIELDS = {
    "market",
    "exchange",
    "canonical_symbol",
    "instrument",
    "eligible",
    "reasons",
    "exchange_rank",
    "selected",
    "qta",
}
QTA_READY_FIELDS = {
    "source_skill",
    "result_schema",
    "calculation_status",
    "setup_status",
    "method_version",
    "analysis_date",
    "market",
    "ticker",
    "source_name",
    "shared_sessions",
    "score_basis",
    "short",
    "medium",
    "long",
    "risk",
    "entry_price",
    "stop_price",
    "take_profit_price",
    "total_score",
    "reference_observations",
    "assumptions",
}
QTA2_READY_FIELDS = QTA_READY_FIELDS | {
    "validation_status",
    "liquidity",
    "market_regime",
}
QTA2_LIQUIDITY_FIELDS = {
    "median_20_session_turnover",
    "minimum_turnover",
    "currency",
    "status",
}
QTA2_MARKET_REGIME_FIELDS = {
    "metric",
    "minimum_change_bps",
    "max_age_seconds",
    "status",
}
QTA_BLOCKED_FIELDS = {
    "source_skill",
    "result_schema",
    "calculation_status",
    "reason",
    "method_version",
    "market",
    "ticker",
}
QTA_HORIZON_FIELDS = {"opinion", "score"}
QTA_RISK_FIELDS = {"score", "counterpoint"}
QTA_REFERENCE_FIELDS = {
    "return_5",
    "return_20",
    "close_over_sma_20",
    "sma_5_over_sma_20",
    "signed_log_volume_ratio_20",
    "rsi_14",
    "return_63",
    "close_over_sma_50",
    "sma_20_over_sma_50",
    "relative_return_63",
    "adx_direction_14",
    "return_126",
    "return_252",
    "close_over_sma_200",
    "sma_50_over_sma_200",
    "relative_return_252",
    "ATR14/close",
    "20d annualized volatility",
    "252d maximum drawdown",
    "60d gap p95",
}
QTA_SCORE_BASIS = "ticker-relative historical percentile; not probability of profit"
QTA2_SCORE_BASIS = (
    "ticker-relative historical percentiles aligned to first-hour "
    "continuation; not probability of profit"
)
QTA_ASSUMPTIONS = [
    "input rows are finalized completed daily sessions",
    "prices are corporate-action adjusted and aligned to analysis-date raw price scale",
    "fees, tax, FX, slippage, position size, and execution are excluded",
]
QTA2_ASSUMPTIONS = [
    *QTA_ASSUMPTIONS,
    "QTA 2.0 is research-only until multi-session walk-forward validation",
    "same-session benchmark regime admission is required downstream",
]
QTA2_LIQUIDITY_FLOOR = {
    "KR": Decimal("1000000000"),
    "US": Decimal("1000000"),
}
QTA2_REGIME_METRIC = "same_session_previous_close_return_bps"
QTA2_REGIME_MINIMUM_CHANGE_BPS = Decimal("0")
QTA2_REGIME_MAX_AGE_SECONDS = 90
QTA_SETUP_STATUSES = {"READY", "CONDITIONAL"}
QTA_OPINIONS = {"강한 긍정", "긍정", "중립", "부정", "강한 부정"}
QTA_MIN_SHARED_SESSIONS = 756
QTA_MIN_REFERENCE_OBSERVATIONS = 252
QTA_MAX_REFERENCE_OBSERVATIONS = 756
ENTRY_WINDOWS = {
    "KR": {
        "timezone": "Asia/Seoul",
        "start": (9, 0),
        "end": (10, 0),
    },
    "US": {
        "timezone": "America/New_York",
        "start": (9, 30),
        "end": (10, 30),
    },
}
MARKET_SESSION_SOURCE_FIELDS = {
    "schema",
    "provider",
    "source_id",
    "source_as_of",
    "market",
    "timezone",
    "session_date",
    "previous_session_date",
    "scheduled_status",
    "regular_open",
    "regular_close",
}
MARKET_SESSION_FIELDS = MARKET_SESSION_SOURCE_FIELDS | {
    "source_path",
    "source_sha256",
    "session_hash",
}
MAX_SNAPSHOT_AGE_SECONDS = 3600

TERMINAL_STATES = {
    "FILLED",
    "CANCELLED",
    "REJECTED",
    "MANUAL_BLOCK",
}
TRANSITIONS = {
    "PLANNED": {"WAIT_TRIGGER", "MANUAL_BLOCK"},
    "WAIT_TRIGGER": {"RESERVED", "CANCELLED", "MANUAL_BLOCK"},
    "RESERVED": {"SUBMITTING", "CANCELLED", "MANUAL_BLOCK"},
    "SUBMITTING": {"ACKNOWLEDGED", "UNKNOWN", "REJECTED"},
    "ACKNOWLEDGED": {
        "PARTIALLY_FILLED",
        "FILLED",
        "CANCEL_PENDING",
        "CANCELLED",
        "REJECTED",
        "UNKNOWN",
    },
    "PARTIALLY_FILLED": {
        "PARTIALLY_FILLED",
        "FILLED",
        "CANCEL_PENDING",
        "CANCELLED",
        "UNKNOWN",
    },
    "CANCEL_PENDING": {
        "CANCELLED",
        "FILLED",
        "UNKNOWN",
    },
    "UNKNOWN": {"RECONCILING", "MANUAL_BLOCK"},
    "RECONCILING": {
        "ACKNOWLEDGED",
        "PARTIALLY_FILLED",
        "FILLED",
        "CANCELLED",
        "REJECTED",
        "MANUAL_BLOCK",
    },
}


class BlockedError(ValueError):
    """Raised when an execution input is incomplete or unsafe."""


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


def decimal_value(value: Any, field: str) -> Decimal:
    if isinstance(value, bool):
        raise BlockedError(f"{field} must be a decimal string or number")
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise BlockedError(f"{field} must be a decimal string or number") from exc
    if not result.is_finite():
        raise BlockedError(f"{field} must be finite")
    return result


def nonnegative_decimal(value: Any, field: str) -> Decimal:
    result = decimal_value(value, field)
    if result < 0:
        raise BlockedError(f"{field} must be >= 0")
    return result


def positive_decimal(value: Any, field: str) -> Decimal:
    result = decimal_value(value, field)
    if result <= 0:
        raise BlockedError(f"{field} must be > 0")
    return result


def exact_fields(value: dict[str, Any], required: set[str], label: str) -> None:
    if set(value) != required:
        missing = sorted(required - set(value))
        extra = sorted(set(value) - required)
        raise BlockedError(f"{label} fields mismatch; missing={missing}, extra={extra}")


def parse_aware_datetime(value: Any, field: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError as exc:
        raise BlockedError(f"{field} must be an ISO-8601 datetime") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise BlockedError(f"{field} must include a UTC offset")
    return parsed


def parse_iso_date(value: Any, field: str) -> date:
    if not isinstance(value, str):
        raise BlockedError(f"{field} must be YYYY-MM-DD")
    try:
        parsed = date.fromisoformat(value)
    except ValueError as exc:
        raise BlockedError(f"{field} must be YYYY-MM-DD") from exc
    if parsed.isoformat() != value:
        raise BlockedError(f"{field} must be YYYY-MM-DD")
    return parsed


def load_json_object(path: str | Path) -> dict[str, Any]:
    source = Path(path)
    value = json.loads(source.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise BlockedError(f"{source} must contain a JSON object")
    return value


def emit_json(value: dict[str, Any], output: str | None = None) -> None:
    rendered = json.dumps(
        value, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False
    )
    if output:
        Path(output).write_text(rendered + "\n", encoding="utf-8")
    print(rendered)


def normalize_symbol(market: str, symbol: str) -> str:
    normalized_market = market.upper()
    normalized = symbol.strip().upper()
    if normalized_market == "KR":
        if normalized.isdigit() and len(normalized) <= 6:
            return normalized.zfill(6)
        if len(normalized) == 6 and all(
            character in "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
            for character in normalized
        ):
            return normalized
        raise BlockedError(f"invalid KR symbol: {symbol!r}")
    if normalized_market == "US":
        if not normalized or any(
            character not in "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789.-"
            for character in normalized
        ):
            raise BlockedError(f"invalid US symbol: {symbol!r}")
        return normalized
    raise BlockedError(f"unsupported market: {market!r}")


def normalized_risk_policy(value: dict[str, Any]) -> dict[str, Any]:
    required = {
        "schema",
        "base_currency",
        "per_trade_risk_krw",
        "max_symbol_notional_krw",
        "max_concurrent_positions",
        "max_daily_loss_krw",
        "round_trip_cost_bps",
        "cash_buffer_bps",
        "allow_existing_additions",
        "allow_borrowed_cash",
        "allow_margin",
        "allow_short",
        "allow_auto_fx",
        "whole_shares_only",
    }
    exact_fields(value, required, "risk policy")
    if value["schema"] != RISK_SCHEMA:
        raise BlockedError(f"unsupported risk policy schema: {value['schema']!r}")
    if value["base_currency"] != "KRW":
        raise BlockedError("base_currency must be KRW")
    for field in (
        "allow_existing_additions",
        "allow_borrowed_cash",
        "allow_margin",
        "allow_short",
        "allow_auto_fx",
        "whole_shares_only",
    ):
        if not isinstance(value[field], bool):
            raise BlockedError(f"{field} must be boolean")
    if value["allow_borrowed_cash"]:
        raise BlockedError("borrowed cash is unsupported by this execution contract")
    if value["allow_margin"] or value["allow_short"] or value["allow_auto_fx"]:
        raise BlockedError("margin, shorting, and automatic FX must remain disabled")
    if not value["whole_shares_only"]:
        raise BlockedError("initial execution contract requires whole shares")
    count = value["max_concurrent_positions"]
    if isinstance(count, bool) or not isinstance(count, int) or count <= 0:
        raise BlockedError("max_concurrent_positions must be a positive integer")
    for field in (
        "per_trade_risk_krw",
        "max_symbol_notional_krw",
        "max_daily_loss_krw",
    ):
        positive_decimal(value[field], field)
    for field in ("round_trip_cost_bps", "cash_buffer_bps"):
        amount = nonnegative_decimal(value[field], field)
        if amount > Decimal(10000):
            raise BlockedError(f"{field} must be <= 10000")
    return {
        **value,
        "per_trade_risk_krw": format(
            positive_decimal(value["per_trade_risk_krw"], "per_trade_risk_krw"),
            "f",
        ),
        "max_symbol_notional_krw": format(
            positive_decimal(
                value["max_symbol_notional_krw"], "max_symbol_notional_krw"
            ),
            "f",
        ),
        "max_daily_loss_krw": format(
            positive_decimal(value["max_daily_loss_krw"], "max_daily_loss_krw"),
            "f",
        ),
        "round_trip_cost_bps": format(
            nonnegative_decimal(value["round_trip_cost_bps"], "round_trip_cost_bps"),
            "f",
        ),
        "cash_buffer_bps": format(
            nonnegative_decimal(value["cash_buffer_bps"], "cash_buffer_bps"),
            "f",
        ),
    }


def canonical_market_session_hash(value: dict[str, Any]) -> str:
    """Hash a market-session artifact without its self-referential hash."""
    try:
        return sha256_json(
            {key: item for key, item in value.items() if key != "session_hash"}
        )
    except (TypeError, ValueError) as exc:
        raise BlockedError("market_session must contain canonical JSON values") from exc


def market_session_from_source(path: str | Path) -> dict[str, Any]:
    source_path = Path(path).resolve()
    try:
        source_payload = load_json_object(source_path)
        source_sha256 = sha256_file(source_path)
    except (OSError, json.JSONDecodeError) as exc:
        raise BlockedError(
            "market-session source must be a readable JSON snapshot"
        ) from exc
    exact_fields(
        source_payload,
        MARKET_SESSION_SOURCE_FIELDS,
        "market-session source snapshot",
    )
    result = {
        **source_payload,
        "source_path": str(source_path),
        "source_sha256": source_sha256,
    }
    result["session_hash"] = canonical_market_session_hash(result)
    return result


def normalized_market_session(
    value: Any,
    *,
    expected_market: str,
    expected_timezone: str,
) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise BlockedError("market_session must be an object")
    exact_fields(value, MARKET_SESSION_FIELDS, "market_session")
    if value["schema"] != MARKET_SESSION_SCHEMA:
        raise BlockedError(f"market_session.schema must be {MARKET_SESSION_SCHEMA}")
    provider = validate_nonempty_string(value["provider"], "market_session.provider")
    source_id = validate_nonempty_string(value["source_id"], "market_session.source_id")
    if value["market"] != expected_market:
        raise BlockedError(f"market_session.market must be {expected_market}")
    if value["timezone"] != expected_timezone:
        raise BlockedError(f"market_session.timezone must be {expected_timezone}")
    if value["scheduled_status"] != "OPEN":
        raise BlockedError("market_session.scheduled_status must be OPEN")

    session_date = parse_iso_date(value["session_date"], "market_session.session_date")
    previous_session_date = parse_iso_date(
        value["previous_session_date"],
        "market_session.previous_session_date",
    )
    if session_date.weekday() >= 5:
        raise BlockedError("market_session.session_date must be a weekday")
    if previous_session_date >= session_date:
        raise BlockedError(
            "market_session.previous_session_date must precede session_date"
        )

    zone = ZoneInfo(expected_timezone)
    regular_open = parse_aware_datetime(
        value["regular_open"], "market_session.regular_open"
    )
    regular_close = parse_aware_datetime(
        value["regular_close"], "market_session.regular_close"
    )
    source_as_of = parse_aware_datetime(
        value["source_as_of"], "market_session.source_as_of"
    )
    for supplied, localized, field in (
        (regular_open, regular_open.astimezone(zone), "regular_open"),
        (regular_close, regular_close.astimezone(zone), "regular_close"),
    ):
        if (
            supplied.replace(tzinfo=None) != localized.replace(tzinfo=None)
            or supplied.utcoffset() != localized.utcoffset()
        ):
            raise BlockedError(
                "market_session."
                f"{field} local clock and UTC offset must match {expected_timezone}"
            )
        if supplied.isoformat() != value[field]:
            raise BlockedError(
                f"market_session.{field} must be a canonical ISO-8601 datetime"
            )
        if localized.date() != session_date:
            raise BlockedError(f"market_session.{field} must fall on session_date")
    if regular_close <= regular_open:
        raise BlockedError("market_session.regular_close must be after regular_open")
    if source_as_of.isoformat() != value["source_as_of"]:
        raise BlockedError(
            "market_session.source_as_of must be a canonical ISO-8601 datetime"
        )
    if source_as_of > regular_open:
        raise BlockedError("market_session.source_as_of must not be after regular_open")

    source_path_text = validate_nonempty_string(
        value["source_path"], "market_session.source_path"
    )
    source_path = Path(source_path_text)
    if not source_path.is_absolute():
        raise BlockedError("market_session.source_path must be absolute")
    validate_sha256(value["source_sha256"], "market_session.source_sha256")
    try:
        actual_source_sha256 = sha256_file(source_path)
        source_payload = load_json_object(source_path)
    except (OSError, json.JSONDecodeError) as exc:
        raise BlockedError(
            "market_session.source_path must be a readable JSON snapshot"
        ) from exc
    if actual_source_sha256 != value["source_sha256"]:
        raise BlockedError("market_session.source_sha256 does not match source_path")
    exact_fields(
        source_payload,
        MARKET_SESSION_SOURCE_FIELDS,
        "market_session source snapshot",
    )
    expected_source_payload = {key: value[key] for key in MARKET_SESSION_SOURCE_FIELDS}
    if source_payload != expected_source_payload:
        raise BlockedError(
            "market_session fields do not match the hashed source snapshot"
        )

    validate_sha256(value["session_hash"], "market_session.session_hash")
    if value["session_hash"] != canonical_market_session_hash(value):
        raise BlockedError(
            "market_session.session_hash does not match canonical contents"
        )
    return {
        **value,
        "provider": provider,
        "source_id": source_id,
        "source_as_of": source_as_of.isoformat(),
        "regular_open": regular_open.isoformat(),
        "regular_close": regular_close.isoformat(),
    }


def normalized_execution_policy(value: dict[str, Any]) -> dict[str, Any]:
    required = {
        "schema",
        "market",
        "timezone",
        "entry_window_start",
        "entry_window_end",
        "market_session",
        "snapshot_max_age_seconds",
        "poll_interval_seconds",
        "quote_max_age_seconds",
        "max_spread_bps",
        "max_gap_bps",
        "trigger_mode",
        "order_ttl_seconds",
        "order_type",
        "time_in_force",
        "allow_partial_fill",
        "cancel_remainder_at_window_end",
    }
    exact_fields(value, required, "execution policy")
    if value["schema"] != EXECUTION_POLICY_SCHEMA:
        raise BlockedError(f"unsupported execution policy schema: {value['schema']!r}")
    market = str(value["market"]).upper()
    if market not in MARKET_CURRENCY:
        raise BlockedError("market must be KR or US")
    window_contract = ENTRY_WINDOWS[market]
    expected_timezone = window_contract["timezone"]
    if value["timezone"] != expected_timezone:
        raise BlockedError(f"{market} timezone must be {expected_timezone}")
    start = parse_aware_datetime(value["entry_window_start"], "entry_window_start")
    end = parse_aware_datetime(value["entry_window_end"], "entry_window_end")
    if start >= end:
        raise BlockedError("entry window start must be before end")
    zone = ZoneInfo(expected_timezone)
    start_local = start.astimezone(zone)
    end_local = end.astimezone(zone)
    for supplied, localized, field in (
        (start, start_local, "entry_window_start"),
        (end, end_local, "entry_window_end"),
    ):
        if (
            supplied.replace(tzinfo=None) != localized.replace(tzinfo=None)
            or supplied.utcoffset() != localized.utcoffset()
        ):
            raise BlockedError(
                f"{field} local clock and UTC offset must match {expected_timezone}"
            )
    expected_start = window_contract["start"]
    expected_end = window_contract["end"]
    if (
        (start_local.hour, start_local.minute) != expected_start
        or start_local.second != 0
        or start_local.microsecond != 0
        or (end_local.hour, end_local.minute) != expected_end
        or end_local.second != 0
        or end_local.microsecond != 0
        or start_local.date() != end_local.date()
    ):
        raise BlockedError(
            f"{market} entry window must be "
            f"{expected_start[0]:02d}:{expected_start[1]:02d}-"
            f"{expected_end[0]:02d}:{expected_end[1]:02d} {expected_timezone}"
        )
    if (end - start).total_seconds() != 3600:
        raise BlockedError("entry window must be exactly one hour")
    market_session = normalized_market_session(
        value["market_session"],
        expected_market=market,
        expected_timezone=expected_timezone,
    )
    regular_open = parse_aware_datetime(
        market_session["regular_open"], "market_session.regular_open"
    )
    regular_close = parse_aware_datetime(
        market_session["regular_close"], "market_session.regular_close"
    )
    if start != regular_open:
        raise BlockedError("entry_window_start must equal market_session.regular_open")
    if end != regular_open + timedelta(hours=1):
        raise BlockedError(
            "entry_window_end must equal one hour after market_session.regular_open"
        )
    if end > regular_close:
        raise BlockedError(
            "entry_window_end must not exceed market_session.regular_close"
        )
    for field in (
        "snapshot_max_age_seconds",
        "poll_interval_seconds",
        "quote_max_age_seconds",
        "order_ttl_seconds",
    ):
        amount = value[field]
        if isinstance(amount, bool) or not isinstance(amount, int) or amount <= 0:
            raise BlockedError(f"{field} must be a positive integer")
    if value["snapshot_max_age_seconds"] > MAX_SNAPSHOT_AGE_SECONDS:
        raise BlockedError(
            f"snapshot_max_age_seconds must be <= {MAX_SNAPSHOT_AGE_SECONDS}"
        )
    if value["order_type"] != "LIMIT" or value["time_in_force"] != "DAY":
        raise BlockedError("initial execution contract requires LIMIT + DAY")
    if value["trigger_mode"] not in {"AT_OR_ABOVE", "CROSS_FROM_BELOW"}:
        raise BlockedError("trigger_mode must be AT_OR_ABOVE or CROSS_FROM_BELOW")
    for field in ("allow_partial_fill", "cancel_remainder_at_window_end"):
        if not isinstance(value[field], bool):
            raise BlockedError(f"{field} must be boolean")
    for field in ("max_spread_bps", "max_gap_bps"):
        amount = nonnegative_decimal(value[field], field)
        if amount > Decimal(10000):
            raise BlockedError(f"{field} must be <= 10000")
    return {
        **value,
        "market": market,
        "entry_window_start": start_local.isoformat(),
        "entry_window_end": end_local.isoformat(),
        "market_session": market_session,
        "max_spread_bps": format(
            nonnegative_decimal(value["max_spread_bps"], "max_spread_bps"), "f"
        ),
        "max_gap_bps": format(
            nonnegative_decimal(value["max_gap_bps"], "max_gap_bps"), "f"
        ),
    }


def normalized_account_snapshot(
    value: dict[str, Any],
    *,
    screen_schema: str = SCREEN_SCHEMA,
) -> dict[str, Any]:
    required = {
        "schema",
        "broker",
        "environment",
        "account_alias",
        "market",
        "currency",
        "as_of",
        "settled_cash",
        "borrowed_buying_power",
        "fx_to_krw",
        "positions",
        "open_orders",
    }
    exact_fields(value, required, "account snapshot")
    if value["schema"] != ACCOUNT_SCHEMA:
        raise BlockedError(f"unsupported account snapshot schema: {value['schema']!r}")
    market = str(value["market"]).upper()
    if market not in MARKET_CURRENCY:
        raise BlockedError("account market must be KR or US")
    if value["currency"] != MARKET_CURRENCY[market]:
        raise BlockedError(
            f"{market} account currency must be {MARKET_CURRENCY[market]}"
        )
    if value["environment"] not in {"paper", "shadow", "live"}:
        raise BlockedError("account environment must be paper, shadow, or live")
    broker = validate_nonempty_string(value["broker"], "broker")
    account_alias = validate_nonempty_string(value["account_alias"], "account_alias")
    as_of = parse_aware_datetime(value["as_of"], "account as_of")
    settled = nonnegative_decimal(value["settled_cash"], "settled_cash")
    borrowed = nonnegative_decimal(
        value["borrowed_buying_power"], "borrowed_buying_power"
    )
    if not isinstance(value["positions"], list) or not isinstance(
        value["open_orders"], list
    ):
        raise BlockedError("positions and open_orders must be arrays")
    raw_fx = value["fx_to_krw"]
    if raw_fx is None:
        if market != "US" or settled != 0 or value["positions"]:
            raise BlockedError(
                "fx_to_krw may be null only for an empty US account with "
                "zero settled cash"
            )
        fx: Decimal | None = None
    else:
        fx = positive_decimal(raw_fx, "fx_to_krw")
    if screen_schema not in {SCREEN_SCHEMA, SCREEN_SCHEMA_V2}:
        raise BlockedError(f"unsupported screen schema: {screen_schema!r}")

    positions: list[dict[str, Any]] = []
    for index, position in enumerate(value["positions"]):
        label = f"account positions[{index}]"
        if not isinstance(position, dict):
            raise BlockedError(f"{label} must be an object")
        if "market" not in position or "symbol" not in position:
            raise BlockedError(f"{label} requires market and symbol")
        position_market = str(position["market"]).upper()
        if position_market != market:
            raise BlockedError(f"{label}.market must match account market {market}")
        symbol = normalize_symbol(position_market, str(position["symbol"]))
        normalized_position = {
            **position,
            "market": position_market,
            "symbol": symbol,
        }
        if screen_schema == SCREEN_SCHEMA_V2:
            if "exchange" not in position:
                raise BlockedError(f"{label} requires exchange for qta-screen/v2")
            exchange = str(position["exchange"]).upper()
            contract = V2_EXCHANGE_CONTRACTS.get(exchange)
            if contract is None or contract["market"] != position_market:
                raise BlockedError(f"{label}.exchange must match its market")
            normalized_position["exchange"] = exchange
        positions.append(normalized_position)

    open_orders: list[dict[str, Any]] = []
    for index, open_order in enumerate(value["open_orders"]):
        label = f"account open_orders[{index}]"
        if not isinstance(open_order, dict):
            raise BlockedError(f"{label} must be an object")
        required_order_fields = {"side", "market", "symbol"}
        missing_order_fields = required_order_fields - set(open_order)
        if missing_order_fields:
            raise BlockedError(
                f"{label} missing fields needed for exposure key: "
                f"{sorted(missing_order_fields)}"
            )
        side = str(open_order["side"]).upper()
        if side not in {"BUY", "SELL"}:
            raise BlockedError(f"{label}.side must be BUY or SELL")
        order_market = str(open_order["market"]).upper()
        if order_market != market:
            raise BlockedError(f"{label}.market must match account market {market}")
        symbol = normalize_symbol(order_market, str(open_order["symbol"]))
        normalized_open_order = {
            **open_order,
            "side": side,
            "market": order_market,
            "symbol": symbol,
        }
        if screen_schema == SCREEN_SCHEMA_V2:
            if "exchange" not in open_order:
                raise BlockedError(f"{label} requires exchange for qta-screen/v2")
            exchange = str(open_order["exchange"]).upper()
            contract = V2_EXCHANGE_CONTRACTS.get(exchange)
            if contract is None or contract["market"] != order_market:
                raise BlockedError(f"{label}.exchange must match its market")
            normalized_open_order["exchange"] = exchange
        open_orders.append(normalized_open_order)

    positions.sort(
        key=lambda item: (
            item["market"],
            item.get("exchange", ""),
            item["symbol"],
        )
    )
    open_orders.sort(
        key=lambda item: (
            item["market"],
            item.get("exchange", ""),
            item["symbol"],
            item["side"],
        )
    )
    return {
        **value,
        "broker": broker,
        "account_alias": account_alias,
        "market": market,
        "as_of": as_of.isoformat(),
        "settled_cash": format(settled, "f"),
        "borrowed_buying_power": format(borrowed, "f"),
        "fx_to_krw": format(fx, "f") if fx is not None else None,
        "positions": positions,
        "open_orders": open_orders,
    }


def normalized_exposure_snapshot(value: dict[str, Any]) -> dict[str, Any]:
    required = {"schema", "as_of", "positions"}
    exact_fields(value, required, "exposure snapshot")
    schema = value["schema"]
    if schema not in {EXPOSURE_SCHEMA, EXPOSURE_SCHEMA_V2}:
        raise BlockedError(f"unsupported exposure snapshot schema: {value['schema']!r}")
    as_of = parse_aware_datetime(value["as_of"], "exposure as_of")
    if not isinstance(value["positions"], list):
        raise BlockedError("exposure positions must be an array")
    positions: list[dict[str, Any]] = []
    required_position = {
        "broker",
        "market",
        "symbol",
        "quantity",
        "market_value_krw",
    }
    if schema == EXPOSURE_SCHEMA_V2:
        required_position = required_position | {"exchange"}
    for index, position in enumerate(value["positions"]):
        if not isinstance(position, dict):
            raise BlockedError(f"positions[{index}] must be an object")
        exact_fields(position, required_position, f"positions[{index}]")
        market = str(position["market"]).upper()
        symbol = normalize_symbol(market, str(position["symbol"]))
        normalized_position = {**position, "market": market, "symbol": symbol}
        if schema == EXPOSURE_SCHEMA_V2:
            exchange = str(position["exchange"]).upper()
            contract = V2_EXCHANGE_CONTRACTS.get(exchange)
            if contract is None:
                raise BlockedError(
                    f"positions[{index}].exchange is unsupported: {exchange!r}"
                )
            if contract["market"] != market:
                raise BlockedError(
                    f"positions[{index}] exchange and market are inconsistent"
                )
            normalized_position["exchange"] = exchange
        if position["quantity"] is not None:
            nonnegative_decimal(position["quantity"], f"positions[{index}].quantity")
        nonnegative_decimal(
            position["market_value_krw"],
            f"positions[{index}].market_value_krw",
        )
        positions.append(normalized_position)
    positions.sort(
        key=lambda item: (
            item["market"],
            item.get("exchange", ""),
            item["symbol"],
            item["broker"],
        )
    )
    return {**value, "as_of": as_of.isoformat(), "positions": positions}


def validate_screen(screen: dict[str, Any]) -> None:
    schema = screen.get("schema")
    if schema not in {SCREEN_SCHEMA, SCREEN_SCHEMA_V2}:
        raise BlockedError(
            f"screen schema must be {SCREEN_SCHEMA} or {SCREEN_SCHEMA_V2}"
        )
    if schema == SCREEN_SCHEMA_V2:
        validate_v2_screen(screen)
        return
    if screen.get("screen_status") != "READY":
        raise BlockedError("screen_status must be READY")
    if screen.get("method_version") != "qta-1.0.0":
        raise BlockedError("method_version must be qta-1.0.0")
    if screen.get("selector_version") != "qta-screen-1.0.0":
        raise BlockedError("selector_version must be qta-screen-1.0.0")
    if not isinstance(screen.get("selected"), dict):
        raise BlockedError("screen.selected must be an object")
    if "screen_hash" not in screen:
        raise BlockedError("screen_hash is required")
    supplied_hash = screen["screen_hash"]
    validate_sha256(supplied_hash, "screen_hash")
    expected_hash = canonical_screen_hash(screen)
    if supplied_hash != expected_hash:
        raise BlockedError("screen_hash does not match canonical screen payload")


def validate_sha256(value: Any, field: str) -> None:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise BlockedError(f"{field} must be a lowercase SHA-256 hex digest")


def canonical_screen_hash(screen: dict[str, Any]) -> str:
    """Hash a screen's canonical content without its self-referential hash."""
    try:
        return sha256_json(
            {key: item for key, item in screen.items() if key != "screen_hash"}
        )
    except (TypeError, ValueError) as exc:
        raise BlockedError("screen must contain canonical JSON values") from exc


def validate_nonempty_string(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise BlockedError(f"{field} must be a non-empty trimmed string")
    return value


def validate_positive_decimal_string(value: Any, field: str) -> Decimal:
    if not isinstance(value, str) or value != value.strip():
        raise BlockedError(f"{field} must be a positive decimal string")
    return positive_decimal(value, field)


def validate_decimal_string(
    value: Any,
    field: str,
    *,
    minimum: Decimal,
    maximum: Decimal,
) -> Decimal:
    if not isinstance(value, str) or value != value.strip():
        raise BlockedError(f"{field} must be a decimal string")
    result = decimal_value(value, field)
    if result < minimum or result > maximum:
        raise BlockedError(f"{field} must be between {minimum} and {maximum}")
    return result


def validate_nonnegative_integer(value: Any, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise BlockedError(f"{field} must be an integer >= 0")
    return value


def validate_v2_selector(selector: Any) -> dict[str, Any]:
    if not isinstance(selector, dict):
        raise BlockedError("screen.selector must be an object")
    exact_fields(selector, V2_SELECTOR_FIELDS, "screen.selector")
    if selector["selector_version"] != V2_SELECTOR_VERSION:
        raise BlockedError(
            f"screen.selector.selector_version must be {V2_SELECTOR_VERSION}"
        )
    validate_decimal_string(
        selector["min_total_score"],
        "screen.selector.min_total_score",
        minimum=Decimal(0),
        maximum=Decimal(100),
    )
    validate_decimal_string(
        selector["max_blocked_fraction"],
        "screen.selector.max_blocked_fraction",
        minimum=Decimal(0),
        maximum=Decimal(1),
    )
    statuses = selector["eligible_setup_statuses"]
    if (
        not isinstance(statuses, list)
        or not statuses
        or any(
            not isinstance(status, str) or not status or status != status.strip()
            for status in statuses
        )
    ):
        raise BlockedError(
            "screen.selector.eligible_setup_statuses must be a non-empty "
            "trimmed string array"
        )
    if statuses != sorted(set(statuses)):
        raise BlockedError(
            "screen.selector.eligible_setup_statuses must be sorted and unique"
        )
    if not set(statuses).issubset(QTA_SETUP_STATUSES):
        raise BlockedError(
            "screen.selector.eligible_setup_statuses must be a non-empty "
            "subset of READY and CONDITIONAL"
        )
    exchanges = set(V2_EXCHANGE_CONTRACTS)
    for field in ("top_k_by_exchange", "min_selected_by_exchange"):
        mapping = selector[field]
        if not isinstance(mapping, dict) or set(mapping) != exchanges:
            raise BlockedError(
                f"screen.selector.{field} must contain exactly "
                + ", ".join(sorted(exchanges))
            )
    for exchange in sorted(exchanges):
        top_k = validate_nonnegative_integer(
            selector["top_k_by_exchange"][exchange],
            f"screen.selector.top_k_by_exchange.{exchange}",
        )
        minimum_selected = validate_nonnegative_integer(
            selector["min_selected_by_exchange"][exchange],
            f"screen.selector.min_selected_by_exchange.{exchange}",
        )
        if minimum_selected > top_k:
            raise BlockedError(
                "screen.selector.min_selected_by_exchange."
                f"{exchange} must be <= top_k_by_exchange.{exchange}"
            )
    return selector


def validate_json_number(
    value: Any,
    field: str,
    *,
    minimum: Decimal,
    maximum: Decimal | None = None,
) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise BlockedError(f"{field} must be a JSON number")
    parsed = decimal_value(value, field)
    if parsed < minimum or (maximum is not None and parsed > maximum):
        upper = "" if maximum is None else f" and <= {maximum}"
        raise BlockedError(f"{field} must be >= {minimum}{upper}")
    return parsed


def qta_allowed_opinions(score: Decimal) -> set[str]:
    boundary_labels = {
        Decimal(20): {"강한 부정", "부정"},
        Decimal(40): {"부정", "중립"},
        Decimal(60): {"중립", "긍정"},
        Decimal(80): {"긍정", "강한 긍정"},
    }
    if score in boundary_labels:
        return boundary_labels[score]
    if score >= Decimal(80):
        return {"강한 긍정"}
    if score >= Decimal(60):
        return {"긍정"}
    if score >= Decimal(40):
        return {"중립"}
    if score >= Decimal(20):
        return {"부정"}
    return {"강한 부정"}


def validate_v2_qta_identity(
    qta: dict[str, Any],
    instrument: dict[str, Any],
    label: str,
) -> None:
    if (
        qta.get("source_skill") != "quant-stock-technical"
        or qta.get("result_schema") != "quant-stock-technical/v1"
        or qta.get("method_version") not in SUPPORTED_QTA_METHODS
    ):
        raise BlockedError(f"{label} provenance is invalid")
    if qta.get("market") != instrument["market"]:
        raise BlockedError(f"{label}.market must equal instrument.market")
    if qta.get("ticker") != instrument["canonical_symbol"]:
        raise BlockedError(f"{label}.ticker must equal instrument.canonical_symbol")


def validate_v2_ready_qta(
    qta: dict[str, Any],
    instrument: dict[str, Any],
    label: str,
    *,
    screen_analysis_date: date,
) -> None:
    method_version = qta.get("method_version")
    exact_fields(
        qta,
        QTA2_READY_FIELDS
        if method_version == QTA_METHOD_V2
        else QTA_READY_FIELDS,
        label,
    )
    validate_v2_qta_identity(qta, instrument, label)
    if qta["calculation_status"] != "READY":
        raise BlockedError(f"{label}.calculation_status must be READY")
    qta_analysis_date = parse_iso_date(qta["analysis_date"], f"{label}.analysis_date")
    if qta_analysis_date != screen_analysis_date:
        raise BlockedError(f"{label}.analysis_date must equal screen.analysis_date")
    source_name = validate_nonempty_string(qta["source_name"], f"{label}.source_name")
    if source_name != instrument["source_name"]:
        raise BlockedError(f"{label}.source_name must equal instrument.source_name")
    shared_sessions = qta["shared_sessions"]
    if (
        isinstance(shared_sessions, bool)
        or not isinstance(shared_sessions, int)
        or shared_sessions < QTA_MIN_SHARED_SESSIONS
    ):
        raise BlockedError(
            f"{label}.shared_sessions must be an integer >= {QTA_MIN_SHARED_SESSIONS}"
        )
    expected_score_basis = (
        QTA2_SCORE_BASIS
        if method_version == QTA_METHOD_V2
        else QTA_SCORE_BASIS
    )
    if qta["score_basis"] != expected_score_basis:
        raise BlockedError(f"{label}.score_basis is invalid")

    horizon_scores: dict[str, Decimal] = {}
    for horizon in ("short", "medium", "long"):
        payload = qta[horizon]
        horizon_label = f"{label}.{horizon}"
        if not isinstance(payload, dict):
            raise BlockedError(f"{horizon_label} must be an object")
        exact_fields(payload, QTA_HORIZON_FIELDS, horizon_label)
        horizon_score = validate_json_number(
            payload["score"],
            f"{horizon_label}.score",
            minimum=Decimal(0),
            maximum=Decimal(100),
        )
        horizon_scores[horizon] = horizon_score
        if payload["opinion"] not in qta_allowed_opinions(horizon_score):
            raise BlockedError(f"{horizon_label}.opinion does not match its score")

    risk = qta["risk"]
    if not isinstance(risk, dict):
        raise BlockedError(f"{label}.risk must be an object")
    exact_fields(risk, QTA_RISK_FIELDS, f"{label}.risk")
    risk_score = validate_json_number(
        risk["score"],
        f"{label}.risk.score",
        minimum=Decimal(0),
        maximum=Decimal(100),
    )
    validate_nonempty_string(risk["counterpoint"], f"{label}.risk.counterpoint")

    entry = validate_json_number(
        qta["entry_price"],
        f"{label}.entry_price",
        minimum=Decimal(0),
    )
    stop = validate_json_number(
        qta["stop_price"],
        f"{label}.stop_price",
        minimum=Decimal(0),
    )
    take_profit = validate_json_number(
        qta["take_profit_price"],
        f"{label}.take_profit_price",
        minimum=Decimal(0),
    )
    if stop <= 0 or not stop < entry < take_profit:
        raise BlockedError(
            f"{label} prices must satisfy stop_price < entry_price < take_profit_price"
        )
    tick = validate_positive_decimal_string(
        instrument["tick_contract"]["resolved_tick_size"],
        f"{label}.instrument_tick_size",
    )
    for price, field in (
        (entry, "entry_price"),
        (stop, "stop_price"),
        (take_profit, "take_profit_price"),
    ):
        if price % tick != 0:
            raise BlockedError(f"{label}.{field} must align to the resolved tick")
    expected_take_profit = entry + Decimal(2) * (entry - stop)
    if take_profit != expected_take_profit:
        raise BlockedError(
            f"{label}.take_profit_price must equal the "
            f"{method_version} 2R target"
        )
    total_score = validate_json_number(
        qta["total_score"],
        f"{label}.total_score",
        minimum=Decimal(0),
        maximum=Decimal(100),
    )
    if method_version == QTA_METHOD_V2:
        recomputed_total = (
            Decimal("0.50") * horizon_scores["short"]
            + Decimal("0.30") * horizon_scores["medium"]
            + Decimal("0.20") * (Decimal(100) - risk_score)
        )
    else:
        recomputed_total = (
            Decimal("0.25") * horizon_scores["short"]
            + Decimal("0.35") * horizon_scores["medium"]
            + Decimal("0.40") * horizon_scores["long"]
            - Decimal("0.20") * max(risk_score - Decimal(50), Decimal(0))
        )
    recomputed_total = min(Decimal(100), max(Decimal(0), recomputed_total))
    if abs(total_score - recomputed_total) > Decimal("0.02"):
        raise BlockedError(
            f"{label}.total_score does not match the {method_version} score formula"
        )
    liquidity_ready = True
    if method_version == QTA_METHOD_V2:
        if qta["validation_status"] != "RESEARCH_ONLY":
            raise BlockedError(
                f"{label}.validation_status must be RESEARCH_ONLY"
            )
        liquidity = qta["liquidity"]
        if not isinstance(liquidity, dict):
            raise BlockedError(f"{label}.liquidity must be an object")
        exact_fields(
            liquidity,
            QTA2_LIQUIDITY_FIELDS,
            f"{label}.liquidity",
        )
        median_turnover = validate_json_number(
            liquidity["median_20_session_turnover"],
            f"{label}.liquidity.median_20_session_turnover",
            minimum=Decimal(0),
        )
        minimum_turnover = validate_json_number(
            liquidity["minimum_turnover"],
            f"{label}.liquidity.minimum_turnover",
            minimum=Decimal(0),
        )
        expected_currency = MARKET_CURRENCY[instrument["market"]]
        if liquidity["currency"] != expected_currency:
            raise BlockedError(
                f"{label}.liquidity.currency must be {expected_currency}"
            )
        expected_floor = QTA2_LIQUIDITY_FLOOR[instrument["market"]]
        if minimum_turnover != expected_floor:
            raise BlockedError(
                f"{label}.liquidity.minimum_turnover does not match QTA 2.0"
            )
        liquidity_ready = median_turnover >= minimum_turnover
        expected_liquidity_status = "READY" if liquidity_ready else "BLOCKED"
        if liquidity["status"] != expected_liquidity_status:
            raise BlockedError(
                f"{label}.liquidity.status does not match turnover"
            )
        market_regime = qta["market_regime"]
        if not isinstance(market_regime, dict):
            raise BlockedError(f"{label}.market_regime must be an object")
        exact_fields(
            market_regime,
            QTA2_MARKET_REGIME_FIELDS,
            f"{label}.market_regime",
        )
        if market_regime["metric"] != QTA2_REGIME_METRIC:
            raise BlockedError(f"{label}.market_regime.metric is invalid")
        minimum_change = validate_json_number(
            market_regime["minimum_change_bps"],
            f"{label}.market_regime.minimum_change_bps",
            minimum=Decimal("-10000"),
            maximum=Decimal("10000"),
        )
        if minimum_change != QTA2_REGIME_MINIMUM_CHANGE_BPS:
            raise BlockedError(
                f"{label}.market_regime.minimum_change_bps does not match "
                "QTA 2.0"
            )
        if (
            isinstance(market_regime["max_age_seconds"], bool)
            or market_regime["max_age_seconds"]
            != QTA2_REGIME_MAX_AGE_SECONDS
        ):
            raise BlockedError(
                f"{label}.market_regime.max_age_seconds does not match QTA 2.0"
            )
        if market_regime["status"] != "REQUIRED":
            raise BlockedError(
                f"{label}.market_regime.status must be REQUIRED"
            )
    allowed_setup_statuses = (
        {"READY", "CONDITIONAL"}
        if total_score == Decimal(60)
        else {"READY"}
        if total_score > Decimal(60)
        else {"CONDITIONAL"}
    )
    if not liquidity_ready:
        allowed_setup_statuses = {"CONDITIONAL"}
    if qta["setup_status"] not in allowed_setup_statuses:
        raise BlockedError(f"{label}.setup_status does not match total_score")

    references = qta["reference_observations"]
    if not isinstance(references, dict):
        raise BlockedError(f"{label}.reference_observations must be an object")
    exact_fields(
        references,
        QTA_REFERENCE_FIELDS,
        f"{label}.reference_observations",
    )
    maximum_reference_count = min(
        QTA_MAX_REFERENCE_OBSERVATIONS,
        shared_sessions - 1,
    )
    for feature in sorted(QTA_REFERENCE_FIELDS):
        count = references[feature]
        if (
            isinstance(count, bool)
            or not isinstance(count, int)
            or count < QTA_MIN_REFERENCE_OBSERVATIONS
            or count > maximum_reference_count
        ):
            raise BlockedError(
                f"{label}.reference_observations.{feature} must be an integer "
                f"between {QTA_MIN_REFERENCE_OBSERVATIONS} and "
                f"{maximum_reference_count}"
            )
    expected_assumptions = (
        QTA2_ASSUMPTIONS
        if method_version == QTA_METHOD_V2
        else QTA_ASSUMPTIONS
    )
    if qta["assumptions"] != expected_assumptions:
        raise BlockedError(
            f"{label}.assumptions do not match {method_version}"
        )


def validate_v2_blocked_qta(
    qta: dict[str, Any],
    instrument: dict[str, Any],
    label: str,
) -> None:
    exact_fields(qta, QTA_BLOCKED_FIELDS, label)
    validate_v2_qta_identity(qta, instrument, label)
    if qta["calculation_status"] != "BLOCKED":
        raise BlockedError(f"{label}.calculation_status must be BLOCKED")
    validate_nonempty_string(qta["reason"], f"{label}.reason")


def validate_v2_qta(
    qta: dict[str, Any],
    instrument: dict[str, Any],
    label: str,
    *,
    screen_analysis_date: date,
    require_ready: bool,
) -> None:
    calculation_status = qta.get("calculation_status")
    if calculation_status == "READY":
        validate_v2_ready_qta(
            qta,
            instrument,
            label,
            screen_analysis_date=screen_analysis_date,
        )
        return
    if calculation_status == "BLOCKED" and not require_ready:
        validate_v2_blocked_qta(qta, instrument, label)
        return
    expected = "READY" if require_ready else "READY or BLOCKED"
    raise BlockedError(f"{label}.calculation_status must be {expected}")


def qta_ranking_key(qta: dict[str, Any]) -> tuple[Any, ...]:
    if qta.get("method_version") == QTA_METHOD_V2:
        return (
            -decimal_value(qta["total_score"], "qta.total_score"),
            -decimal_value(qta["short"]["score"], "qta.short.score"),
            -decimal_value(qta["medium"]["score"], "qta.medium.score"),
            decimal_value(qta["risk"]["score"], "qta.risk.score"),
            -decimal_value(qta["long"]["score"], "qta.long.score"),
            str(qta["market"]),
            str(qta["ticker"]),
        )
    return (
        -decimal_value(qta["total_score"], "qta.total_score"),
        -decimal_value(qta["medium"]["score"], "qta.medium.score"),
        -decimal_value(qta["long"]["score"], "qta.long.score"),
        -decimal_value(qta["short"]["score"], "qta.short.score"),
        decimal_value(qta["risk"]["score"], "qta.risk.score"),
        str(qta["market"]),
        str(qta["ticker"]),
    )


def validate_v2_instrument(
    instrument: dict[str, Any],
    exchange: str,
    label: str,
    *,
    analysis_date: date | None = None,
) -> dict[str, Any]:
    exact_fields(instrument, V2_INSTRUMENT_FIELDS, label)
    contract = V2_EXCHANGE_CONTRACTS[exchange]
    for field in ("market", "benchmark_id", "currency", "venue"):
        if instrument[field] != contract[field]:
            raise BlockedError(
                f"{label}.{field} must be {contract[field]} for {exchange}"
            )
    if instrument["exchange"] != exchange:
        raise BlockedError(f"{label}.exchange must be {exchange}")
    if instrument["instrument_type"] not in V2_INSTRUMENT_TYPES:
        raise BlockedError(f"{label}.instrument_type must be COMMON, ADR, or REIT")
    market = contract["market"]
    canonical_symbol = normalize_symbol(market, str(instrument["canonical_symbol"]))
    broker_symbol = normalize_symbol(market, str(instrument["broker_symbol"]))
    if canonical_symbol != instrument["canonical_symbol"]:
        raise BlockedError(f"{label}.canonical_symbol must be normalized")
    if broker_symbol != instrument["broker_symbol"]:
        raise BlockedError(f"{label}.broker_symbol must be normalized")
    validate_nonempty_string(instrument["data_symbol"], f"{label}.data_symbol")
    for field in (
        "source_name",
        "official_source_id",
        "broker_source_id",
    ):
        validate_nonempty_string(instrument[field], f"{label}.{field}")
    for field in ("ticker_csv", "benchmark_csv"):
        path = validate_nonempty_string(instrument[field], f"{label}.{field}")
        if not Path(path).is_absolute():
            raise BlockedError(f"{label}.{field} must be an absolute path")
    if not isinstance(instrument["broker_tradability_verified"], bool):
        raise BlockedError(f"{label}.broker_tradability_verified must be boolean")
    if not instrument["broker_tradability_verified"]:
        raise BlockedError(f"{label} is not broker-tradability verified")
    validate_sha256(instrument["ticker_csv_sha256"], f"{label}.ticker_csv_sha256")
    validate_sha256(
        instrument["benchmark_csv_sha256"],
        f"{label}.benchmark_csv_sha256",
    )
    tick_contract = instrument["tick_contract"]
    if not isinstance(tick_contract, dict):
        raise BlockedError(f"{label}.tick_contract must be an object")
    exact_fields(tick_contract, V2_TICK_CONTRACT_FIELDS, f"{label}.tick_contract")
    if tick_contract["schema"] != "qta-tick-contract/v1":
        raise BlockedError(f"{label}.tick_contract.schema must be qta-tick-contract/v1")
    if tick_contract["kind"] != "RESOLVED_PRICE_LADDER":
        raise BlockedError(f"{label}.tick_contract.kind must be RESOLVED_PRICE_LADDER")
    validate_nonempty_string(
        tick_contract["rule_id"],
        f"{label}.tick_contract.rule_id",
    )
    effective_date = validate_nonempty_string(
        tick_contract["effective_date"],
        f"{label}.tick_contract.effective_date",
    )
    try:
        parsed_effective_date = date.fromisoformat(effective_date)
    except ValueError as exc:
        raise BlockedError(
            f"{label}.tick_contract.effective_date must be YYYY-MM-DD"
        ) from exc
    if parsed_effective_date.isoformat() != effective_date:
        raise BlockedError(f"{label}.tick_contract.effective_date must be YYYY-MM-DD")
    if analysis_date is not None and parsed_effective_date > analysis_date:
        raise BlockedError(
            f"{label}.tick_contract.effective_date must not be after analysis_date"
        )
    validate_positive_decimal_string(
        tick_contract["reference_price"],
        f"{label}.tick_contract.reference_price",
    )
    validate_positive_decimal_string(
        tick_contract["resolved_tick_size"],
        f"{label}.tick_contract.resolved_tick_size",
    )
    return instrument


def validate_v2_selected(
    selected: dict[str, Any],
    *,
    analysis_date: date,
    method_version: str,
) -> dict[tuple[str, str], dict[str, Any]]:
    expected_exchanges = set(V2_EXCHANGE_CONTRACTS)
    if set(selected) != expected_exchanges:
        raise BlockedError(
            "qta-screen/v2 selected must contain exactly "
            + ", ".join(sorted(expected_exchanges))
        )
    seen: dict[tuple[str, str], dict[str, Any]] = {}
    for exchange in sorted(expected_exchanges):
        candidates = selected[exchange]
        if not isinstance(candidates, list):
            raise BlockedError(f"screen.selected.{exchange} must be an array")
        ranks: list[int] = []
        for index, selected_item in enumerate(candidates):
            label = f"screen.selected.{exchange}[{index}]"
            if not isinstance(selected_item, dict):
                raise BlockedError(f"{label} must be an object")
            exact_fields(
                selected_item,
                {"exchange_rank", "instrument", "qta"},
                label,
            )
            exchange_rank = selected_item["exchange_rank"]
            if (
                isinstance(exchange_rank, bool)
                or not isinstance(exchange_rank, int)
                or exchange_rank <= 0
            ):
                raise BlockedError(f"{label}.exchange_rank must be a positive integer")
            ranks.append(exchange_rank)
            instrument = selected_item["instrument"]
            qta = selected_item["qta"]
            if not isinstance(instrument, dict) or not isinstance(qta, dict):
                raise BlockedError(f"{label}.instrument and qta must be objects")
            validate_v2_instrument(
                instrument,
                exchange,
                f"{label}.instrument",
                analysis_date=analysis_date,
            )
            expected_market = V2_EXCHANGE_CONTRACTS[exchange]["market"]
            if instrument["market"] != expected_market:
                raise BlockedError(
                    f"{label}.instrument.market must be {expected_market}"
                )
            validate_v2_qta(
                qta,
                instrument,
                f"{label}.qta",
                screen_analysis_date=analysis_date,
                require_ready=True,
            )
            if qta["method_version"] != method_version:
                raise BlockedError(
                    f"{label}.qta method differs from screen.method_version"
                )
            key = (exchange, instrument["broker_symbol"])
            if key in seen:
                raise BlockedError(
                    f"duplicate selected broker symbol: {exchange}:{key[1]}"
                )
            seen[key] = selected_item
        if ranks != list(range(1, len(ranks) + 1)):
            raise BlockedError(
                f"screen.selected.{exchange} exchange_rank values must be "
                "ordered and contiguous"
            )
    return seen


def validate_v2_screen(screen: dict[str, Any]) -> None:
    exact_fields(screen, V2_SCREEN_FIELDS, "qta-screen/v2")
    if screen["source_skill"] != "quant-stock-technical":
        raise BlockedError("qta-screen/v2 source_skill must be quant-stock-technical")
    if screen["schema"] != SCREEN_SCHEMA_V2:
        raise BlockedError(f"qta-screen/v2 schema must be {SCREEN_SCHEMA_V2}")
    if screen["screen_status"] != "READY":
        raise BlockedError("screen_status must be READY")
    if screen["method_version"] not in SUPPORTED_QTA_METHODS:
        raise BlockedError(
            "method_version must be qta-1.0.0 or qta-2.0.0"
        )
    if screen["selector_version"] != V2_SELECTOR_VERSION:
        raise BlockedError(f"selector_version must be {V2_SELECTOR_VERSION}")
    validate_sha256(screen["screen_hash"], "screen_hash")
    if screen["screen_hash"] != canonical_screen_hash(screen):
        raise BlockedError("screen_hash does not match canonical screen payload")

    analysis_date = parse_iso_date(screen["analysis_date"], "screen.analysis_date")
    validate_sha256(screen["manifest_hash"], "screen.manifest_hash")
    validate_sha256(screen["selector_hash"], "screen.selector_hash")
    selector = validate_v2_selector(screen["selector"])
    if selector["selector_version"] != screen["selector_version"]:
        raise BlockedError("screen selector versions are inconsistent")
    if screen["selector_hash"] != sha256_json(selector):
        raise BlockedError("selector_hash does not match screen.selector")

    blocked_count = validate_nonnegative_integer(
        screen["blocked_count"], "screen.blocked_count"
    )
    instrument_count = validate_nonnegative_integer(
        screen["instrument_count"], "screen.instrument_count"
    )
    if instrument_count <= 0:
        raise BlockedError("screen.instrument_count must be positive")
    if blocked_count > instrument_count:
        raise BlockedError("screen.blocked_count must not exceed instrument_count")
    blocked_fraction = validate_decimal_string(
        screen["blocked_fraction"],
        "screen.blocked_fraction",
        minimum=Decimal(0),
        maximum=Decimal(1),
    )
    expected_blocked_fraction = Decimal(blocked_count) / Decimal(instrument_count)
    if screen["blocked_fraction"] != format(expected_blocked_fraction, "f"):
        raise BlockedError(
            "screen.blocked_fraction does not match blocked_count/instrument_count"
        )
    if blocked_fraction > Decimal(selector["max_blocked_fraction"]):
        raise BlockedError("READY screen exceeds selector.max_blocked_fraction")

    selected = screen["selected"]
    if not isinstance(selected, dict):
        raise BlockedError("screen.selected must be an object")
    selected_by_key = validate_v2_selected(
        selected,
        analysis_date=analysis_date,
        method_version=screen["method_version"],
    )
    decisions = screen["decisions"]
    if not isinstance(decisions, list):
        raise BlockedError("screen.decisions must be an array")
    if len(decisions) != instrument_count:
        raise BlockedError("screen.decisions length must equal instrument_count")

    statuses = set(selector["eligible_setup_statuses"])
    minimum_score = Decimal(selector["min_total_score"])
    decision_by_key: dict[tuple[str, str], dict[str, Any]] = {}
    eligible_decisions: dict[str, list[dict[str, Any]]] = {
        exchange: [] for exchange in V2_EXCHANGE_CONTRACTS
    }
    computed_blocked_count = 0
    for index, decision in enumerate(decisions):
        label = f"screen.decisions[{index}]"
        if not isinstance(decision, dict):
            raise BlockedError(f"{label} must be an object")
        exact_fields(decision, V2_DECISION_FIELDS, label)
        exchange = decision["exchange"]
        if exchange not in V2_EXCHANGE_CONTRACTS:
            raise BlockedError(f"{label}.exchange is unsupported")
        instrument = decision["instrument"]
        qta = decision["qta"]
        if not isinstance(instrument, dict) or not isinstance(qta, dict):
            raise BlockedError(f"{label}.instrument and qta must be objects")
        validate_v2_instrument(
            instrument,
            exchange,
            f"{label}.instrument",
            analysis_date=analysis_date,
        )
        if decision["market"] != instrument["market"]:
            raise BlockedError(f"{label}.market must equal instrument.market")
        if decision["canonical_symbol"] != instrument["canonical_symbol"]:
            raise BlockedError(
                f"{label}.canonical_symbol must equal instrument.canonical_symbol"
            )
        validate_v2_qta(
            qta,
            instrument,
            f"{label}.qta",
            screen_analysis_date=analysis_date,
            require_ready=False,
        )
        if qta["method_version"] != screen["method_version"]:
            raise BlockedError(
                f"{label}.qta method differs from screen.method_version"
            )
        calculation_ready = qta["calculation_status"] == "READY"
        if not calculation_ready:
            computed_blocked_count += 1
        expected_reasons: list[str] = []
        if not calculation_ready:
            expected_reasons.append("calculation_not_ready")
        else:
            if qta.get("setup_status") not in statuses:
                expected_reasons.append("setup_status_ineligible")
            if (
                decimal_value(qta.get("total_score"), f"{label}.qta.total_score")
                < minimum_score
            ):
                expected_reasons.append("score_below_minimum")
        reasons = decision["reasons"]
        if (
            not isinstance(reasons, list)
            or any(not isinstance(reason, str) for reason in reasons)
            or reasons != expected_reasons
        ):
            raise BlockedError(f"{label}.reasons do not match the selector decision")
        eligible = decision["eligible"]
        selected_flag = decision["selected"]
        if not isinstance(eligible, bool) or not isinstance(selected_flag, bool):
            raise BlockedError(f"{label}.eligible and selected must be boolean")
        if eligible != (not expected_reasons):
            raise BlockedError(f"{label}.eligible does not match reasons")
        exchange_rank = decision["exchange_rank"]
        if eligible:
            if (
                isinstance(exchange_rank, bool)
                or not isinstance(exchange_rank, int)
                or exchange_rank <= 0
            ):
                raise BlockedError(
                    f"{label}.exchange_rank must be positive for an eligible decision"
                )
            eligible_decisions[exchange].append(decision)
        elif exchange_rank is not None:
            raise BlockedError(
                f"{label}.exchange_rank must be null for an ineligible decision"
            )

        key = (exchange, instrument["broker_symbol"])
        if key in decision_by_key:
            raise BlockedError(f"duplicate decision broker symbol: {exchange}:{key[1]}")
        decision_by_key[key] = decision
        selected_item = selected_by_key.get(key)
        if selected_flag != (selected_item is not None):
            raise BlockedError(f"{label}.selected disagrees with screen.selected")
        if selected_item is not None and (
            selected_item["exchange_rank"] != exchange_rank
            or selected_item["instrument"] != instrument
            or selected_item["qta"] != qta
        ):
            raise BlockedError(
                f"{label} does not exactly match its screen.selected item"
            )

    if computed_blocked_count != blocked_count:
        raise BlockedError("screen.blocked_count does not match calculation decisions")
    if set(selected_by_key) - set(decision_by_key):
        raise BlockedError("screen.selected contains an item absent from decisions")
    for exchange in sorted(V2_EXCHANGE_CONTRACTS):
        ranked = sorted(
            eligible_decisions[exchange],
            key=lambda decision: qta_ranking_key(decision["qta"]),
        )
        for expected_rank, decision in enumerate(ranked, start=1):
            if decision["exchange_rank"] != expected_rank:
                raise BlockedError(
                    f"screen.decisions {exchange} exchange_rank does not match "
                    "qta ranking_key"
                )
        minimum_selected = selector["min_selected_by_exchange"][exchange]
        if len(ranked) < minimum_selected:
            raise BlockedError(
                f"READY screen does not meet minimum selection for {exchange}"
            )
        expected_selected_count = min(
            selector["top_k_by_exchange"][exchange],
            len(ranked),
        )
        if len(selected[exchange]) != expected_selected_count:
            raise BlockedError(
                f"screen.selected.{exchange} count does not match selector"
            )
        expected_selected = ranked[:expected_selected_count]
        for selected_item, expected_decision in zip(
            selected[exchange],
            expected_selected,
        ):
            if (
                selected_item["exchange_rank"] != expected_decision["exchange_rank"]
                or selected_item["instrument"] != expected_decision["instrument"]
                or selected_item["qta"] != expected_decision["qta"]
            ):
                raise BlockedError(
                    f"screen.selected.{exchange} order does not match qta ranking_key"
                )


def selected_for_execution(
    screen: dict[str, Any],
    market: str,
) -> list[dict[str, Any]]:
    if screen["schema"] == SCREEN_SCHEMA:
        selected = screen["selected"].get(market)
        if not isinstance(selected, list):
            raise BlockedError(f"screen.selected.{market} must be an array")
        return sorted(selected, key=lambda item: int(item["rank"]))

    candidates: list[dict[str, Any]] = []
    for exchange_order, exchange in enumerate(V2_EXCHANGES_BY_MARKET[market], start=1):
        for selected_item in screen["selected"][exchange]:
            candidates.append(
                {
                    **selected_item,
                    "_exchange": exchange,
                    "_exchange_order": exchange_order,
                }
            )
    candidates.sort(
        key=lambda item: (
            item["_exchange_order"],
            int(item["exchange_rank"]),
            item["instrument"]["broker_symbol"],
        )
    )
    return [
        {**item, "_execution_rank": index}
        for index, item in enumerate(candidates, start=1)
    ]


def round_down_to_tick(value: Decimal, tick: Decimal) -> Decimal:
    if tick <= 0:
        raise BlockedError("tick size must be positive")
    return (value / tick).to_integral_value(rounding=ROUND_DOWN) * tick


def whole_quantity(value: Decimal) -> int:
    return int(value.to_integral_value(rounding=ROUND_DOWN))


def existing_exposure_keys(
    exposure: dict[str, Any],
) -> set[tuple[str, str]]:
    if exposure["schema"] == EXPOSURE_SCHEMA:
        return {
            (position["market"], position["symbol"])
            for position in exposure["positions"]
            if decimal_value(position["market_value_krw"], "market_value_krw") > 0
            or (
                position["quantity"] is not None
                and decimal_value(position["quantity"], "quantity") > 0
            )
        }
    return {
        (position["exchange"], position["symbol"])
        for position in exposure["positions"]
        if decimal_value(position["market_value_krw"], "market_value_krw") > 0
        or (
            position["quantity"] is not None
            and decimal_value(position["quantity"], "quantity") > 0
        )
    }


def account_position_keys(
    account: dict[str, Any],
    screen_schema: str,
) -> set[tuple[str, str]]:
    if screen_schema == SCREEN_SCHEMA:
        return {
            (position["market"], position["symbol"])
            for position in account["positions"]
        }
    return {
        (position["exchange"], position["symbol"]) for position in account["positions"]
    }


def account_open_buy_keys(
    account: dict[str, Any],
    screen_schema: str,
) -> set[tuple[str, str]]:
    open_buys = [
        open_order
        for open_order in account["open_orders"]
        if open_order["side"] == "BUY"
    ]
    if screen_schema == SCREEN_SCHEMA:
        return {
            (open_order["market"], open_order["symbol"]) for open_order in open_buys
        }
    return {(open_order["exchange"], open_order["symbol"]) for open_order in open_buys}


def validate_temporal_snapshots(
    account: dict[str, Any],
    exposure: dict[str, Any],
    execution: dict[str, Any],
) -> None:
    zone = ZoneInfo(execution["timezone"])
    window_start = parse_aware_datetime(
        execution["entry_window_start"], "entry_window_start"
    )
    market_session = execution["market_session"]
    session_date = parse_iso_date(
        market_session["session_date"],
        "market_session.session_date",
    )
    if window_start.astimezone(zone).date() != session_date:
        raise BlockedError(
            "entry_window_start must fall on market_session.session_date"
        )
    account_as_of = parse_aware_datetime(account["as_of"], "account as_of")
    exposure_as_of = parse_aware_datetime(exposure["as_of"], "exposure as_of")
    if account_as_of != exposure_as_of:
        raise BlockedError(
            "account and exposure snapshots must share one frozen as_of instant"
        )
    snapshot_local = account_as_of.astimezone(zone)
    if snapshot_local.date() != session_date:
        raise BlockedError(
            "account and exposure snapshots must be from the entry session local date"
        )
    if account_as_of > window_start:
        raise BlockedError(
            "account and exposure snapshots must be frozen no later than "
            "entry_window_start"
        )
    source_as_of = parse_aware_datetime(
        market_session["source_as_of"],
        "market_session.source_as_of",
    )
    if source_as_of > account_as_of:
        raise BlockedError(
            "market_session.source_as_of must not be after the frozen snapshots"
        )
    snapshot_age_seconds = (window_start - account_as_of).total_seconds()
    if snapshot_age_seconds > execution["snapshot_max_age_seconds"]:
        raise BlockedError("account snapshots exceed snapshot_max_age_seconds")


def validate_v2_analysis_date(
    screen: dict[str, Any],
    execution: dict[str, Any],
) -> None:
    market_session = execution["market_session"]
    analysis_date = parse_iso_date(screen["analysis_date"], "screen.analysis_date")
    previous_session_date = parse_iso_date(
        market_session["previous_session_date"],
        "market_session.previous_session_date",
    )
    if analysis_date != previous_session_date:
        raise BlockedError(
            "qta-screen/v2 analysis_date must equal "
            "market_session.previous_session_date"
        )


def plan_orders(
    screen_raw: dict[str, Any],
    account_raw: dict[str, Any],
    exposure_raw: dict[str, Any],
    risk_raw: dict[str, Any],
    execution_raw: dict[str, Any],
) -> dict[str, Any]:
    validate_screen(screen_raw)
    screen_schema = screen_raw["schema"]
    account = normalized_account_snapshot(
        account_raw,
        screen_schema=screen_schema,
    )
    exposure = normalized_exposure_snapshot(exposure_raw)
    risk = normalized_risk_policy(risk_raw)
    execution = normalized_execution_policy(execution_raw)
    market = execution["market"]
    if account["market"] != market:
        raise BlockedError("account and execution markets differ")
    expected_exposure_schema = (
        EXPOSURE_SCHEMA if screen_schema == SCREEN_SCHEMA else EXPOSURE_SCHEMA_V2
    )
    if exposure["schema"] != expected_exposure_schema:
        raise BlockedError(
            f"{screen_schema} requires exposure schema {expected_exposure_schema}"
        )
    validate_temporal_snapshots(account, exposure, execution)
    if screen_schema == SCREEN_SCHEMA_V2:
        if account["broker"] != "kis":
            raise BlockedError(
                "qta-screen/v2 broker_symbol is KIS-qualified and requires "
                "account broker kis; Toss requires a separate Toss-qualified "
                "universe snapshot contract"
            )
        validate_v2_analysis_date(screen_raw, execution)
    selected = selected_for_execution(screen_raw, market)

    settled_cash = Decimal(account["settled_cash"])
    raw_fx = account["fx_to_krw"]
    if raw_fx is None:
        if settled_cash != 0:
            raise BlockedError("a positive settled cash balance requires fx_to_krw")
        per_trade_risk_native = Decimal(0)
        max_notional_native = Decimal(0)
    else:
        fx = Decimal(raw_fx)
        per_trade_risk_native = Decimal(risk["per_trade_risk_krw"]) / fx
        max_notional_native = Decimal(risk["max_symbol_notional_krw"]) / fx
    cost_bps = Decimal(risk["round_trip_cost_bps"])
    cash_buffer_bps = Decimal(risk["cash_buffer_bps"])
    gap_bps = Decimal(execution["max_gap_bps"])
    position_keys = existing_exposure_keys(exposure)
    position_keys.update(account_position_keys(account, screen_schema))
    open_buy_keys = account_open_buy_keys(account, screen_schema)
    occupied_keys = position_keys | open_buy_keys

    context = {
        "execution_version": EXECUTION_VERSION,
        "screen_hash": canonical_screen_hash(screen_raw),
        "account_hash": sha256_json(account),
        "exposure_hash": sha256_json(exposure),
        "risk_hash": sha256_json(risk),
        "execution_policy_hash": sha256_json(execution),
        "execution_policy": execution,
        "market_session": execution["market_session"],
        "market_session_hash": execution["market_session"]["session_hash"],
        "snapshot_max_age_seconds": execution["snapshot_max_age_seconds"],
        "broker": account["broker"],
        "environment": account["environment"],
        "account_alias": account["account_alias"],
        "market": market,
    }
    if screen_schema == SCREEN_SCHEMA_V2:
        context.update(
            {
                "screen_schema": SCREEN_SCHEMA_V2,
                "candidate_order_contract": (
                    "exchange_contract_order_then_exchange_rank_then_broker_symbol"
                ),
                "exchange_order": list(V2_EXCHANGES_BY_MARKET[market]),
                "broker_symbol_qualification": "KIS",
                "analysis_date": screen_raw["analysis_date"],
                "snapshot_as_of": account["as_of"],
            }
        )
    plan_seed = sha256_json(context)
    intents: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    planned_keys: set[tuple[str, str]] = set()
    cash_remaining = settled_cash

    for selected_item in selected:
        is_v2 = screen_schema == SCREEN_SCHEMA_V2
        qta = selected_item.get("qta")
        instrument = selected_item.get("instrument")
        if not isinstance(qta, dict) or not isinstance(instrument, dict):
            raise BlockedError("selected item must contain qta and instrument objects")
        exchange = selected_item["_exchange"] if is_v2 else None
        rank = (
            int(selected_item["_execution_rank"])
            if is_v2
            else int(selected_item["rank"])
        )
        symbol = normalize_symbol(
            market,
            str(instrument["broker_symbol"] if is_v2 else qta["ticker"]),
        )
        if (
            qta.get("source_skill") != "quant-stock-technical"
            or qta.get("result_schema") != "quant-stock-technical/v1"
            or qta.get("calculation_status") != "READY"
            or qta.get("method_version") != screen_raw["method_version"]
        ):
            raise BlockedError("selected QTA payload contract is invalid")
        if is_v2:
            if qta.get("setup_status") not in set(
                screen_raw["selector"]["eligible_setup_statuses"]
            ):
                raise BlockedError(
                    "selected QTA setup_status is not allowed by the v2 selector"
                )
            if qta.get("market") != market:
                raise BlockedError("selected QTA market differs from execution market")
            if qta.get("ticker") != instrument["canonical_symbol"]:
                raise BlockedError("selected QTA ticker differs from canonical_symbol")
            key = (str(exchange), symbol)
        else:
            if qta.get("setup_status") != "READY":
                raise BlockedError("legacy selected QTA setup_status must be READY")
            symbol = normalize_symbol(market, str(qta["ticker"]))
            key = (market, symbol)
        if key in planned_keys:
            skipped_item = {
                "market": market,
                "symbol": symbol,
                "reason": "duplicate_planned_exposure",
            }
            if is_v2:
                skipped_item.update(
                    {
                        "exchange": exchange,
                        "exchange_rank": int(selected_item["exchange_rank"]),
                        "canonical_symbol": instrument["canonical_symbol"],
                    }
                )
            skipped.append(skipped_item)
            continue
        if key in open_buy_keys:
            skipped_item = {
                "market": market,
                "symbol": symbol,
                "reason": "existing_open_buy_order",
            }
            if is_v2:
                skipped_item.update(
                    {
                        "exchange": exchange,
                        "exchange_rank": int(selected_item["exchange_rank"]),
                        "canonical_symbol": instrument["canonical_symbol"],
                    }
                )
            skipped.append(skipped_item)
            continue
        if key in position_keys and not risk["allow_existing_additions"]:
            skipped_item = {
                "market": market,
                "symbol": symbol,
                "reason": "existing_exposure",
            }
            if is_v2:
                skipped_item.update(
                    {
                        "exchange": exchange,
                        "exchange_rank": int(selected_item["exchange_rank"]),
                        "canonical_symbol": instrument["canonical_symbol"],
                    }
                )
            skipped.append(skipped_item)
            continue
        if (
            key not in occupied_keys
            and len(occupied_keys | planned_keys) >= risk["max_concurrent_positions"]
        ):
            skipped_item = {
                "market": market,
                "symbol": symbol,
                "reason": "max_concurrent_positions",
            }
            if is_v2:
                skipped_item.update(
                    {
                        "exchange": exchange,
                        "exchange_rank": int(selected_item["exchange_rank"]),
                        "canonical_symbol": instrument["canonical_symbol"],
                    }
                )
            skipped.append(skipped_item)
            continue
        entry = positive_decimal(qta["entry_price"], f"{symbol}.entry_price")
        stop = positive_decimal(qta["stop_price"], f"{symbol}.stop_price")
        if stop >= entry:
            raise BlockedError(f"{symbol} stop must be below entry")
        tick_value = (
            instrument["tick_contract"]["resolved_tick_size"]
            if is_v2
            else instrument["tick_size"]
        )
        tick = positive_decimal(tick_value, f"{symbol}.tick_size")
        limit_price = round_down_to_tick(
            entry * (Decimal(1) + gap_bps / Decimal(10000)), tick
        )
        limit_price = max(limit_price, entry)
        effective_loss = limit_price - stop + limit_price * cost_bps / Decimal(10000)
        if effective_loss <= 0:
            raise BlockedError(f"{symbol} effective loss per share is non-positive")
        risk_quantity = whole_quantity(per_trade_risk_native / effective_loss)
        notional_quantity = whole_quantity(max_notional_native / limit_price)
        worst_case_unit = limit_price * (Decimal(1) + cash_buffer_bps / Decimal(10000))
        cash_quantity = whole_quantity(cash_remaining / worst_case_unit)
        quantity = min(risk_quantity, notional_quantity, cash_quantity)
        if quantity <= 0:
            skipped.append(
                {"market": market, "symbol": symbol, "reason": "quantity_below_one"}
            )
            continue
        reserved_cash = worst_case_unit * Decimal(quantity)
        intent_seed = {
            "plan_seed": plan_seed,
            "rank": rank,
            "market": market,
            "symbol": symbol,
            "side": "BUY",
            "order_type": "LIMIT",
            "time_in_force": "DAY",
            "quantity": str(quantity),
            "limit_price": format(limit_price, "f"),
        }
        if is_v2:
            intent_seed.update(
                {
                    "exchange": exchange,
                    "exchange_rank": int(selected_item["exchange_rank"]),
                    "canonical_symbol": instrument["canonical_symbol"],
                    "data_symbol": instrument["data_symbol"],
                    "broker_symbol": instrument["broker_symbol"],
                    "venue": instrument["venue"],
                }
            )
        intent_id = hashlib.sha256(
            canonical_json(intent_seed).encode("utf-8")
        ).hexdigest()[:32]
        client_order_id = f"qta-{intent_id[:28]}"
        intent = {
            "intent_id": intent_id,
            "client_order_id": client_order_id,
            "rank": rank,
            "market": market,
            "symbol": symbol,
            "currency": account["currency"],
            "side": "BUY",
            "order_type": "LIMIT",
            "time_in_force": "DAY",
            "quantity": str(quantity),
            "entry_trigger": format(entry, "f"),
            "limit_price": format(limit_price, "f"),
            "stop_price": format(stop, "f"),
            "take_profit_price": format(
                positive_decimal(
                    qta["take_profit_price"], f"{symbol}.take_profit_price"
                ),
                "f",
            ),
            "effective_loss_per_share": format(effective_loss, "f"),
            "reserved_cash": format(reserved_cash, "f"),
            "qta_payload_hash": sha256_json(qta),
            "initial_state": "PLANNED",
        }
        if is_v2:
            intent.update(
                {
                    "exchange": exchange,
                    "exchange_rank": int(selected_item["exchange_rank"]),
                    "canonical_symbol": instrument["canonical_symbol"],
                    "data_symbol": instrument["data_symbol"],
                    "broker_symbol": instrument["broker_symbol"],
                    "instrument_type": instrument["instrument_type"],
                    "benchmark_id": instrument["benchmark_id"],
                    "venue": instrument["venue"],
                    "resolved_tick_size": format(tick, "f"),
                    "tick_contract_hash": sha256_json(instrument["tick_contract"]),
                }
            )
        intent["intent_hash"] = sha256_json(intent)
        intents.append(intent)
        planned_keys.add(key)
        cash_remaining -= reserved_cash

    output = {
        "schema": PLAN_SCHEMA,
        "plan_status": "READY" if intents else "NO_ORDERS",
        "execution_version": EXECUTION_VERSION,
        "context": context,
        "entry_window": {
            "timezone": execution["timezone"],
            "start": execution["entry_window_start"],
            "end": execution["entry_window_end"],
            "poll_interval_seconds": execution["poll_interval_seconds"],
        },
        "quote_policy": {
            "max_age_seconds": execution["quote_max_age_seconds"],
            "max_spread_bps": execution["max_spread_bps"],
            "max_gap_bps": execution["max_gap_bps"],
            "trigger_mode": execution["trigger_mode"],
        },
        "order_policy": {
            "ttl_seconds": execution["order_ttl_seconds"],
            "allow_partial_fill": execution["allow_partial_fill"],
            "cancel_remainder_at_window_end": execution[
                "cancel_remainder_at_window_end"
            ],
        },
        "settled_cash_start": format(settled_cash, "f"),
        "borrowed_buying_power_excluded": account["borrowed_buying_power"],
        "settled_cash_unreserved": format(cash_remaining, "f"),
        "intents": intents,
        "skipped": skipped,
        "frozen_inputs": {
            "screen": screen_raw,
            "account": account,
            "exposure": exposure,
            "risk": risk,
            "execution": execution,
        },
    }
    output["plan_hash"] = sha256_json(output)
    return output


@dataclass(frozen=True)
class IntentRecord:
    intent_id: str
    state: str
    broker_order_id: str | None
    request_hash: str | None
    payload: dict[str, Any]


class Ledger:
    """SQLite ledger that persists intent state before broker mutation."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(str(self.path))
        self.connection.execute("PRAGMA journal_mode=WAL")
        self.connection.execute("PRAGMA synchronous=FULL")
        self.connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS intents (
                intent_id TEXT PRIMARY KEY,
                plan_hash TEXT NOT NULL,
                state TEXT NOT NULL,
                broker_order_id TEXT,
                request_hash TEXT,
                payload_json TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS events (
                sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                intent_id TEXT NOT NULL,
                event_type TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            """
        )
        self.connection.commit()

    def close(self) -> None:
        self.connection.close()

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            yield self.connection
        except BaseException:
            self.connection.rollback()
            raise
        else:
            self.connection.commit()

    def create_intent(self, plan_hash: str, intent: dict[str, Any]) -> None:
        intent_id = str(intent["intent_id"])
        now = datetime.now(timezone.utc).isoformat()
        payload_json = canonical_json(intent)
        with self.transaction() as connection:
            existing = connection.execute(
                "SELECT plan_hash, payload_json FROM intents WHERE intent_id = ?",
                (intent_id,),
            ).fetchone()
            if existing:
                if existing != (plan_hash, payload_json):
                    raise BlockedError(f"intent collision: {intent_id}")
                return
            connection.execute(
                """
                INSERT INTO intents
                    (intent_id, plan_hash, state, payload_json, updated_at)
                VALUES (?, ?, 'PLANNED', ?, ?)
                """,
                (intent_id, plan_hash, payload_json, now),
            )
            connection.execute(
                """
                INSERT INTO events (intent_id, event_type, payload_json, created_at)
                VALUES (?, 'INTENT_CREATED', ?, ?)
                """,
                (intent_id, canonical_json({"plan_hash": plan_hash}), now),
            )

    def get(self, intent_id: str) -> IntentRecord:
        row = self.connection.execute(
            """
            SELECT intent_id, state, broker_order_id, request_hash, payload_json
            FROM intents WHERE intent_id = ?
            """,
            (intent_id,),
        ).fetchone()
        if row is None:
            raise BlockedError(f"unknown intent: {intent_id}")
        return IntentRecord(
            intent_id=row[0],
            state=row[1],
            broker_order_id=row[2],
            request_hash=row[3],
            payload=json.loads(row[4]),
        )

    def transition(
        self,
        intent_id: str,
        new_state: str,
        event_payload: dict[str, Any],
        *,
        broker_order_id: str | None = None,
        request_hash: str | None = None,
    ) -> None:
        current = self.get(intent_id)
        allowed = TRANSITIONS.get(current.state, set())
        if new_state not in allowed:
            raise BlockedError(
                f"illegal intent transition: {current.state} -> {new_state}"
            )
        now = datetime.now(timezone.utc).isoformat()
        next_broker_id = broker_order_id or current.broker_order_id
        next_request_hash = request_hash or current.request_hash
        with self.transaction() as connection:
            changed = connection.execute(
                """
                UPDATE intents
                SET state = ?, broker_order_id = ?, request_hash = ?, updated_at = ?
                WHERE intent_id = ? AND state = ?
                """,
                (
                    new_state,
                    next_broker_id,
                    next_request_hash,
                    now,
                    intent_id,
                    current.state,
                ),
            ).rowcount
            if changed != 1:
                raise BlockedError(f"concurrent intent mutation: {intent_id}")
            connection.execute(
                """
                INSERT INTO events (intent_id, event_type, payload_json, created_at)
                VALUES (?, ?, ?, ?)
                """,
                (
                    intent_id,
                    f"STATE_{new_state}",
                    canonical_json(event_payload),
                    now,
                ),
            )

    def append_event(
        self, intent_id: str, event_type: str, event_payload: dict[str, Any]
    ) -> None:
        self.append_events([(intent_id, event_type, event_payload)])

    def append_events(
        self,
        events: list[tuple[str, str, dict[str, Any]]],
    ) -> None:
        """Persist non-mutation telemetry in one FULL-synchronous transaction."""
        if not events:
            return
        for intent_id, event_type, event_payload in events:
            self.get(intent_id)
            if not isinstance(event_type, str) or not event_type:
                raise BlockedError("event_type must be a non-empty string")
            if not isinstance(event_payload, dict):
                raise BlockedError("event payload must be an object")
        now = datetime.now(timezone.utc).isoformat()
        with self.transaction() as connection:
            connection.executemany(
                """
                INSERT INTO events (intent_id, event_type, payload_json, created_at)
                VALUES (?, ?, ?, ?)
                """,
                [
                    (
                        intent_id,
                        event_type,
                        canonical_json(event_payload),
                        now,
                    )
                    for intent_id, event_type, event_payload in events
                ],
            )

    def nonterminal(self) -> list[IntentRecord]:
        rows = self.connection.execute(
            """
            SELECT intent_id, state, broker_order_id, request_hash, payload_json
            FROM intents ORDER BY intent_id
            """
        ).fetchall()
        return [
            IntentRecord(row[0], row[1], row[2], row[3], json.loads(row[4]))
            for row in rows
            if row[1] not in TERMINAL_STATES
        ]

    def nonterminal_bindings(self) -> list[dict[str, str]]:
        rows = self.connection.execute(
            """
            SELECT intent_id, plan_hash, state
            FROM intents ORDER BY intent_id
            """
        ).fetchall()
        return [
            {
                "intent_id": str(row[0]),
                "plan_hash": str(row[1]),
                "state": str(row[2]),
            }
            for row in rows
            if row[2] not in TERMINAL_STATES or row[2] == "MANUAL_BLOCK"
        ]

    def events(self, intent_id: str) -> list[dict[str, Any]]:
        rows = self.connection.execute(
            """
            SELECT sequence, event_type, payload_json, created_at
            FROM events WHERE intent_id = ? ORDER BY sequence
            """,
            (intent_id,),
        ).fetchall()
        return [
            {
                "sequence": row[0],
                "event_type": row[1],
                "payload": json.loads(row[2]),
                "created_at": row[3],
            }
            for row in rows
        ]


class SingleWriterLock:
    """Non-blocking process lock for one account/session state directory."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.handle: Any = None

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.handle = self.path.open("a+", encoding="utf-8")
        try:
            fcntl.flock(self.handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            self.handle.close()
            self.handle = None
            raise BlockedError(f"another writer holds {self.path}") from exc
        return self

    def __exit__(
        self,
        exc_type: object,
        exc: object,
        traceback: object,
    ) -> None:
        if self.handle is not None:
            fcntl.flock(self.handle.fileno(), fcntl.LOCK_UN)
            self.handle.close()
            self.handle = None


def redact(value: Any) -> Any:
    secret_fragments = (
        "secret",
        "token",
        "authorization",
        "appkey",
        "app_key",
        "password",
        "account_number",
        "accountnumber",
        "accountno",
        "account_prefix",
        "accountprefix",
        "account_product",
        "accountproduct",
        "account_seq",
        "accountseq",
        "cano",
        "acnt_no",
        "acnt_prdt_cd",
        "x-tossinvest-account",
    )
    if isinstance(value, dict):
        result: dict[str, Any] = {}
        for key, item in value.items():
            lowered = key.lower()
            result[key] = (
                "[REDACTED]"
                if any(fragment in lowered for fragment in secret_fragments)
                else redact(item)
            )
        return result
    if isinstance(value, list):
        return [redact(item) for item in value]
    return value


def self_test() -> None:
    qta_payload = {
        "source_skill": "quant-stock-technical",
        "result_schema": "quant-stock-technical/v1",
        "calculation_status": "READY",
        "setup_status": "READY",
        "method_version": "qta-1.0.0",
        "ticker": "NEW",
        "entry_price": 100,
        "stop_price": 90,
        "take_profit_price": 120,
    }
    screen = {
        "schema": SCREEN_SCHEMA,
        "screen_status": "READY",
        "method_version": "qta-1.0.0",
        "selector_version": "qta-screen-1.0.0",
        "selected": {
            "KR": [],
            "US": [
                {
                    "rank": 1,
                    "instrument": {
                        "market": "US",
                        "ticker": "NEW",
                        "tick_size": "0.01",
                    },
                    "qta": qta_payload,
                }
            ],
        },
    }
    screen["screen_hash"] = canonical_screen_hash(screen)
    account = {
        "schema": ACCOUNT_SCHEMA,
        "broker": "kis",
        "environment": "paper",
        "account_alias": "paper-us",
        "market": "US",
        "currency": "USD",
        "as_of": "2026-07-27T09:00:00-04:00",
        "settled_cash": "200",
        "borrowed_buying_power": "5000",
        "fx_to_krw": "1400",
        "positions": [],
        "open_orders": [],
    }
    exposure = {
        "schema": EXPOSURE_SCHEMA,
        "as_of": "2026-07-27T09:00:00-04:00",
        "positions": [],
    }
    risk = {
        "schema": RISK_SCHEMA,
        "base_currency": "KRW",
        "per_trade_risk_krw": "15000",
        "max_symbol_notional_krw": "280000",
        "max_concurrent_positions": 1,
        "max_daily_loss_krw": "28000",
        "round_trip_cost_bps": "10",
        "cash_buffer_bps": "100",
        "allow_existing_additions": False,
        "allow_borrowed_cash": False,
        "allow_margin": False,
        "allow_short": False,
        "allow_auto_fx": False,
        "whole_shares_only": True,
    }
    execution = {
        "schema": EXECUTION_POLICY_SCHEMA,
        "market": "US",
        "timezone": "America/New_York",
        "entry_window_start": "2026-07-27T09:30:00-04:00",
        "entry_window_end": "2026-07-27T10:30:00-04:00",
        "market_session": market_session_from_source(
            Path(__file__).resolve().parents[1]
            / "references"
            / "fixtures"
            / "us-market-session-2026-07-27.json"
        ),
        "snapshot_max_age_seconds": 1800,
        "poll_interval_seconds": 2,
        "quote_max_age_seconds": 5,
        "max_spread_bps": "25",
        "max_gap_bps": "20",
        "trigger_mode": "AT_OR_ABOVE",
        "order_ttl_seconds": 30,
        "order_type": "LIMIT",
        "time_in_force": "DAY",
        "allow_partial_fill": True,
        "cancel_remainder_at_window_end": True,
    }
    first = plan_orders(screen, account, exposure, risk, execution)
    second = plan_orders(screen, account, exposure, risk, execution)
    assert first == second
    assert first["plan_status"] == "READY"
    assert first["borrowed_buying_power_excluded"] == "5000"
    assert len(first["intents"]) == 1
    assert first["intents"][0]["quantity"] == "1"

    empty_account = {
        **account,
        "settled_cash": "0",
        "fx_to_krw": None,
    }
    empty_plan = plan_orders(screen, empty_account, exposure, risk, execution)
    assert empty_plan["plan_status"] == "NO_ORDERS"
    assert empty_plan["frozen_inputs"]["account"]["fx_to_krw"] is None
    assert empty_plan["skipped"][0]["reason"] == "quantity_below_one"

    held = json.loads(canonical_json(exposure))
    held["positions"] = [
        {
            "broker": "toss",
            "market": "US",
            "symbol": "NEW",
            "quantity": "0.5",
            "market_value_krw": "70000",
        }
    ]
    no_orders = plan_orders(screen, account, held, risk, execution)
    assert no_orders["plan_status"] == "NO_ORDERS"
    assert no_orders["skipped"][0]["reason"] == "existing_exposure"

    with tempfile.TemporaryDirectory(prefix="qta-ledger-") as directory:
        ledger = Ledger(Path(directory) / "ledger.sqlite3")
        intent = first["intents"][0]
        ledger.create_intent(first["plan_hash"], intent)
        ledger.create_intent(first["plan_hash"], intent)
        ledger.transition(intent["intent_id"], "WAIT_TRIGGER", {})
        ledger.transition(intent["intent_id"], "RESERVED", {})
        ledger.transition(
            intent["intent_id"],
            "SUBMITTING",
            {"request_hash": "b" * 64},
            request_hash="b" * 64,
        )
        ledger.transition(intent["intent_id"], "UNKNOWN", {"reason": "timeout"})
        ledger.transition(intent["intent_id"], "RECONCILING", {})
        ledger.transition(intent["intent_id"], "MANUAL_BLOCK", {"reason": "ambiguous"})
        assert ledger.get(intent["intent_id"]).state == "MANUAL_BLOCK"
        assert len(ledger.events(intent["intent_id"])) == 7
        ledger.close()

    redacted = redact(
        {
            "authorization": "Bearer secret",
            "nested": {"appsecret": "secret", "safe": "ok"},
        }
    )
    assert redacted["authorization"] == "[REDACTED]"
    assert redacted["nested"]["appsecret"] == "[REDACTED]"
    assert redacted["nested"]["safe"] == "ok"
    print(
        canonical_json(
            {
                "self_test": "PASS",
                "execution_version": EXECUTION_VERSION,
                "plan_hash": first["plan_hash"],
            }
        )
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--self-test", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.self_test:
        self_test()
        return 0
    emit_json(
        {
            "schema": PLAN_SCHEMA,
            "plan_status": "BLOCKED",
            "reason": "use plan_orders.py or pass --self-test",
        }
    )
    return 2


if __name__ == "__main__":
    sys.exit(main())

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
from datetime import datetime, timezone
from decimal import ROUND_DOWN, Decimal, InvalidOperation
from pathlib import Path
from typing import Any

EXECUTION_VERSION = "open1h-exec-1.0.0"
PLAN_SCHEMA = "qta-order-plan/v1"
RISK_SCHEMA = "qta-risk-policy/v1"
EXECUTION_POLICY_SCHEMA = "qta-execution-policy/v1"
ACCOUNT_SCHEMA = "qta-account-snapshot/v1"
EXPOSURE_SCHEMA = "qta-exposure-snapshot/v1"
SCREEN_SCHEMA = "qta-screen/v1"
MARKET_CURRENCY = {"KR": "KRW", "US": "USD"}

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
        "PARTIALLY_FILLED",
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
        if not normalized.isdigit() or len(normalized) > 6:
            raise BlockedError(f"invalid KR symbol: {symbol!r}")
        return normalized.zfill(6)
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


def normalized_execution_policy(value: dict[str, Any]) -> dict[str, Any]:
    required = {
        "schema",
        "market",
        "timezone",
        "entry_window_start",
        "entry_window_end",
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
    expected_timezone = "Asia/Seoul" if market == "KR" else "America/New_York"
    if value["timezone"] != expected_timezone:
        raise BlockedError(f"{market} timezone must be {expected_timezone}")
    try:
        start = datetime.fromisoformat(str(value["entry_window_start"]))
        end = datetime.fromisoformat(str(value["entry_window_end"]))
    except ValueError as exc:
        raise BlockedError("entry window values must be ISO-8601 datetimes") from exc
    if start.tzinfo is None or end.tzinfo is None or start >= end:
        raise BlockedError("entry window must be timezone-aware and start before end")
    if (end - start).total_seconds() != 3600:
        raise BlockedError("entry window must be exactly one hour")
    for field in (
        "poll_interval_seconds",
        "quote_max_age_seconds",
        "order_ttl_seconds",
    ):
        amount = value[field]
        if isinstance(amount, bool) or not isinstance(amount, int) or amount <= 0:
            raise BlockedError(f"{field} must be a positive integer")
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
        "entry_window_start": start.isoformat(),
        "entry_window_end": end.isoformat(),
        "max_spread_bps": format(
            nonnegative_decimal(value["max_spread_bps"], "max_spread_bps"), "f"
        ),
        "max_gap_bps": format(
            nonnegative_decimal(value["max_gap_bps"], "max_gap_bps"), "f"
        ),
    }


def normalized_account_snapshot(value: dict[str, Any]) -> dict[str, Any]:
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
    if not value["broker"] or not value["account_alias"]:
        raise BlockedError("broker and account_alias are required")
    try:
        datetime.fromisoformat(str(value["as_of"]).replace("Z", "+00:00"))
    except ValueError as exc:
        raise BlockedError("account as_of must be ISO-8601") from exc
    settled = nonnegative_decimal(value["settled_cash"], "settled_cash")
    borrowed = nonnegative_decimal(
        value["borrowed_buying_power"], "borrowed_buying_power"
    )
    fx = positive_decimal(value["fx_to_krw"], "fx_to_krw")
    if not isinstance(value["positions"], list) or not isinstance(
        value["open_orders"], list
    ):
        raise BlockedError("positions and open_orders must be arrays")
    return {
        **value,
        "market": market,
        "settled_cash": format(settled, "f"),
        "borrowed_buying_power": format(borrowed, "f"),
        "fx_to_krw": format(fx, "f"),
    }


def normalized_exposure_snapshot(value: dict[str, Any]) -> dict[str, Any]:
    required = {"schema", "as_of", "positions"}
    exact_fields(value, required, "exposure snapshot")
    if value["schema"] != EXPOSURE_SCHEMA:
        raise BlockedError(f"unsupported exposure snapshot schema: {value['schema']!r}")
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
    for index, position in enumerate(value["positions"]):
        if not isinstance(position, dict):
            raise BlockedError(f"positions[{index}] must be an object")
        exact_fields(position, required_position, f"positions[{index}]")
        market = str(position["market"]).upper()
        symbol = normalize_symbol(market, str(position["symbol"]))
        if position["quantity"] is not None:
            nonnegative_decimal(position["quantity"], f"positions[{index}].quantity")
        nonnegative_decimal(
            position["market_value_krw"],
            f"positions[{index}].market_value_krw",
        )
        positions.append({**position, "market": market, "symbol": symbol})
    positions.sort(key=lambda item: (item["market"], item["symbol"], item["broker"]))
    return {**value, "positions": positions}


def validate_screen(screen: dict[str, Any]) -> None:
    if screen.get("schema") != SCREEN_SCHEMA:
        raise BlockedError(f"screen schema must be {SCREEN_SCHEMA}")
    if screen.get("screen_status") != "READY":
        raise BlockedError("screen_status must be READY")
    if screen.get("method_version") != "qta-1.0.0":
        raise BlockedError("method_version must be qta-1.0.0")
    if screen.get("selector_version") != "qta-screen-1.0.0":
        raise BlockedError("selector_version must be qta-screen-1.0.0")
    if not isinstance(screen.get("selected"), dict):
        raise BlockedError("screen.selected must be an object")


def round_down_to_tick(value: Decimal, tick: Decimal) -> Decimal:
    if tick <= 0:
        raise BlockedError("tick size must be positive")
    return (value / tick).to_integral_value(rounding=ROUND_DOWN) * tick


def whole_quantity(value: Decimal) -> int:
    return int(value.to_integral_value(rounding=ROUND_DOWN))


def existing_exposure_keys(exposure: dict[str, Any]) -> set[tuple[str, str]]:
    return {
        (position["market"], position["symbol"])
        for position in exposure["positions"]
        if decimal_value(position["market_value_krw"], "market_value_krw") > 0
        or (
            position["quantity"] is not None
            and decimal_value(position["quantity"], "quantity") > 0
        )
    }


def plan_orders(
    screen_raw: dict[str, Any],
    account_raw: dict[str, Any],
    exposure_raw: dict[str, Any],
    risk_raw: dict[str, Any],
    execution_raw: dict[str, Any],
) -> dict[str, Any]:
    validate_screen(screen_raw)
    account = normalized_account_snapshot(account_raw)
    exposure = normalized_exposure_snapshot(exposure_raw)
    risk = normalized_risk_policy(risk_raw)
    execution = normalized_execution_policy(execution_raw)
    market = execution["market"]
    if account["market"] != market:
        raise BlockedError("account and execution markets differ")
    selected = screen_raw["selected"].get(market)
    if not isinstance(selected, list):
        raise BlockedError(f"screen.selected.{market} must be an array")

    settled_cash = Decimal(account["settled_cash"])
    fx = Decimal(account["fx_to_krw"])
    per_trade_risk_native = Decimal(risk["per_trade_risk_krw"]) / fx
    max_notional_native = Decimal(risk["max_symbol_notional_krw"]) / fx
    cost_bps = Decimal(risk["round_trip_cost_bps"])
    cash_buffer_bps = Decimal(risk["cash_buffer_bps"])
    gap_bps = Decimal(execution["max_gap_bps"])
    exposure_keys = existing_exposure_keys(exposure)

    account_positions = {
        (
            str(item.get("market", market)).upper(),
            normalize_symbol(
                str(item.get("market", market)).upper(), str(item["symbol"])
            ),
        )
        for item in account["positions"]
        if isinstance(item, dict) and "symbol" in item
    }
    exposure_keys.update(account_positions)

    context = {
        "execution_version": EXECUTION_VERSION,
        "screen_hash": screen_raw.get("screen_hash") or sha256_json(screen_raw),
        "account_hash": sha256_json(account),
        "exposure_hash": sha256_json(exposure),
        "risk_hash": sha256_json(risk),
        "execution_policy_hash": sha256_json(execution),
        "broker": account["broker"],
        "environment": account["environment"],
        "account_alias": account["account_alias"],
        "market": market,
    }
    plan_seed = sha256_json(context)
    intents: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    cash_remaining = settled_cash

    for selected_item in sorted(selected, key=lambda item: int(item["rank"])):
        if len(intents) >= risk["max_concurrent_positions"]:
            skipped.append(
                {
                    "market": market,
                    "symbol": selected_item["qta"]["ticker"],
                    "reason": "max_concurrent_positions",
                }
            )
            continue
        qta = selected_item.get("qta")
        instrument = selected_item.get("instrument")
        if not isinstance(qta, dict) or not isinstance(instrument, dict):
            raise BlockedError("selected item must contain qta and instrument objects")
        if (
            qta.get("source_skill") != "quant-stock-technical"
            or qta.get("result_schema") != "quant-stock-technical/v1"
            or qta.get("calculation_status") != "READY"
            or qta.get("setup_status") != "READY"
            or qta.get("method_version") != "qta-1.0.0"
        ):
            raise BlockedError("selected QTA payload contract is invalid")
        symbol = normalize_symbol(market, str(qta["ticker"]))
        key = (market, symbol)
        if key in exposure_keys and not risk["allow_existing_additions"]:
            skipped.append(
                {"market": market, "symbol": symbol, "reason": "existing_exposure"}
            )
            continue
        entry = positive_decimal(qta["entry_price"], f"{symbol}.entry_price")
        stop = positive_decimal(qta["stop_price"], f"{symbol}.stop_price")
        if stop >= entry:
            raise BlockedError(f"{symbol} stop must be below entry")
        tick = positive_decimal(instrument["tick_size"], f"{symbol}.tick_size")
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
            "rank": int(selected_item["rank"]),
            "market": market,
            "symbol": symbol,
            "side": "BUY",
            "order_type": "LIMIT",
            "time_in_force": "DAY",
            "quantity": str(quantity),
            "limit_price": format(limit_price, "f"),
        }
        intent_id = hashlib.sha256(
            canonical_json(intent_seed).encode("utf-8")
        ).hexdigest()[:32]
        client_order_id = f"qta-{intent_id[:28]}"
        intent = {
            "intent_id": intent_id,
            "client_order_id": client_order_id,
            "rank": int(selected_item["rank"]),
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
        intent["intent_hash"] = sha256_json(intent)
        intents.append(intent)
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
        self.get(intent_id)
        now = datetime.now(timezone.utc).isoformat()
        with self.transaction() as connection:
            connection.execute(
                """
                INSERT INTO events (intent_id, event_type, payload_json, created_at)
                VALUES (?, ?, ?, ?)
                """,
                (intent_id, event_type, canonical_json(event_payload), now),
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
        "cano",
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
        "screen_hash": "a" * 64,
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
    account = {
        "schema": ACCOUNT_SCHEMA,
        "broker": "kis",
        "environment": "paper",
        "account_alias": "paper-us",
        "market": "US",
        "currency": "USD",
        "as_of": "2026-07-26T09:00:00+09:00",
        "settled_cash": "200",
        "borrowed_buying_power": "5000",
        "fx_to_krw": "1400",
        "positions": [],
        "open_orders": [],
    }
    exposure = {
        "schema": EXPOSURE_SCHEMA,
        "as_of": "2026-07-26T09:00:00+09:00",
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

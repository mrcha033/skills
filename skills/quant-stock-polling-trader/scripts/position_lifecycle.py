#!/usr/bin/env python3
"""Deterministic strategy-position lifecycle and exit decisions."""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import tempfile
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

from execution_core import (
    SCREEN_SCHEMA_V2,
    BlockedError,
    canonical_json,
    decimal_value,
    exact_fields,
    normalize_symbol,
    normalized_account_snapshot,
    parse_aware_datetime,
    positive_decimal,
    round_down_to_tick,
    sha256_json,
)

POSITION_POLICY_SCHEMA = "qta-position-policy/v1"
STRATEGY_POSITION_SCHEMA = "qta-strategy-position/v1"
EXIT_DECISION_SCHEMA = "qta-exit-decision/v1"
POSITION_RECONCILIATION_SCHEMA = "qta-position-reconciliation/v1"
POSITION_POLICY_FIELDS = {
    "schema",
    "close_liquidation_seconds_before_close",
    "exit_order_ttl_seconds",
    "max_exit_replacements",
    "quote_max_age_seconds",
    "partial_entry_action",
    "gap_down_action",
    "daily_loss_action",
    "overnight_residual_action",
}
STRATEGY_POSITION_FIELDS = {
    "schema",
    "position_id",
    "plan_hash",
    "entry_intent_id",
    "market",
    "exchange",
    "symbol",
    "venue",
    "currency",
    "entry_session_date",
    "last_managed_session_date",
    "planned_quantity",
    "acquired_quantity",
    "exited_quantity",
    "average_entry_price",
    "average_exit_price",
    "stop_price",
    "take_profit_price",
    "tick_size",
    "fx_to_krw",
    "daily_baseline_session_date",
    "daily_baseline_price",
    "realized_pnl_native",
    "daily_realized_pnl_native",
    "status",
}
POSITION_STATUSES = {
    "PENDING_ENTRY",
    "OPEN",
    "EXIT_PENDING",
    "EXIT_PARTIAL",
    "CARRY_EXIT_ONLY",
    "CLOSED",
    "MANUAL_BLOCK",
}
MARKET_EXCHANGES = {
    "KR": {"KOSPI", "KOSDAQ"},
    "US": {"NYSE", "NASDAQ"},
}
MARKET_CURRENCY = {"KR": "KRW", "US": "USD"}


def require_iso_date(value: Any, field: str) -> str:
    if not isinstance(value, str):
        raise BlockedError(f"{field} must be YYYY-MM-DD")
    try:
        parsed = date.fromisoformat(value)
    except ValueError as exc:
        raise BlockedError(f"{field} must be YYYY-MM-DD") from exc
    if parsed.isoformat() != value:
        raise BlockedError(f"{field} must be YYYY-MM-DD")
    return value


def require_nonnegative_integer(value: Any, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise BlockedError(f"{field} must be a nonnegative integer")
    return value


def require_positive_integer(value: Any, field: str) -> int:
    amount = require_nonnegative_integer(value, field)
    if amount == 0:
        raise BlockedError(f"{field} must be a positive integer")
    return amount


def normalized_position_policy(value: dict[str, Any]) -> dict[str, Any]:
    exact_fields(value, POSITION_POLICY_FIELDS, "position policy")
    if value["schema"] != POSITION_POLICY_SCHEMA:
        raise BlockedError(
            f"position policy schema must be {POSITION_POLICY_SCHEMA}"
        )
    close_seconds = require_positive_integer(
        value["close_liquidation_seconds_before_close"],
        "close_liquidation_seconds_before_close",
    )
    if close_seconds > 3600:
        raise BlockedError(
            "close_liquidation_seconds_before_close must be <= 3600"
        )
    ttl = require_positive_integer(
        value["exit_order_ttl_seconds"], "exit_order_ttl_seconds"
    )
    if ttl > 300:
        raise BlockedError("exit_order_ttl_seconds must be <= 300")
    replacements = require_nonnegative_integer(
        value["max_exit_replacements"], "max_exit_replacements"
    )
    if replacements > 20:
        raise BlockedError("max_exit_replacements must be <= 20")
    quote_age = require_positive_integer(
        value["quote_max_age_seconds"], "quote_max_age_seconds"
    )
    if quote_age > 60:
        raise BlockedError("quote_max_age_seconds must be <= 60")
    expected_enums = {
        "partial_entry_action": "CANCEL_REMAINDER_MANAGE_FILLED",
        "gap_down_action": "MARKETABLE_LIMIT",
        "daily_loss_action": "ENTRY_FREEZE_LIQUIDATE",
        "overnight_residual_action": "EXIT_ONLY_NEXT_SESSION",
    }
    for field, expected in expected_enums.items():
        if value[field] != expected:
            raise BlockedError(f"{field} must be {expected}")
    return {
        **value,
        "close_liquidation_seconds_before_close": close_seconds,
        "exit_order_ttl_seconds": ttl,
        "max_exit_replacements": replacements,
        "quote_max_age_seconds": quote_age,
    }


def strategy_position_id(plan_hash: str, intent: dict[str, Any]) -> str:
    if len(plan_hash) != 64 or any(
        character not in "0123456789abcdef" for character in plan_hash
    ):
        raise BlockedError("plan_hash must be lowercase SHA-256")
    seed = {
        "plan_hash": plan_hash,
        "entry_intent_id": intent.get("intent_id"),
        "market": intent.get("market"),
        "exchange": intent.get("exchange"),
        "symbol": intent.get("symbol"),
        "venue": intent.get("venue"),
    }
    return sha256_json(seed)[:32]


def position_from_intent(
    *,
    plan_hash: str,
    intent: dict[str, Any],
    session_date: str,
    fx_to_krw: Any,
) -> dict[str, Any]:
    session = require_iso_date(session_date, "session_date")
    market = str(intent.get("market", "")).upper()
    if market not in MARKET_EXCHANGES:
        raise BlockedError("intent market must be KR or US")
    exchange = str(intent.get("exchange", "")).upper()
    if exchange not in MARKET_EXCHANGES[market]:
        raise BlockedError("intent exchange does not match market")
    symbol = normalize_symbol(market, str(intent.get("symbol", "")))
    venue = str(intent.get("venue", "")).upper()
    if not venue:
        raise BlockedError("intent venue is required")
    quantity = positive_decimal(intent.get("quantity"), "intent quantity")
    if quantity != quantity.to_integral_value():
        raise BlockedError("strategy positions require whole-share quantity")
    currency = str(intent.get("currency", "")).upper()
    if currency != MARKET_CURRENCY[market]:
        raise BlockedError("intent currency does not match market")
    fx = positive_decimal(fx_to_krw, "fx_to_krw")
    stop = positive_decimal(intent.get("stop_price"), "stop_price")
    take_profit = positive_decimal(
        intent.get("take_profit_price"), "take_profit_price"
    )
    tick_source = intent.get("resolved_tick_size")
    if tick_source is None:
        tick_source = intent.get("tick_size")
    if tick_source is None:
        raise BlockedError("intent resolved tick size is required")
    tick = positive_decimal(tick_source, "tick_size")
    entry_intent_id = str(intent.get("intent_id", ""))
    if len(entry_intent_id) != 32:
        raise BlockedError("entry intent ID must contain 32 characters")
    output = {
        "schema": STRATEGY_POSITION_SCHEMA,
        "position_id": strategy_position_id(plan_hash, intent),
        "plan_hash": plan_hash,
        "entry_intent_id": entry_intent_id,
        "market": market,
        "exchange": exchange,
        "symbol": symbol,
        "venue": venue,
        "currency": currency,
        "entry_session_date": session,
        "last_managed_session_date": session,
        "planned_quantity": format(quantity, "f"),
        "acquired_quantity": "0",
        "exited_quantity": "0",
        "average_entry_price": None,
        "average_exit_price": None,
        "stop_price": format(stop, "f"),
        "take_profit_price": format(take_profit, "f"),
        "tick_size": format(tick, "f"),
        "fx_to_krw": format(fx, "f"),
        "daily_baseline_session_date": None,
        "daily_baseline_price": None,
        "realized_pnl_native": "0",
        "daily_realized_pnl_native": "0",
        "status": "PENDING_ENTRY",
    }
    return normalize_strategy_position(output)


def normalize_strategy_position(value: dict[str, Any]) -> dict[str, Any]:
    exact_fields(value, STRATEGY_POSITION_FIELDS, "strategy position")
    if value["schema"] != STRATEGY_POSITION_SCHEMA:
        raise BlockedError(
            f"strategy position schema must be {STRATEGY_POSITION_SCHEMA}"
        )
    position_id = str(value["position_id"])
    if len(position_id) != 32 or any(
        character not in "0123456789abcdef" for character in position_id
    ):
        raise BlockedError("position_id must be 32 lowercase hex characters")
    plan_hash = str(value["plan_hash"])
    if len(plan_hash) != 64 or any(
        character not in "0123456789abcdef" for character in plan_hash
    ):
        raise BlockedError("plan_hash must be lowercase SHA-256")
    market = str(value["market"]).upper()
    exchange = str(value["exchange"]).upper()
    if market not in MARKET_EXCHANGES or exchange not in MARKET_EXCHANGES[market]:
        raise BlockedError("strategy position exchange does not match market")
    symbol = normalize_symbol(market, str(value["symbol"]))
    currency = str(value["currency"]).upper()
    if currency != MARKET_CURRENCY[market]:
        raise BlockedError("strategy position currency does not match market")
    entry_date = require_iso_date(
        value["entry_session_date"], "entry_session_date"
    )
    managed_date = require_iso_date(
        value["last_managed_session_date"], "last_managed_session_date"
    )
    if managed_date < entry_date:
        raise BlockedError("last managed session cannot precede entry session")
    planned = positive_decimal(value["planned_quantity"], "planned_quantity")
    acquired = decimal_value(value["acquired_quantity"], "acquired_quantity")
    exited = decimal_value(value["exited_quantity"], "exited_quantity")
    for amount, field in (
        (planned, "planned_quantity"),
        (acquired, "acquired_quantity"),
        (exited, "exited_quantity"),
    ):
        if amount < 0 or amount != amount.to_integral_value():
            raise BlockedError(f"{field} must be a nonnegative whole number")
    if acquired > planned:
        raise BlockedError("acquired quantity exceeds planned quantity")
    if exited > acquired:
        raise BlockedError("exited quantity exceeds acquired quantity")
    average_entry = value["average_entry_price"]
    if acquired > 0:
        average_entry = format(
            positive_decimal(average_entry, "average_entry_price"), "f"
        )
    elif average_entry is not None:
        raise BlockedError("zero acquired quantity cannot have an entry price")
    average_exit = value["average_exit_price"]
    if exited > 0:
        average_exit = format(
            positive_decimal(average_exit, "average_exit_price"), "f"
        )
    elif average_exit is not None:
        raise BlockedError("zero exited quantity cannot have an exit price")
    baseline_date = value["daily_baseline_session_date"]
    baseline_price = value["daily_baseline_price"]
    if baseline_date is None:
        if baseline_price is not None:
            raise BlockedError("daily baseline date and price must both be null")
    else:
        baseline_date = require_iso_date(
            baseline_date, "daily_baseline_session_date"
        )
        if baseline_date != managed_date:
            raise BlockedError("daily baseline must match last managed session")
        baseline_price = format(
            positive_decimal(baseline_price, "daily_baseline_price"), "f"
        )
    stop = positive_decimal(value["stop_price"], "stop_price")
    take_profit = positive_decimal(value["take_profit_price"], "take_profit_price")
    if stop >= take_profit:
        raise BlockedError("stop price must be below take-profit price")
    tick = positive_decimal(value["tick_size"], "tick_size")
    fx = positive_decimal(value["fx_to_krw"], "fx_to_krw")
    realized = decimal_value(value["realized_pnl_native"], "realized_pnl_native")
    daily_realized = decimal_value(
        value["daily_realized_pnl_native"], "daily_realized_pnl_native"
    )
    status = str(value["status"])
    if status not in POSITION_STATUSES:
        raise BlockedError(f"unsupported strategy position status: {status}")
    if acquired == 0 and status != "PENDING_ENTRY":
        raise BlockedError("zero acquired quantity must be PENDING_ENTRY")
    if acquired > 0 and exited == acquired and status != "CLOSED":
        raise BlockedError("fully exited position must be CLOSED")
    if acquired > exited and status in {"PENDING_ENTRY", "CLOSED"}:
        raise BlockedError("open quantity requires an open position status")
    return {
        **value,
        "market": market,
        "exchange": exchange,
        "symbol": symbol,
        "currency": currency,
        "entry_session_date": entry_date,
        "last_managed_session_date": managed_date,
        "planned_quantity": format(planned, "f"),
        "acquired_quantity": format(acquired, "f"),
        "exited_quantity": format(exited, "f"),
        "average_entry_price": average_entry,
        "average_exit_price": average_exit,
        "stop_price": format(stop, "f"),
        "take_profit_price": format(take_profit, "f"),
        "tick_size": format(tick, "f"),
        "fx_to_krw": format(fx, "f"),
        "daily_baseline_session_date": baseline_date,
        "daily_baseline_price": baseline_price,
        "realized_pnl_native": format(realized, "f"),
        "daily_realized_pnl_native": format(daily_realized, "f"),
        "status": status,
    }


def open_quantity(position: dict[str, Any]) -> Decimal:
    normalized = normalize_strategy_position(position)
    return Decimal(normalized["acquired_quantity"]) - Decimal(
        normalized["exited_quantity"]
    )


def record_entry_fill(
    position: dict[str, Any],
    *,
    cumulative_filled_quantity: Any,
    average_fill_price: Any,
    session_date: str,
) -> dict[str, Any]:
    normalized = normalize_strategy_position(position)
    session = require_iso_date(session_date, "session_date")
    cumulative = decimal_value(
        cumulative_filled_quantity, "cumulative_filled_quantity"
    )
    prior = Decimal(normalized["acquired_quantity"])
    planned = Decimal(normalized["planned_quantity"])
    if (
        cumulative < prior
        or cumulative > planned
        or cumulative != cumulative.to_integral_value()
    ):
        raise BlockedError("entry cumulative fill is non-monotonic or out of bounds")
    if cumulative == 0:
        return normalized
    average = positive_decimal(average_fill_price, "average_fill_price")
    if session < normalized["entry_session_date"]:
        raise BlockedError("entry fill session precedes the planned entry session")
    exited = Decimal(normalized["exited_quantity"])
    next_status = "CLOSED" if cumulative == exited else "OPEN"
    output = {
        **normalized,
        "last_managed_session_date": session,
        "acquired_quantity": format(cumulative, "f"),
        "average_entry_price": format(average, "f"),
        "daily_baseline_session_date": session,
        "daily_baseline_price": format(average, "f"),
        "status": next_status,
    }
    return normalize_strategy_position(output)


def roll_position_session(
    position: dict[str, Any],
    *,
    session_date: str,
) -> dict[str, Any]:
    normalized = normalize_strategy_position(position)
    session = require_iso_date(session_date, "session_date")
    if session < normalized["last_managed_session_date"]:
        raise BlockedError("cannot roll a position backward")
    if session == normalized["last_managed_session_date"]:
        return normalized
    status = (
        "CLOSED"
        if open_quantity(normalized) == 0
        else "CARRY_EXIT_ONLY"
    )
    return normalize_strategy_position(
        {
            **normalized,
            "last_managed_session_date": session,
            "daily_baseline_session_date": None,
            "daily_baseline_price": None,
            "daily_realized_pnl_native": "0",
            "status": status,
        }
    )


def set_daily_baseline(
    position: dict[str, Any],
    *,
    session_date: str,
    price: Any,
) -> dict[str, Any]:
    normalized = normalize_strategy_position(position)
    session = require_iso_date(session_date, "session_date")
    if session != normalized["last_managed_session_date"]:
        raise BlockedError("daily baseline session must be the managed session")
    baseline = positive_decimal(price, "daily baseline price")
    existing_date = normalized["daily_baseline_session_date"]
    if existing_date is not None:
        if (
            existing_date != session
            or Decimal(str(normalized["daily_baseline_price"])) != baseline
        ):
            raise BlockedError("daily baseline is immutable within one session")
        return normalized
    return normalize_strategy_position(
        {
            **normalized,
            "daily_baseline_session_date": session,
            "daily_baseline_price": format(baseline, "f"),
        }
    )


def record_exit_fill(
    position: dict[str, Any],
    *,
    cumulative_exited_quantity: Any,
    average_exit_price: Any,
) -> dict[str, Any]:
    normalized = normalize_strategy_position(position)
    cumulative = decimal_value(
        cumulative_exited_quantity, "cumulative_exited_quantity"
    )
    prior = Decimal(normalized["exited_quantity"])
    acquired = Decimal(normalized["acquired_quantity"])
    if (
        cumulative < prior
        or cumulative > acquired
        or cumulative != cumulative.to_integral_value()
    ):
        raise BlockedError("exit cumulative fill is non-monotonic or out of bounds")
    if cumulative == 0:
        return normalized
    average_exit = positive_decimal(average_exit_price, "average_exit_price")
    entry = positive_decimal(
        normalized["average_entry_price"], "average_entry_price"
    )
    prior_average_exit = (
        Decimal(str(normalized["average_exit_price"]))
        if prior > 0
        else Decimal(0)
    )
    prior_exit_proceeds = prior_average_exit * prior
    cumulative_exit_proceeds = average_exit * cumulative
    incremental_quantity = cumulative - prior
    incremental_exit_proceeds = cumulative_exit_proceeds - prior_exit_proceeds
    realized = (
        Decimal(normalized["realized_pnl_native"])
        + incremental_exit_proceeds
        - entry * incremental_quantity
    )
    baseline_value = normalized["daily_baseline_price"]
    if baseline_value is None:
        raise BlockedError("daily baseline is required before recording an exit")
    baseline = positive_decimal(baseline_value, "daily_baseline_price")
    daily_realized = (
        Decimal(normalized["daily_realized_pnl_native"])
        + incremental_exit_proceeds
        - baseline * incremental_quantity
    )
    status = "CLOSED" if cumulative == acquired else "EXIT_PARTIAL"
    return normalize_strategy_position(
        {
            **normalized,
            "exited_quantity": format(cumulative, "f"),
            "average_exit_price": format(average_exit, "f"),
            "realized_pnl_native": format(realized, "f"),
            "daily_realized_pnl_native": format(daily_realized, "f"),
            "status": status,
        }
    )


def partial_entry_requires_cancel(
    position: dict[str, Any],
    *,
    broker_remaining_quantity: Any,
) -> bool:
    normalized = normalize_strategy_position(position)
    remaining = decimal_value(
        broker_remaining_quantity, "broker_remaining_quantity"
    )
    return Decimal(normalized["acquired_quantity"]) > 0 and remaining > 0


def normalize_exit_quote(
    quote: dict[str, Any],
    *,
    position: dict[str, Any],
    observed_at: datetime,
    max_age_seconds: int,
) -> dict[str, Any]:
    normalized = normalize_strategy_position(position)
    if str(quote.get("market", "")).upper() != normalized["market"]:
        raise BlockedError("exit quote market does not match position")
    if normalize_symbol(
        normalized["market"], str(quote.get("symbol", ""))
    ) != normalized["symbol"]:
        raise BlockedError("exit quote symbol does not match position")
    last = positive_decimal(quote.get("last_price"), "exit quote last_price")
    bid = positive_decimal(quote.get("best_bid"), "exit quote best_bid")
    ask = positive_decimal(quote.get("best_ask"), "exit quote best_ask")
    if bid > ask:
        raise BlockedError("exit quote best bid exceeds best ask")
    trade_time = parse_aware_datetime(
        quote.get("trade_timestamp"), "exit quote trade_timestamp"
    )
    book_time = parse_aware_datetime(
        quote.get("book_timestamp"), "exit quote book_timestamp"
    )
    received = parse_aware_datetime(
        quote.get("received_at"), "exit quote received_at"
    )
    now = observed_at.astimezone(timezone.utc)
    for supplied, field in (
        (trade_time, "trade_timestamp"),
        (book_time, "book_timestamp"),
        (received, "received_at"),
    ):
        age = (now - supplied.astimezone(timezone.utc)).total_seconds()
        if age < -1:
            raise BlockedError(f"exit quote {field} is future-dated")
        if age > max_age_seconds:
            raise BlockedError(f"exit quote {field} is stale")
    tick = Decimal(normalized["tick_size"])
    return {
        "last_price": format(last, "f"),
        "best_bid": format(bid, "f"),
        "best_ask": format(ask, "f"),
        "limit_price": format(round_down_to_tick(bid, tick), "f"),
        "trade_timestamp": trade_time.isoformat(),
        "book_timestamp": book_time.isoformat(),
        "received_at": received.isoformat(),
    }


def exit_decision(
    position: dict[str, Any],
    quote: dict[str, Any],
    policy: dict[str, Any],
    *,
    observed_at: datetime,
    regular_close: datetime,
    first_valid_quote_of_session: bool,
    daily_loss_breached: bool,
) -> dict[str, Any]:
    normalized = normalize_strategy_position(position)
    normalized_policy = normalized_position_policy(policy)
    quantity = open_quantity(normalized)
    if quantity <= 0:
        action = "CLOSED"
        reason = "NO_OPEN_QUANTITY"
        normalized_quote = None
        limit_price = None
    else:
        if observed_at.tzinfo is None or observed_at.utcoffset() is None:
            raise BlockedError("observed_at must be timezone-aware")
        if regular_close.tzinfo is None or regular_close.utcoffset() is None:
            raise BlockedError("regular_close must be timezone-aware")
        if observed_at >= regular_close:
            action = "MANUAL_BLOCK"
            reason = "MARKET_CLOSED_WITH_RESIDUAL"
            normalized_quote = None
            limit_price = None
        else:
            normalized_quote = normalize_exit_quote(
                quote,
                position=normalized,
                observed_at=observed_at,
                max_age_seconds=normalized_policy["quote_max_age_seconds"],
            )
            last = Decimal(normalized_quote["last_price"])
            stop = Decimal(normalized["stop_price"])
            target = Decimal(normalized["take_profit_price"])
            close_trigger = regular_close - timedelta(
                seconds=normalized_policy[
                    "close_liquidation_seconds_before_close"
                ]
            )
            action = "HOLD"
            reason = "NO_EXIT_TRIGGER"
            if daily_loss_breached:
                action = "SUBMIT_EXIT"
                reason = "DAILY_LOSS_LIMIT"
            elif last <= stop:
                action = "SUBMIT_EXIT"
                reason = (
                    "STOP_GAP"
                    if first_valid_quote_of_session and last < stop
                    else "STOP"
                )
            elif last >= target:
                action = "SUBMIT_EXIT"
                reason = "TAKE_PROFIT"
            elif observed_at >= close_trigger:
                action = "SUBMIT_EXIT"
                reason = "MARKET_CLOSE"
            limit_price = (
                normalized_quote["limit_price"]
                if action == "SUBMIT_EXIT"
                else None
            )
    without_hash = {
        "schema": EXIT_DECISION_SCHEMA,
        "position_id": normalized["position_id"],
        "session_date": normalized["last_managed_session_date"],
        "observed_at": observed_at.isoformat(),
        "action": action,
        "reason": reason,
        "quantity": format(max(quantity, Decimal(0)), "f"),
        "order_type": "LIMIT" if action == "SUBMIT_EXIT" else None,
        "limit_price": limit_price,
        "quote": normalized_quote,
        "daily_loss_breached": bool(daily_loss_breached),
    }
    return {**without_hash, "decision_hash": sha256_json(without_hash)}


def marked_daily_pnl_krw(
    position: dict[str, Any],
    *,
    best_bid: Any,
    estimated_exit_cost_bps: Any,
) -> Decimal:
    normalized = normalize_strategy_position(position)
    if normalized["daily_baseline_price"] is None:
        raise BlockedError("daily baseline is required for daily P&L")
    bid = positive_decimal(best_bid, "best_bid")
    cost_bps = decimal_value(estimated_exit_cost_bps, "estimated_exit_cost_bps")
    if cost_bps < 0 or cost_bps > 10000:
        raise BlockedError("estimated_exit_cost_bps must be between 0 and 10000")
    quantity = open_quantity(normalized)
    baseline = Decimal(str(normalized["daily_baseline_price"]))
    daily_realized = Decimal(normalized["daily_realized_pnl_native"])
    exit_cost = bid * quantity * cost_bps / Decimal(10000)
    pnl_native = daily_realized + (bid - baseline) * quantity - exit_cost
    return pnl_native * Decimal(normalized["fx_to_krw"])


def portfolio_daily_loss_krw(
    marked_pnl_by_position: dict[str, Decimal],
) -> Decimal:
    pnl = sum(marked_pnl_by_position.values(), Decimal(0))
    return max(-pnl, Decimal(0))


def daily_loss_limit_breached(loss_krw: Any, maximum_loss_krw: Any) -> bool:
    loss = decimal_value(loss_krw, "loss_krw")
    maximum = positive_decimal(maximum_loss_krw, "maximum_loss_krw")
    if loss < 0:
        raise BlockedError("loss_krw must be nonnegative")
    return loss >= maximum


def exit_order_id(
    decision: dict[str, Any],
    *,
    attempt: int,
) -> str:
    attempt_number = require_nonnegative_integer(attempt, "exit attempt")
    if decision.get("schema") != EXIT_DECISION_SCHEMA:
        raise BlockedError("exit decision schema is invalid")
    if decision.get("action") != "SUBMIT_EXIT":
        raise BlockedError("exit order requires a SUBMIT_EXIT decision")
    seed = {
        "position_id": decision["position_id"],
        "session_date": decision["session_date"],
        "reason": decision["reason"],
        "quantity": decision["quantity"],
        "limit_price": decision["limit_price"],
        "attempt": attempt_number,
    }
    return sha256_json(seed)[:32]


@dataclass(frozen=True)
class StoredPosition:
    position_id: str
    status: str
    payload: dict[str, Any]


class PositionLedger:
    """Account/market-level position registry that survives session dates."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(str(self.path))
        self.connection.execute("PRAGMA journal_mode=WAL")
        self.connection.execute("PRAGMA synchronous=FULL")
        self.connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS strategy_positions (
                position_id TEXT PRIMARY KEY,
                status TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS position_events (
                sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                position_id TEXT NOT NULL,
                event_type TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            """
        )
        self.connection.commit()

    def close(self) -> None:
        self.connection.close()

    def put(
        self,
        position: dict[str, Any],
        *,
        event_type: str,
        event_payload: dict[str, Any],
    ) -> None:
        normalized = normalize_strategy_position(position)
        if not event_type:
            raise BlockedError("position event type is required")
        if not isinstance(event_payload, dict):
            raise BlockedError("position event payload must be an object")
        now = datetime.now(timezone.utc).isoformat()
        rendered = canonical_json(normalized)
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            existing = self.connection.execute(
                """
                SELECT payload_json FROM strategy_positions
                WHERE position_id = ?
                """,
                (normalized["position_id"],),
            ).fetchone()
            if existing is None:
                self.connection.execute(
                    """
                    INSERT INTO strategy_positions
                        (position_id, status, payload_json, updated_at)
                    VALUES (?, ?, ?, ?)
                    """,
                    (
                        normalized["position_id"],
                        normalized["status"],
                        rendered,
                        now,
                    ),
                )
            else:
                self.connection.execute(
                    """
                    UPDATE strategy_positions
                    SET status = ?, payload_json = ?, updated_at = ?
                    WHERE position_id = ?
                    """,
                    (
                        normalized["status"],
                        rendered,
                        now,
                        normalized["position_id"],
                    ),
                )
            self.connection.execute(
                """
                INSERT INTO position_events
                    (position_id, event_type, payload_json, created_at)
                VALUES (?, ?, ?, ?)
                """,
                (
                    normalized["position_id"],
                    event_type,
                    canonical_json(event_payload),
                    now,
                ),
            )
        except BaseException:
            self.connection.rollback()
            raise
        else:
            self.connection.commit()

    def get(self, position_id: str) -> StoredPosition:
        row = self.connection.execute(
            """
            SELECT position_id, status, payload_json
            FROM strategy_positions WHERE position_id = ?
            """,
            (position_id,),
        ).fetchone()
        if row is None:
            raise BlockedError(f"unknown strategy position: {position_id}")
        return StoredPosition(
            position_id=str(row[0]),
            status=str(row[1]),
            payload=normalize_strategy_position(json.loads(row[2])),
        )

    def get_optional(self, position_id: str) -> StoredPosition | None:
        row = self.connection.execute(
            """
            SELECT position_id, status, payload_json
            FROM strategy_positions WHERE position_id = ?
            """,
            (position_id,),
        ).fetchone()
        if row is None:
            return None
        return StoredPosition(
            position_id=str(row[0]),
            status=str(row[1]),
            payload=normalize_strategy_position(json.loads(row[2])),
        )

    def open_positions(self) -> list[StoredPosition]:
        rows = self.connection.execute(
            """
            SELECT position_id, status, payload_json
            FROM strategy_positions
            WHERE status != 'CLOSED'
            ORDER BY position_id
            """
        ).fetchall()
        return [
            StoredPosition(
                position_id=str(row[0]),
                status=str(row[1]),
                payload=normalize_strategy_position(json.loads(row[2])),
            )
            for row in rows
        ]

    def events(self, position_id: str) -> list[dict[str, Any]]:
        rows = self.connection.execute(
            """
            SELECT sequence, event_type, payload_json, created_at
            FROM position_events
            WHERE position_id = ?
            ORDER BY sequence
            """,
            (position_id,),
        ).fetchall()
        return [
            {
                "sequence": int(row[0]),
                "event_type": str(row[1]),
                "payload": json.loads(row[2]),
                "created_at": str(row[3]),
            }
            for row in rows
        ]


def reconcile_account_positions(
    ledger: PositionLedger,
    *,
    account_snapshot: dict[str, Any],
    session_date: str,
) -> dict[str, Any]:
    """Match bot-owned positions to the broker snapshot and roll valid carries."""
    session = require_iso_date(session_date, "session_date")
    account = normalized_account_snapshot(
        account_snapshot,
        screen_schema=SCREEN_SCHEMA_V2,
    )
    market = account["market"]
    account_quantities: dict[tuple[str, str], Decimal] = {}
    for item in account["positions"]:
        key = (str(item["exchange"]), str(item["symbol"]))
        quantity = decimal_value(item.get("quantity"), "account position quantity")
        if quantity < 0:
            raise BlockedError("account position quantity must be nonnegative")
        account_quantities[key] = account_quantities.get(key, Decimal(0)) + quantity
    open_order_keys = {
        (str(item["exchange"]), str(item["symbol"]))
        for item in account["open_orders"]
    }

    stored_positions = [
        item for item in ledger.open_positions() if item.payload["market"] == market
    ]
    by_key: dict[tuple[str, str], list[StoredPosition]] = {}
    for stored in stored_positions:
        key = (stored.payload["exchange"], stored.payload["symbol"])
        by_key.setdefault(key, []).append(stored)

    results: list[dict[str, Any]] = []
    managed_keys: set[tuple[str, str]] = set()
    for key in sorted(by_key):
        positions = by_key[key]
        managed_keys.add(key)
        expected = sum(
            (open_quantity(item.payload) for item in positions), Decimal(0)
        )
        observed = account_quantities.get(key, Decimal(0))
        reasons: list[str] = []
        if len(positions) != 1:
            reasons.append("duplicate_strategy_positions_for_symbol")
        if observed != expected:
            reasons.append("broker_quantity_differs_from_strategy_ledger")
        if key in open_order_keys:
            reasons.append("broker_has_open_order_for_strategy_symbol")
        if any(item.status == "MANUAL_BLOCK" for item in positions):
            reasons.append("prior_manual_block_requires_explicit_repair")

        if reasons:
            for stored in positions:
                if stored.status != "MANUAL_BLOCK":
                    blocked_position = normalize_strategy_position(
                        {**stored.payload, "status": "MANUAL_BLOCK"}
                    )
                    ledger.put(
                        blocked_position,
                        event_type="ACCOUNT_RECONCILIATION_BLOCKED",
                        event_payload={
                            "session_date": session,
                            "expected_quantity": format(expected, "f"),
                            "observed_quantity": format(observed, "f"),
                            "reasons": reasons,
                        },
                    )
            state = "MANUAL_BLOCK"
        else:
            stored = positions[0]
            rolled = roll_position_session(
                stored.payload,
                session_date=session,
            )
            if rolled != stored.payload:
                ledger.put(
                    rolled,
                    event_type="POSITION_CARRIED",
                    event_payload={
                        "from_session_date": stored.payload[
                            "last_managed_session_date"
                        ],
                        "to_session_date": session,
                        "quantity": format(expected, "f"),
                    },
                )
            state = rolled["status"]
        results.append(
            {
                "exchange": key[0],
                "symbol": key[1],
                "strategy_position_ids": [
                    item.position_id for item in positions
                ],
                "expected_quantity": format(expected, "f"),
                "observed_quantity": format(observed, "f"),
                "state": state,
                "reasons": reasons,
            }
        )

    unmanaged = [
        {
            "exchange": key[0],
            "symbol": key[1],
            "quantity": format(quantity, "f"),
        }
        for key, quantity in sorted(account_quantities.items())
        if key not in managed_keys and quantity > 0
    ]
    without_hash = {
        "schema": POSITION_RECONCILIATION_SCHEMA,
        "account_as_of": account["as_of"],
        "account_alias": account["account_alias"],
        "market": market,
        "session_date": session,
        "status": (
            "MANUAL_BLOCK"
            if any(item["state"] == "MANUAL_BLOCK" for item in results)
            else "READY"
        ),
        "managed_positions": results,
        "unmanaged_broker_positions": unmanaged,
        "api_mutation_count": 0,
        "live_enabled": False,
    }
    return {
        **without_hash,
        "reconciliation_hash": sha256_json(without_hash),
    }


def atomic_write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(json.dumps(value, sort_keys=True, separators=(",", ":")))
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


def self_test() -> None:
    plan_hash = "a" * 64
    intent = {
        "intent_id": "b" * 32,
        "market": "KR",
        "exchange": "KOSPI",
        "symbol": "005930",
        "venue": "KRX",
        "currency": "KRW",
        "quantity": "10",
        "stop_price": "65000",
        "take_profit_price": "80000",
        "resolved_tick_size": "100",
    }
    policy = normalized_position_policy(
        {
            "schema": POSITION_POLICY_SCHEMA,
            "close_liquidation_seconds_before_close": 600,
            "exit_order_ttl_seconds": 15,
            "max_exit_replacements": 3,
            "quote_max_age_seconds": 5,
            "partial_entry_action": "CANCEL_REMAINDER_MANAGE_FILLED",
            "gap_down_action": "MARKETABLE_LIMIT",
            "daily_loss_action": "ENTRY_FREEZE_LIQUIDATE",
            "overnight_residual_action": "EXIT_ONLY_NEXT_SESSION",
        }
    )
    position = position_from_intent(
        plan_hash=plan_hash,
        intent=intent,
        session_date="2026-07-29",
        fx_to_krw="1",
    )
    position = record_entry_fill(
        position,
        cumulative_filled_quantity="4",
        average_fill_price="70000",
        session_date="2026-07-29",
    )
    assert partial_entry_requires_cancel(
        position, broker_remaining_quantity="6"
    )
    now = datetime(2026, 7, 29, 9, 1, tzinfo=timezone(timedelta(hours=9)))
    quote = {
        "market": "KR",
        "symbol": "005930",
        "last_price": "64000",
        "best_bid": "63900",
        "best_ask": "64000",
        "trade_timestamp": now.isoformat(),
        "book_timestamp": now.isoformat(),
        "received_at": now.isoformat(),
    }
    decision = exit_decision(
        position,
        quote,
        policy,
        observed_at=now,
        regular_close=datetime(
            2026, 7, 29, 15, 30, tzinfo=timezone(timedelta(hours=9))
        ),
        first_valid_quote_of_session=True,
        daily_loss_breached=False,
    )
    assert decision["reason"] == "STOP_GAP"
    assert decision["quantity"] == "4"
    assert decision["limit_price"] == "63900"
    assert len(exit_order_id(decision, attempt=0)) == 32
    assert marked_daily_pnl_krw(
        position,
        best_bid="64000",
        estimated_exit_cost_bps="30",
    ) < 0
    position = record_exit_fill(
        position,
        cumulative_exited_quantity="2",
        average_exit_price="64000",
    )
    assert position["status"] == "EXIT_PARTIAL"
    assert open_quantity(position) == Decimal(2)
    carried = roll_position_session(position, session_date="2026-07-30")
    assert carried["status"] == "CARRY_EXIT_ONLY"
    with tempfile.TemporaryDirectory(prefix="qta-position-ledger-") as temporary:
        ledger = PositionLedger(Path(temporary) / "positions.sqlite3")
        try:
            ledger.put(
                carried,
                event_type="POSITION_CARRIED",
                event_payload={"session_date": "2026-07-30"},
            )
            assert ledger.open_positions()[0].status == "CARRY_EXIT_ONLY"
            assert ledger.events(carried["position_id"])[0]["event_type"] == (
                "POSITION_CARRIED"
            )
        finally:
            ledger.close()
    print(
        json.dumps(
            {
                "schema": STRATEGY_POSITION_SCHEMA,
                "self_test": "PASS",
                "decision_hash": decision["decision_hash"],
                "live_enabled": False,
                "api_mutation_count": 0,
            },
            sort_keys=True,
        )
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--self-test", action="store_true")
    subparsers = parser.add_subparsers(dest="command")
    reconcile_parser = subparsers.add_parser("reconcile-account")
    reconcile_parser.add_argument("--ledger", required=True)
    reconcile_parser.add_argument("--account-snapshot", required=True)
    reconcile_parser.add_argument("--session-date", required=True)
    reconcile_parser.add_argument("--output", required=True)
    args = parser.parse_args(argv)
    try:
        if args.self_test:
            self_test()
            return 0
        if args.command != "reconcile-account":
            parser.error("use --self-test or reconcile-account")
        paths = {
            name: Path(getattr(args, name))
            for name in ("ledger", "account_snapshot", "output")
        }
        if any(not path.is_absolute() for path in paths.values()):
            raise BlockedError("ledger, account snapshot, and output paths must be absolute")
        if (
            not paths["account_snapshot"].is_file()
            or paths["account_snapshot"].is_symlink()
        ):
            raise BlockedError(
                "account snapshot must be a regular non-symlink file"
            )
        account_value = json.loads(
            paths["account_snapshot"].read_text(encoding="utf-8")
        )
        if not isinstance(account_value, dict):
            raise BlockedError("account snapshot must contain one JSON object")
        ledger = PositionLedger(paths["ledger"])
        try:
            receipt = reconcile_account_positions(
                ledger,
                account_snapshot=account_value,
                session_date=args.session_date,
            )
        finally:
            ledger.close()
        atomic_write_json(paths["output"], receipt)
        print(json.dumps(receipt, sort_keys=True))
        return 0
    except (
        AssertionError,
        BlockedError,
        ValueError,
        OSError,
        json.JSONDecodeError,
    ) as exc:
        print(
            json.dumps(
                {
                    "status": "BLOCKED",
                    "reason": str(exc),
                    "api_mutation_count": 0,
                    "live_enabled": False,
                },
                sort_keys=True,
            )
        )
        return 2


if __name__ == "__main__":
    sys.exit(main())

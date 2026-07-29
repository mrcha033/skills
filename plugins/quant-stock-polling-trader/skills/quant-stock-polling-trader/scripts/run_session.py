#!/usr/bin/env python3
"""Preview or run a deterministic first-hour shadow/paper polling session."""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from decimal import ROUND_CEILING, Decimal
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from broker_adapters import (
    AmbiguousMutationError,
    AuthoritativeMutationRejection,
    KisBroker,
    TossBroker,
    TransportFailure,
)
from broker_credentials import load_kis_credentials, require_kis_runtime_credentials
from execution_core import (
    ENTRY_WINDOWS,
    EXECUTION_VERSION,
    MARKET_CURRENCY,
    MAX_SNAPSHOT_AGE_SECONDS,
    PLAN_SCHEMA,
    QTA2_REGIME_MAX_AGE_SECONDS,
    QTA2_REGIME_MINIMUM_CHANGE_BPS,
    QTA2_REGIME_METRIC,
    QTA_METHOD_V2,
    SCREEN_SCHEMA_V2,
    V2_EXCHANGE_CONTRACTS,
    V2_EXCHANGES_BY_MARKET,
    BlockedError,
    Ledger,
    SingleWriterLock,
    canonical_json,
    decimal_value,
    emit_json,
    exact_fields,
    load_json_object,
    nonnegative_decimal,
    normalize_symbol,
    normalized_execution_policy,
    normalized_market_session,
    parse_aware_datetime,
    parse_iso_date,
    plan_orders as rebuild_order_plan,
    positive_decimal,
    redact,
    sha256_json,
    validate_nonempty_string,
    validate_sha256,
)
from position_lifecycle import (
    PositionLedger,
    position_from_intent,
    reconcile_account_positions,
    record_entry_fill,
    strategy_position_id,
)

VENUE_SCHEMA = "qta-venue-map/v1"
SESSION_STATUS_SCHEMA = "qta-session-status/v2"
PREOPEN_WARMUP_SECONDS = 30
SUBMIT_HTTP_TIMEOUT_SECONDS = 10
DEFAULT_QUOTE_HTTP_REQUESTS_PER_INTENT = 2
BROKER_PACING_SECONDS = {
    "toss": Decimal("0.1"),
    "kis-paper": Decimal("1.05"),
    "kis-live": Decimal("0.12"),
}
PLAN_FIELDS = {
    "schema",
    "plan_status",
    "execution_version",
    "context",
    "entry_window",
    "quote_policy",
    "order_policy",
    "settled_cash_start",
    "borrowed_buying_power_excluded",
    "settled_cash_unreserved",
    "intents",
    "skipped",
    "frozen_inputs",
    "plan_hash",
}
FROZEN_INPUT_FIELDS = {
    "screen",
    "account",
    "exposure",
    "risk",
    "execution",
}
BASE_CONTEXT_FIELDS = {
    "execution_version",
    "screen_hash",
    "account_hash",
    "exposure_hash",
    "risk_hash",
    "execution_policy_hash",
    "execution_policy",
    "market_session",
    "market_session_hash",
    "snapshot_max_age_seconds",
    "broker",
    "environment",
    "account_alias",
    "market",
}
V2_CONTEXT_FIELDS = BASE_CONTEXT_FIELDS | {
    "screen_schema",
    "candidate_order_contract",
    "exchange_order",
    "broker_symbol_qualification",
    "analysis_date",
    "snapshot_as_of",
}
ENTRY_WINDOW_FIELDS = {
    "timezone",
    "start",
    "end",
    "poll_interval_seconds",
}
QUOTE_POLICY_FIELDS = {
    "max_age_seconds",
    "max_spread_bps",
    "max_gap_bps",
    "trigger_mode",
}
ORDER_POLICY_FIELDS = {
    "ttl_seconds",
    "allow_partial_fill",
    "cancel_remainder_at_window_end",
}
BASE_INTENT_FIELDS = {
    "intent_id",
    "client_order_id",
    "rank",
    "market",
    "symbol",
    "currency",
    "side",
    "order_type",
    "time_in_force",
    "quantity",
    "entry_trigger",
    "limit_price",
    "stop_price",
    "take_profit_price",
    "effective_loss_per_share",
    "reserved_cash",
    "qta_payload_hash",
    "initial_state",
    "intent_hash",
}
V2_INTENT_FIELDS = BASE_INTENT_FIELDS | {
    "exchange",
    "exchange_rank",
    "canonical_symbol",
    "data_symbol",
    "broker_symbol",
    "instrument_type",
    "benchmark_id",
    "venue",
    "resolved_tick_size",
    "tick_contract_hash",
}
SKIP_REASONS = {
    "duplicate_planned_exposure",
    "existing_open_buy_order",
    "existing_exposure",
    "max_concurrent_positions",
    "quantity_below_one",
}
MARKET_REGIME_QUOTE_FIELDS = {
    "schema",
    "broker",
    "exchange",
    "benchmark_id",
    "regime_proxy_id",
    "provider_symbol",
    "current",
    "previous_close",
    "change_bps",
    "source_timestamp",
    "received_at",
    "raw_status",
}
REGIME_PROXY_BY_EXCHANGE = {
    "KOSPI": "KOSPI_COMPOSITE",
    "KOSDAQ": "KOSDAQ_COMPOSITE",
    "NYSE": "S&P_500",
    "NASDAQ": "NASDAQ_COMPOSITE",
}


def validate_positive_integer(value: Any, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise BlockedError(f"{field} must be a positive integer")
    return value


def validate_decimal_text(
    value: Any,
    field: str,
    *,
    positive: bool,
) -> Decimal:
    if not isinstance(value, str) or not value or value != value.strip():
        raise BlockedError(f"{field} must be a decimal string")
    result = (
        positive_decimal(value, field)
        if positive
        else nonnegative_decimal(value, field)
    )
    if format(result, "f") != value:
        raise BlockedError(f"{field} must be a canonical decimal string")
    return result


def validate_intent(
    intent: Any,
    *,
    index: int,
    context: dict[str, Any],
    is_v2: bool,
) -> tuple[int, Decimal, tuple[str, str]]:
    label = f"plan.intents[{index}]"
    if not isinstance(intent, dict):
        raise BlockedError(f"{label} must be an object")
    exact_fields(
        intent,
        V2_INTENT_FIELDS if is_v2 else BASE_INTENT_FIELDS,
        label,
    )
    validate_sha256(intent["intent_hash"], f"{label}.intent_hash")
    expected_intent_hash = sha256_json(
        {key: value for key, value in intent.items() if key != "intent_hash"}
    )
    if intent["intent_hash"] != expected_intent_hash:
        raise BlockedError(f"{label}.intent_hash does not match intent contents")
    validate_sha256(intent["qta_payload_hash"], f"{label}.qta_payload_hash")

    market = context["market"]
    if intent["market"] != market:
        raise BlockedError(f"{label}.market must equal plan.context.market")
    symbol = normalize_symbol(market, str(intent["symbol"]))
    if symbol != intent["symbol"]:
        raise BlockedError(f"{label}.symbol must be normalized")
    if intent["currency"] != MARKET_CURRENCY[market]:
        raise BlockedError(f"{label}.currency does not match market")
    if (
        intent["side"] != "BUY"
        or intent["order_type"] != "LIMIT"
        or intent["time_in_force"] != "DAY"
        or intent["initial_state"] != "PLANNED"
    ):
        raise BlockedError(f"{label} must be BUY, LIMIT, DAY, and initially PLANNED")
    rank = validate_positive_integer(intent["rank"], f"{label}.rank")
    quantity_text = intent["quantity"]
    if (
        not isinstance(quantity_text, str)
        or not quantity_text.isdigit()
        or str(int(quantity_text)) != quantity_text
        or int(quantity_text) <= 0
    ):
        raise BlockedError(f"{label}.quantity must be a canonical positive integer")
    quantity = Decimal(quantity_text)
    entry = validate_decimal_text(
        intent["entry_trigger"],
        f"{label}.entry_trigger",
        positive=True,
    )
    limit_price = validate_decimal_text(
        intent["limit_price"],
        f"{label}.limit_price",
        positive=True,
    )
    stop = validate_decimal_text(
        intent["stop_price"],
        f"{label}.stop_price",
        positive=True,
    )
    take_profit = validate_decimal_text(
        intent["take_profit_price"],
        f"{label}.take_profit_price",
        positive=True,
    )
    effective_loss = validate_decimal_text(
        intent["effective_loss_per_share"],
        f"{label}.effective_loss_per_share",
        positive=True,
    )
    reserved_cash = validate_decimal_text(
        intent["reserved_cash"],
        f"{label}.reserved_cash",
        positive=True,
    )
    if not stop < entry <= limit_price < take_profit:
        raise BlockedError(
            f"{label} prices must satisfy stop < entry <= limit < take_profit"
        )
    if effective_loss < limit_price - stop:
        raise BlockedError(f"{label}.effective_loss_per_share is below price loss")
    if reserved_cash < limit_price * quantity:
        raise BlockedError(f"{label}.reserved_cash is below limit notional")

    intent_seed = {
        "plan_seed": sha256_json(context),
        "rank": rank,
        "market": market,
        "symbol": symbol,
        "side": "BUY",
        "order_type": "LIMIT",
        "time_in_force": "DAY",
        "quantity": quantity_text,
        "limit_price": intent["limit_price"],
    }
    exposure_namespace = market
    if is_v2:
        exchange = intent["exchange"]
        contract = V2_EXCHANGE_CONTRACTS.get(exchange)
        if contract is None or contract["market"] != market:
            raise BlockedError(f"{label}.exchange does not match market")
        exchange_rank = validate_positive_integer(
            intent["exchange_rank"],
            f"{label}.exchange_rank",
        )
        canonical_symbol = normalize_symbol(
            market,
            validate_nonempty_string(
                intent["canonical_symbol"],
                f"{label}.canonical_symbol",
            ),
        )
        broker_symbol = normalize_symbol(
            market,
            validate_nonempty_string(
                intent["broker_symbol"],
                f"{label}.broker_symbol",
            ),
        )
        if canonical_symbol != intent["canonical_symbol"]:
            raise BlockedError(f"{label}.canonical_symbol must be normalized")
        if broker_symbol != intent["broker_symbol"] or symbol != broker_symbol:
            raise BlockedError(
                f"{label}.symbol and broker_symbol must match and be normalized"
            )
        validate_nonempty_string(intent["data_symbol"], f"{label}.data_symbol")
        if intent["instrument_type"] not in {"COMMON", "ADR", "REIT"}:
            raise BlockedError(f"{label}.instrument_type is unsupported")
        if (
            intent["benchmark_id"] != contract["benchmark_id"]
            or intent["venue"] != contract["venue"]
        ):
            raise BlockedError(f"{label}.benchmark_id and venue must match exchange")
        validate_sha256(
            intent["tick_contract_hash"],
            f"{label}.tick_contract_hash",
        )
        tick_size = validate_decimal_text(
            intent["resolved_tick_size"],
            f"{label}.resolved_tick_size",
            positive=True,
        )
        for price, field in (
            (entry, "entry_trigger"),
            (limit_price, "limit_price"),
            (stop, "stop_price"),
            (take_profit, "take_profit_price"),
        ):
            if price % tick_size != 0:
                raise BlockedError(
                    f"{label}.{field} must align to resolved_tick_size"
                )
        intent_seed.update(
            {
                "exchange": exchange,
                "exchange_rank": exchange_rank,
                "canonical_symbol": canonical_symbol,
                "data_symbol": intent["data_symbol"],
                "broker_symbol": broker_symbol,
                "venue": intent["venue"],
            }
        )
        exposure_namespace = exchange
    expected_intent_id = sha256_json(intent_seed)[:32]
    if intent["intent_id"] != expected_intent_id:
        raise BlockedError(f"{label}.intent_id does not match deterministic seed")
    if intent["client_order_id"] != f"qta-{expected_intent_id[:28]}":
        raise BlockedError(
            f"{label}.client_order_id does not match deterministic intent_id"
        )
    return rank, reserved_cash, (exposure_namespace, symbol)


def validate_skipped(
    skipped: Any,
    *,
    market: str,
) -> None:
    if not isinstance(skipped, list):
        raise BlockedError("plan.skipped must be an array")
    for index, item in enumerate(skipped):
        label = f"plan.skipped[{index}]"
        if not isinstance(item, dict):
            raise BlockedError(f"{label} must be an object")
        base_fields = {"market", "symbol", "reason"}
        extended_fields = base_fields | {
            "exchange",
            "exchange_rank",
            "canonical_symbol",
        }
        item_fields = frozenset(item)
        if item_fields not in {
            frozenset(base_fields),
            frozenset(extended_fields),
        }:
            raise BlockedError(f"{label} fields mismatch")
        if item["market"] != market:
            raise BlockedError(f"{label}.market must equal plan market")
        if normalize_symbol(market, str(item["symbol"])) != item["symbol"]:
            raise BlockedError(f"{label}.symbol must be normalized")
        if item["reason"] not in SKIP_REASONS:
            raise BlockedError(f"{label}.reason is unsupported")
        if item_fields == frozenset(extended_fields):
            exchange = item["exchange"]
            contract = V2_EXCHANGE_CONTRACTS.get(exchange)
            if contract is None or contract["market"] != market:
                raise BlockedError(f"{label}.exchange does not match market")
            validate_positive_integer(
                item["exchange_rank"],
                f"{label}.exchange_rank",
            )
            canonical_symbol = normalize_symbol(
                market,
                str(item["canonical_symbol"]),
            )
            if canonical_symbol != item["canonical_symbol"]:
                raise BlockedError(f"{label}.canonical_symbol must be normalized")


def validate_plan(plan: dict[str, Any]) -> None:
    if not isinstance(plan, dict):
        raise BlockedError("plan must be an object")
    exact_fields(plan, PLAN_FIELDS, "plan")
    if plan.get("schema") != PLAN_SCHEMA:
        raise BlockedError(f"plan schema must be {PLAN_SCHEMA}")
    if plan.get("plan_status") not in {"READY", "NO_ORDERS"}:
        raise BlockedError("plan_status must be READY or NO_ORDERS")
    if plan["execution_version"] != EXECUTION_VERSION:
        raise BlockedError(f"execution_version must be {EXECUTION_VERSION}")
    claimed = plan.get("plan_hash")
    validate_sha256(claimed, "plan_hash")
    unhashed = dict(plan)
    unhashed.pop("plan_hash")
    if sha256_json(unhashed) != claimed:
        raise BlockedError("plan_hash does not match plan contents")

    context = plan.get("context")
    if not isinstance(context, dict):
        raise BlockedError("plan.context must be an object")
    is_v2 = context.get("screen_schema") == SCREEN_SCHEMA_V2
    exact_fields(
        context,
        V2_CONTEXT_FIELDS if is_v2 else BASE_CONTEXT_FIELDS,
        "plan.context",
    )
    if context["execution_version"] != plan["execution_version"]:
        raise BlockedError(
            "plan.context.execution_version must equal plan.execution_version"
        )
    for field in (
        "screen_hash",
        "account_hash",
        "exposure_hash",
        "risk_hash",
        "execution_policy_hash",
    ):
        validate_sha256(context[field], f"plan.context.{field}")
    validate_nonempty_string(context["account_alias"], "plan.context.account_alias")
    if context["broker"] not in {"kis", "toss"}:
        raise BlockedError("plan.context.broker must be kis or toss")
    if context["environment"] not in {"paper", "shadow", "live"}:
        raise BlockedError("plan.context.environment must be paper, shadow, or live")
    market = context.get("market")
    if market not in ENTRY_WINDOWS:
        raise BlockedError("plan.context.market is unsupported")
    snapshot_max_age = validate_positive_integer(
        context["snapshot_max_age_seconds"],
        "plan.context.snapshot_max_age_seconds",
    )
    if snapshot_max_age > MAX_SNAPSHOT_AGE_SECONDS:
        raise BlockedError(
            "plan.context.snapshot_max_age_seconds exceeds the hard maximum"
        )
    market_session = context.get("market_session")
    normalized_session = normalized_market_session(
        market_session,
        expected_market=market,
        expected_timezone=ENTRY_WINDOWS[market]["timezone"],
    )
    if normalized_session != market_session:
        raise BlockedError("plan.context.market_session is not normalized")
    if context.get("market_session_hash") != normalized_session["session_hash"]:
        raise BlockedError(
            "plan.context.market_session_hash does not match market_session"
        )
    execution_policy = context["execution_policy"]
    if not isinstance(execution_policy, dict):
        raise BlockedError("plan.context.execution_policy must be an object")
    normalized_policy = normalized_execution_policy(execution_policy)
    if normalized_policy != execution_policy:
        raise BlockedError("plan.context.execution_policy is not normalized")
    if sha256_json(normalized_policy) != context["execution_policy_hash"]:
        raise BlockedError(
            "plan.context.execution_policy_hash does not match execution_policy"
        )
    if (
        normalized_policy["market"] != market
        or normalized_policy["market_session"] != normalized_session
        or normalized_policy["snapshot_max_age_seconds"] != snapshot_max_age
    ):
        raise BlockedError("plan.context execution policy duplicates are inconsistent")
    if is_v2:
        if (
            context["candidate_order_contract"]
            != "exchange_contract_order_then_exchange_rank_then_broker_symbol"
            or context["exchange_order"] != list(V2_EXCHANGES_BY_MARKET[market])
            or context["broker_symbol_qualification"] != "KIS"
            or context["broker"] != "kis"
        ):
            raise BlockedError("qta-screen/v2 plan context contract is invalid")
        analysis_date = parse_iso_date(
            context["analysis_date"],
            "plan.context.analysis_date",
        )
        if analysis_date.isoformat() != normalized_session["previous_session_date"]:
            raise BlockedError(
                "plan.context.analysis_date must equal previous_session_date"
            )
        snapshot_as_of = parse_aware_datetime(
            context["snapshot_as_of"],
            "plan.context.snapshot_as_of",
        )
        session_zone = ZoneInfo(normalized_session["timezone"])
        if (
            snapshot_as_of.astimezone(session_zone).date().isoformat()
            != normalized_session["session_date"]
        ):
            raise BlockedError("plan.context.snapshot_as_of must fall on session_date")
        source_as_of = parse_aware_datetime(
            normalized_session["source_as_of"],
            "market_session.source_as_of",
        )
        regular_open = parse_aware_datetime(
            normalized_session["regular_open"],
            "market_session.regular_open",
        )
        if not source_as_of <= snapshot_as_of <= regular_open:
            raise BlockedError(
                "plan.context.snapshot_as_of must be between source and open"
            )
        if (regular_open - snapshot_as_of).total_seconds() > snapshot_max_age:
            raise BlockedError("plan.context.snapshot_as_of exceeds snapshot freshness")

    entry_window = plan["entry_window"]
    if not isinstance(entry_window, dict):
        raise BlockedError("plan.entry_window must be an object")
    exact_fields(entry_window, ENTRY_WINDOW_FIELDS, "plan.entry_window")
    if entry_window["timezone"] != ENTRY_WINDOWS[market]["timezone"]:
        raise BlockedError("plan.entry_window.timezone does not match market")
    window_start = parse_aware_datetime(
        entry_window["start"], "plan.entry_window.start"
    )
    window_end = parse_aware_datetime(entry_window["end"], "plan.entry_window.end")
    regular_open = parse_aware_datetime(
        normalized_session["regular_open"],
        "market_session.regular_open",
    )
    regular_close = parse_aware_datetime(
        normalized_session["regular_close"],
        "market_session.regular_close",
    )
    if (
        window_start != regular_open
        or window_end != regular_open + timedelta(hours=1)
        or window_end > regular_close
    ):
        raise BlockedError(
            "plan.entry_window must be the first hour of the regular session"
        )
    validate_positive_integer(
        entry_window["poll_interval_seconds"],
        "plan.entry_window.poll_interval_seconds",
    )
    if entry_window != {
        "timezone": normalized_policy["timezone"],
        "start": normalized_policy["entry_window_start"],
        "end": normalized_policy["entry_window_end"],
        "poll_interval_seconds": normalized_policy["poll_interval_seconds"],
    }:
        raise BlockedError(
            "plan.entry_window does not match the embedded execution policy"
        )

    quote_policy = plan["quote_policy"]
    if not isinstance(quote_policy, dict):
        raise BlockedError("plan.quote_policy must be an object")
    exact_fields(quote_policy, QUOTE_POLICY_FIELDS, "plan.quote_policy")
    validate_positive_integer(
        quote_policy["max_age_seconds"],
        "plan.quote_policy.max_age_seconds",
    )
    for field in ("max_spread_bps", "max_gap_bps"):
        value = validate_decimal_text(
            quote_policy[field],
            f"plan.quote_policy.{field}",
            positive=False,
        )
        if value > Decimal(10000):
            raise BlockedError(f"plan.quote_policy.{field} must be <= 10000")
    if quote_policy["trigger_mode"] not in {"AT_OR_ABOVE", "CROSS_FROM_BELOW"}:
        raise BlockedError("plan.quote_policy.trigger_mode is unsupported")
    if quote_policy != {
        "max_age_seconds": normalized_policy["quote_max_age_seconds"],
        "max_spread_bps": normalized_policy["max_spread_bps"],
        "max_gap_bps": normalized_policy["max_gap_bps"],
        "trigger_mode": normalized_policy["trigger_mode"],
    }:
        raise BlockedError(
            "plan.quote_policy does not match the embedded execution policy"
        )

    order_policy = plan["order_policy"]
    if not isinstance(order_policy, dict):
        raise BlockedError("plan.order_policy must be an object")
    exact_fields(order_policy, ORDER_POLICY_FIELDS, "plan.order_policy")
    validate_positive_integer(
        order_policy["ttl_seconds"],
        "plan.order_policy.ttl_seconds",
    )
    for field in ("allow_partial_fill", "cancel_remainder_at_window_end"):
        if not isinstance(order_policy[field], bool):
            raise BlockedError(f"plan.order_policy.{field} must be boolean")
    if order_policy != {
        "ttl_seconds": normalized_policy["order_ttl_seconds"],
        "allow_partial_fill": normalized_policy["allow_partial_fill"],
        "cancel_remainder_at_window_end": normalized_policy[
            "cancel_remainder_at_window_end"
        ],
    }:
        raise BlockedError(
            "plan.order_policy does not match the embedded execution policy"
        )

    settled_cash_start = validate_decimal_text(
        plan["settled_cash_start"],
        "plan.settled_cash_start",
        positive=False,
    )
    validate_decimal_text(
        plan["borrowed_buying_power_excluded"],
        "plan.borrowed_buying_power_excluded",
        positive=False,
    )
    settled_cash_unreserved = validate_decimal_text(
        plan["settled_cash_unreserved"],
        "plan.settled_cash_unreserved",
        positive=False,
    )
    if settled_cash_unreserved > settled_cash_start:
        raise BlockedError(
            "plan.settled_cash_unreserved must not exceed settled_cash_start"
        )
    intents = plan["intents"]
    if not isinstance(intents, list):
        raise BlockedError("plan.intents must be an array")
    if plan["plan_status"] != ("READY" if intents else "NO_ORDERS"):
        raise BlockedError("plan_status does not match intents")
    intent_ranks: list[int] = []
    intent_keys: set[tuple[str, str]] = set()
    reserved_total = Decimal(0)
    for index, intent in enumerate(intents):
        rank, reserved_cash, exposure_key = validate_intent(
            intent,
            index=index,
            context=context,
            is_v2=is_v2,
        )
        if exposure_key in intent_keys:
            raise BlockedError("plan.intents contain duplicate exposure keys")
        intent_keys.add(exposure_key)
        intent_ranks.append(rank)
        reserved_total += reserved_cash
    if intent_ranks != sorted(set(intent_ranks)):
        raise BlockedError("plan.intent ranks must be strictly increasing and unique")
    if settled_cash_start - reserved_total != settled_cash_unreserved:
        raise BlockedError("plan cash totals do not match reserved_cash across intents")
    validate_skipped(plan["skipped"], market=market)
    frozen_inputs = plan["frozen_inputs"]
    if not isinstance(frozen_inputs, dict):
        raise BlockedError("plan.frozen_inputs must be an object")
    exact_fields(frozen_inputs, FROZEN_INPUT_FIELDS, "plan.frozen_inputs")
    if any(not isinstance(frozen_inputs[field], dict) for field in FROZEN_INPUT_FIELDS):
        raise BlockedError("every plan.frozen_inputs value must be an object")
    rebuilt_plan = rebuild_order_plan(
        frozen_inputs["screen"],
        frozen_inputs["account"],
        frozen_inputs["exposure"],
        frozen_inputs["risk"],
        frozen_inputs["execution"],
    )
    if rebuilt_plan != plan:
        raise BlockedError(
            "plan does not match deterministic rebuild from frozen_inputs"
        )


def validate_broker_binding(plan: dict[str, Any], broker_name: str) -> None:
    context = plan["context"]
    expected_broker = "toss" if broker_name == "toss" else "kis"
    if context["broker"] != expected_broker:
        raise BlockedError("plan broker does not match selected adapter")
    environment = context["environment"]
    if broker_name == "kis-paper":
        if environment != "paper":
            raise BlockedError("KIS paper adapter requires a paper plan")
    elif broker_name == "kis-live":
        if environment not in {"shadow", "live"}:
            raise BlockedError("KIS live adapter requires a shadow/live plan")
    elif broker_name == "toss":
        if environment not in {"shadow", "live"}:
            raise BlockedError("Toss adapter requires a shadow/live plan")
    else:
        raise BlockedError(f"unsupported broker: {broker_name}")


def normalize_venue_map(value: dict[str, Any]) -> dict[str, str]:
    if set(value) != {"schema", "venues"} or value.get("schema") != VENUE_SCHEMA:
        raise BlockedError(f"venue map schema must be {VENUE_SCHEMA}")
    venues = value["venues"]
    if not isinstance(venues, dict):
        raise BlockedError("venue map venues must be an object")
    normalized: dict[str, str] = {}
    for key, venue in venues.items():
        if not isinstance(key, str) or ":" not in key or not isinstance(venue, str):
            raise BlockedError(
                "venue keys must be MARKET:SYMBOL or EXCHANGE:SYMBOL strings"
            )
        namespace, symbol = key.split(":", 1)
        normalized_key = f"{namespace.strip().upper()}:{symbol.strip().upper()}"
        normalized_venue = venue.strip().upper()
        if not namespace.strip() or not symbol.strip() or not normalized_venue:
            raise BlockedError("venue keys and values must be non-empty")
        if (
            normalized_key in normalized
            and normalized[normalized_key] != normalized_venue
        ):
            raise BlockedError(f"conflicting venue map entry for {normalized_key}")
        normalized[normalized_key] = normalized_venue
    return dict(sorted(normalized.items()))


def venue_for(venues: dict[str, str], intent: dict[str, Any]) -> str:
    market = str(intent["market"]).upper()
    symbol = str(intent["symbol"]).upper()
    market_key = f"{market}:{symbol}"
    intent_venue = intent.get("venue")
    exchange = intent.get("exchange")
    if intent_venue is None:
        if market_key not in venues:
            raise BlockedError(f"venue map missing {market_key}")
        return venues[market_key]

    if not isinstance(intent_venue, str) or not intent_venue.strip():
        raise BlockedError("intent venue must be a non-empty string")
    venue = intent_venue.strip().upper()
    exchange_contracts = {
        "KOSPI": ("KR", "KRX"),
        "KOSDAQ": ("KR", "KRX"),
        "NYSE": ("US", "NYSE"),
        "NASDAQ": ("US", "NASD"),
    }
    if not isinstance(exchange, str) or exchange.upper() not in exchange_contracts:
        raise BlockedError("intent venue requires a supported exchange")
    normalized_exchange = exchange.upper()
    expected_market, expected_venue = exchange_contracts[normalized_exchange]
    if market != expected_market or venue != expected_venue:
        raise BlockedError(
            f"{normalized_exchange} intent requires market {expected_market} "
            f"and venue {expected_venue}"
        )

    exchange_key = f"{normalized_exchange}:{symbol}"
    mapped = venues.get(exchange_key, venues.get(market_key))
    if mapped is not None and mapped != venue:
        raise BlockedError(
            f"venue map {mapped} conflicts with hashed intent venue {venue} "
            f"for {exchange_key}"
        )
    return venue


def parse_timestamp(value: Any, field: str) -> datetime:
    if not isinstance(value, str):
        raise BlockedError(f"{field} must be an ISO-8601 string")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise BlockedError(f"{field} must be an ISO-8601 string") from exc
    if parsed.tzinfo is None:
        raise BlockedError(f"{field} must include a timezone")
    return parsed


def evaluate_quote(
    intent: dict[str, Any],
    quote: dict[str, Any],
    quote_policy: dict[str, Any],
    now: datetime,
    previous_last: Decimal | None,
    *,
    require_source_timestamp: bool,
) -> dict[str, Any]:
    quality_reasons: list[str] = []
    if quote.get("raw_status") != "OK":
        quality_reasons.append("quote_status_not_ok")
    if quote.get("market") != intent.get("market") or quote.get("symbol") != intent.get(
        "symbol"
    ):
        quality_reasons.append("quote_instrument_mismatch")
    last = decimal_value(quote.get("last_price"), "last_price")
    ask = decimal_value(quote.get("best_ask"), "best_ask")
    bid = decimal_value(quote.get("best_bid"), "best_bid")
    if bid <= 0 or ask <= 0 or last <= 0 or ask < bid:
        quality_reasons.append("malformed_bid_ask")
    received = parse_timestamp(quote.get("received_at"), "received_at")
    age = (
        now.astimezone(timezone.utc) - received.astimezone(timezone.utc)
    ).total_seconds()
    if age < -1 or age > int(quote_policy["max_age_seconds"]):
        quality_reasons.append("stale_quote")
    source_timestamp_present = all(
        bool(quote.get(field)) for field in ("trade_timestamp", "book_timestamp")
    )
    if require_source_timestamp and not source_timestamp_present:
        quality_reasons.append("source_timestamp_missing")
    source_ages: list[float] = []
    for field in ("trade_timestamp", "book_timestamp"):
        value = quote.get(field)
        if not value:
            continue
        try:
            source_time = parse_timestamp(value, field)
        except BlockedError:
            quality_reasons.append(f"{field}_invalid")
            continue
        source_ages.append(
            (
                now.astimezone(timezone.utc) - source_time.astimezone(timezone.utc)
            ).total_seconds()
        )
    source_age = max(source_ages) if source_ages else None
    if any(
        item < -1 or item > int(quote_policy["max_age_seconds"]) for item in source_ages
    ):
        quality_reasons.append("stale_source_quote")
    reasons = list(quality_reasons)
    midpoint = (ask + bid) / Decimal(2) if ask >= bid > 0 else Decimal(0)
    spread_bps = (
        Decimal(10000) * (ask - bid) / midpoint if midpoint > 0 else Decimal("Infinity")
    )
    if spread_bps > decimal_value(quote_policy["max_spread_bps"], "max_spread_bps"):
        reasons.append("spread_too_wide")
    entry = decimal_value(intent["entry_trigger"], "entry_trigger")
    limit_price = decimal_value(intent["limit_price"], "limit_price")
    if ask > limit_price:
        reasons.append("ask_above_limit")
    trigger_mode = quote_policy["trigger_mode"]
    if trigger_mode == "AT_OR_ABOVE":
        crossed = last >= entry
    elif trigger_mode == "CROSS_FROM_BELOW":
        crossed = previous_last is not None and previous_last < entry <= last
    else:
        raise BlockedError(f"unsupported trigger_mode: {trigger_mode}")
    if not crossed:
        reasons.append("entry_not_triggered")
    return {
        "triggered": not reasons,
        "quality_valid": not quality_reasons,
        "reasons": reasons,
        "last_price": format(last, "f"),
        "best_ask": format(ask, "f"),
        "best_bid": format(bid, "f"),
        "spread_bps": format(spread_bps, "f"),
        "quote_age_seconds": f"{age:.6f}",
        "source_timestamp_present": source_timestamp_present,
        "source_age_seconds": None if source_age is None else f"{source_age:.6f}",
    }


def normalize_toss_order(raw: dict[str, Any]) -> dict[str, Any]:
    raw_status = str(raw.get("status", "UNKNOWN"))
    status_map = {
        "PENDING": "ACKNOWLEDGED",
        "PENDING_CANCEL": "CANCEL_PENDING",
        "PENDING_REPLACE": "UNKNOWN",
        "PARTIAL_FILLED": "PARTIALLY_FILLED",
        "FILLED": "FILLED",
        "CANCELED": "CANCELLED",
        "REJECTED": "REJECTED",
        "CANCEL_REJECTED": "UNKNOWN",
        "REPLACE_REJECTED": "UNKNOWN",
        "REPLACED": "UNKNOWN",
    }
    execution = raw.get("execution")
    if not isinstance(execution, dict):
        execution = {}
    return {
        "normalized_status": status_map.get(raw_status, "UNKNOWN"),
        "raw_status": raw_status,
        "filled_quantity": execution.get("filledQuantity"),
        "average_fill_price": execution.get("averageFilledPrice"),
        "raw": redact(raw),
    }


def preview_requests(
    plan: dict[str, Any], broker_name: str, venues: dict[str, str]
) -> dict[str, Any]:
    validate_plan(plan)
    previews: list[dict[str, Any]] = []
    if broker_name == "toss":
        broker: Any = TossBroker(
            client_id="preview",
            client_secret="preview",
            account_seq=1,
        )
        for intent in plan["intents"]:
            previews.append(
                {
                    "intent_id": intent["intent_id"],
                    "request": broker.preview_submit(intent),
                }
            )
    elif broker_name in {"kis-paper", "kis-live"}:
        environment = broker_name.split("-", 1)[1]
        broker = KisBroker(
            app_key="preview",
            app_secret="preview",
            account_prefix="00000000",
            account_product="01",
            environment=environment,
            access_token="preview",
        )
        for intent in plan["intents"]:
            previews.append(
                {
                    "intent_id": intent["intent_id"],
                    "request": broker.preview_submit(
                        intent, venue=venue_for(venues, intent)
                    ),
                }
            )
    else:
        raise BlockedError(f"unsupported broker: {broker_name}")
    output = {
        "schema": "qta-request-preview/v1",
        "status": "READY",
        "plan_hash": plan["plan_hash"],
        "broker": broker_name,
        "account_bound": False,
        "request_hash_scope": "placeholder_account",
        "trigger_evaluated": False,
        "mutation_sent": False,
        "previews": previews,
    }
    output["preview_hash"] = sha256_json(output)
    return output


def require_environment(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise BlockedError(f"required environment variable is absent: {name}")
    return value


def runtime_toss_account_seq() -> int:
    raw = require_environment("QTA_TOSS_ACCOUNT_SEQ")
    try:
        account_seq = int(raw)
    except ValueError as exc:
        raise BlockedError(
            "QTA_TOSS_ACCOUNT_SEQ must be a canonical positive integer"
        ) from exc
    if account_seq <= 0 or str(account_seq) != raw:
        raise BlockedError("QTA_TOSS_ACCOUNT_SEQ must be a canonical positive integer")
    return account_seq


def create_broker(broker_name: str) -> Any:
    if broker_name == "toss":
        return TossBroker(
            client_id=require_environment("QTA_TOSS_CLIENT_ID"),
            client_secret=require_environment("QTA_TOSS_CLIENT_SECRET"),
            account_seq=runtime_toss_account_seq(),
            access_token=os.environ.get("QTA_TOSS_ACCESS_TOKEN"),
            access_token_expires_at=os.environ.get("QTA_TOSS_ACCESS_TOKEN_EXPIRES_AT"),
        )
    if broker_name in {"kis-paper", "kis-live"}:
        environment = broker_name.split("-", 1)[1]
        return KisBroker(
            app_key=require_environment("QTA_KIS_APP_KEY"),
            app_secret=require_environment("QTA_KIS_APP_SECRET"),
            account_prefix=require_environment("QTA_KIS_ACCOUNT_PREFIX"),
            account_product=require_environment("QTA_KIS_ACCOUNT_PRODUCT"),
            environment=environment,
            access_token=os.environ.get("QTA_KIS_ACCESS_TOKEN"),
        )
    raise BlockedError(f"unsupported broker: {broker_name}")


def validate_mode(plan: dict[str, Any], broker_name: str, mode: str) -> None:
    validate_broker_binding(plan, broker_name)
    context = plan["context"]
    screen_method = plan["frozen_inputs"]["screen"].get("method_version")
    if screen_method == "qta-2.0.0" and mode != "shadow":
        raise BlockedError(
            "qta-2.0.0 is RESEARCH_ONLY and may run only in shadow mode"
        )
    if mode == "live":
        raise BlockedError(
            "live promotion is intentionally disabled until accepted-timeout "
            "recovery, terminal cancel verification, and OCO protection are complete"
        )
    if mode == "paper":
        if broker_name != "kis-paper" or context.get("environment") != "paper":
            raise BlockedError("paper mode requires a KIS paper plan and adapter")
    elif mode == "shadow":
        if context.get("environment") not in {"shadow", "live"}:
            raise BlockedError("shadow mode requires a shadow/live account snapshot")
    else:
        raise BlockedError("mode must be paper, shadow, or live")


def validate_runtime_capabilities(plan: dict[str, Any], broker_name: str) -> None:
    if broker_name == "kis-paper" and any(
        str(intent.get("market", "")).upper() == "US"
        for intent in plan.get("intents", [])
        if isinstance(intent, dict)
    ):
        raise BlockedError(
            "KIS U.S. paper sessions are unsupported because "
            "HHDFS76200100 order-book quotes have no paper TR; "
            "use KIS live production shadow quotes and test paper order "
            "serialization separately"
        )


def preflight_venues(
    plan: dict[str, Any],
    venues: dict[str, str],
) -> dict[str, str]:
    return {
        str(intent["intent_id"]): venue_for(venues, intent)
        for intent in plan["intents"]
    }


def preflight_submit_requests(
    broker: Any,
    broker_name: str,
    plan: dict[str, Any],
    venue_by_intent: dict[str, str],
) -> dict[str, dict[str, Any]]:
    return {
        str(intent["intent_id"]): (
            broker.preview_submit(intent)
            if broker_name == "toss"
            else broker.preview_submit(
                intent,
                venue=venue_by_intent[str(intent["intent_id"])],
            )
        )
        for intent in plan["intents"]
    }


def quote_http_request_count(
    broker_name: str,
    intent: dict[str, Any],
) -> int:
    if broker_name == "kis-live" and str(intent.get("market", "")).upper() == "US":
        return 1
    return DEFAULT_QUOTE_HTTP_REQUESTS_PER_INTENT


def plan_uses_qta2(plan: dict[str, Any]) -> bool:
    frozen_inputs = plan.get("frozen_inputs")
    if not isinstance(frozen_inputs, dict):
        return False
    screen = frozen_inputs.get("screen")
    return (
        isinstance(screen, dict)
        and screen.get("method_version") == QTA_METHOD_V2
    )


def active_qta2_exchanges(plan: dict[str, Any]) -> list[str]:
    if not plan_uses_qta2(plan):
        return []
    present = {
        str(intent.get("exchange") or "").upper()
        for intent in plan.get("intents", [])
        if isinstance(intent, dict)
    }
    market = str(plan.get("context", {}).get("market") or "").upper()
    return [
        exchange
        for exchange in V2_EXCHANGES_BY_MARKET.get(market, ())
        if exchange in present
    ]


def polling_admission(
    plan: dict[str, Any],
    broker_name: str,
    mode: str,
) -> dict[str, Any]:
    """Fail closed when a full deterministic cycle cannot fit broker pacing."""
    if broker_name not in BROKER_PACING_SECONDS:
        raise BlockedError(f"unsupported broker: {broker_name}")
    intent_count = len(plan["intents"])
    quote_requests = sum(
        quote_http_request_count(broker_name, intent) for intent in plan["intents"]
    )
    regime_requests = len(active_qta2_exchanges(plan))
    mutation_requests = intent_count if mode == "paper" else 0
    request_budget = quote_requests + regime_requests + mutation_requests
    pacing_seconds = BROKER_PACING_SECONDS[broker_name] * request_budget
    minimum_interval = max(
        1,
        int(pacing_seconds.to_integral_value(rounding=ROUND_CEILING)),
    )
    configured_interval = int(plan["entry_window"]["poll_interval_seconds"])
    if configured_interval < minimum_interval:
        raise BlockedError(
            f"{broker_name} {mode} cycle with {intent_count} intents requires "
            f"poll_interval_seconds >= {minimum_interval} for "
            f"{request_budget} paced requests; got {configured_interval}"
        )
    return {
        "intent_count": intent_count,
        "quote_request_budget": quote_requests,
        "quote_request_contract": (
            "KIS-live US=1; KIS KR and Toss=2 per waiting intent"
        ),
        "market_regime_request_budget": regime_requests,
        "worst_case_mutation_requests": mutation_requests,
        "worst_case_request_budget": request_budget,
        "minimum_request_interval_ms": int(BROKER_PACING_SECONDS[broker_name] * 1000),
        "minimum_poll_interval_seconds": minimum_interval,
        "configured_poll_interval_seconds": configured_interval,
        "network_and_persistence_headroom_seconds": format(
            Decimal(configured_interval) - pacing_seconds,
            "f",
        ),
    }


def evaluate_market_regime(
    *,
    exchange: str,
    quote: dict[str, Any],
    observed_at: datetime,
    session_date: str,
) -> dict[str, Any]:
    """Validate and deterministically admit one QTA2 exchange regime quote."""
    exchange = str(exchange).upper()
    if exchange not in V2_EXCHANGE_CONTRACTS:
        raise BlockedError(f"unsupported market regime exchange: {exchange}")
    if observed_at.tzinfo is None or observed_at.utcoffset() is None:
        raise BlockedError("market regime observed_at must be timezone-aware")
    exact_fields(quote, MARKET_REGIME_QUOTE_FIELDS, "market regime quote")
    reasons: list[str] = []
    expected_benchmark = V2_EXCHANGE_CONTRACTS[exchange]["benchmark_id"]
    if quote["schema"] != "qta-market-regime-quote/v1":
        reasons.append("invalid_schema")
    if quote["broker"] != "kis":
        reasons.append("invalid_broker")
    if quote["exchange"] != exchange:
        reasons.append("exchange_mismatch")
    if quote["benchmark_id"] != expected_benchmark:
        reasons.append("benchmark_mismatch")
    if quote["regime_proxy_id"] != REGIME_PROXY_BY_EXCHANGE[exchange]:
        reasons.append("proxy_mismatch")
    if quote["raw_status"] != "OK":
        reasons.append("raw_status_not_ok")

    current = positive_decimal(quote["current"], "market regime current")
    previous_close = positive_decimal(
        quote["previous_close"],
        "market regime previous_close",
    )
    change_bps = decimal_value(
        quote["change_bps"],
        "market regime change_bps",
    )
    recomputed_change = (
        (current / previous_close - Decimal(1)) * Decimal(10000)
    )
    if abs(change_bps - recomputed_change) > Decimal("0.02"):
        reasons.append("change_bps_mismatch")

    zone_name = (
        "Asia/Seoul"
        if exchange in {"KOSPI", "KOSDAQ"}
        else "America/New_York"
    )
    for timestamp_field in ("source_timestamp", "received_at"):
        try:
            timestamp = parse_aware_datetime(
                quote[timestamp_field],
                f"market regime {timestamp_field}",
            )
        except BlockedError:
            reasons.append(f"{timestamp_field}_invalid")
            continue
        age_seconds = (
            observed_at.astimezone(timezone.utc)
            - timestamp.astimezone(timezone.utc)
        ).total_seconds()
        if age_seconds < -1 or age_seconds > QTA2_REGIME_MAX_AGE_SECONDS:
            reasons.append(f"{timestamp_field}_stale")
        if (
            timestamp_field == "source_timestamp"
            and timestamp.astimezone(ZoneInfo(zone_name)).date().isoformat()
            != session_date
        ):
            reasons.append("source_session_date_mismatch")

    quality_valid = not reasons
    admitted = (
        quality_valid
        and change_bps >= QTA2_REGIME_MINIMUM_CHANGE_BPS
    )
    if quality_valid and not admitted:
        reasons.append("benchmark_below_minimum")
    without_hash = {
        "metric": QTA2_REGIME_METRIC,
        "exchange": exchange,
        "benchmark_id": expected_benchmark,
        "regime_proxy_id": quote["regime_proxy_id"],
        "minimum_change_bps": format(
            QTA2_REGIME_MINIMUM_CHANGE_BPS,
            "f",
        ),
        "observed_change_bps": format(change_bps, "f"),
        "max_age_seconds": QTA2_REGIME_MAX_AGE_SECONDS,
        "quality_valid": quality_valid,
        "admitted": admitted,
        "reasons": reasons,
        "quote": quote,
        "observed_at": observed_at.isoformat(),
    }
    return {**without_hash, "decision_hash": sha256_json(without_hash)}


def wait_until(target: datetime) -> None:
    while True:
        remaining = target.timestamp() - datetime.now(timezone.utc).timestamp()
        if remaining <= 0:
            return
        time.sleep(min(remaining, 60))


def warm_broker_before_open(broker: Any, start: datetime) -> dict[str, Any]:
    warmup_at = start - timedelta(seconds=PREOPEN_WARMUP_SECONDS)
    now = datetime.now(timezone.utc)
    if now >= start:
        raise BlockedError(
            "session runner must start before the regular open for pre-open "
            "authentication warm-up"
        )
    wait_until(warmup_at)
    started_at = datetime.now(timezone.utc)
    started_monotonic = time.monotonic()
    try:
        broker.token()
    except (BlockedError, TransportFailure) as exc:
        raise BlockedError(f"pre-open broker authentication failed: {exc}") from exc
    completed_at = datetime.now(timezone.utc)
    if completed_at >= start:
        raise BlockedError(
            "broker authentication did not complete before the regular open"
        )
    return {
        "scheduled_at": warmup_at.isoformat(),
        "started_at": started_at.isoformat(),
        "completed_at": completed_at.isoformat(),
        "latency_ms": int((time.monotonic() - started_monotonic) * 1000),
        "token_material_logged": False,
    }


def entry_window_open(end: datetime, now: datetime | None = None) -> bool:
    observed = now or datetime.now(timezone.utc)
    return observed.timestamp() < end.timestamp()


def require_remaining_quantity(snapshot: dict[str, Any]) -> str:
    remaining = decimal_value(snapshot.get("remaining_quantity"), "remaining_quantity")
    if remaining <= 0 or remaining != remaining.to_integral_value():
        raise BlockedError(
            "KIS cancel requires a positive whole current remaining quantity"
        )
    return format(remaining, "f")


def current_order(
    broker: Any,
    broker_name: str,
    *,
    intent: dict[str, Any],
    ack: dict[str, Any],
    venue: str,
    trading_date: str,
) -> dict[str, Any]:
    if broker_name == "toss":
        return normalize_toss_order(broker.get_order(ack["broker_order_id"]))
    return broker.get_order(
        market=intent["market"],
        trading_date=trading_date,
        symbol=intent["symbol"],
        venue=venue,
        broker_order_id=ack["broker_order_id"],
    )


def cancel_order(
    broker: Any,
    broker_name: str,
    *,
    intent: dict[str, Any],
    ack: dict[str, Any],
    venue: str,
    quantity: str | None = None,
) -> dict[str, Any]:
    if broker_name == "toss":
        return broker.cancel(ack["broker_order_id"])
    if quantity is None:
        raise BlockedError("KIS cancel requires current remaining quantity")
    return broker.cancel(
        market=intent["market"],
        broker_order_id=ack["broker_order_id"],
        symbol=intent["symbol"],
        quantity=quantity,
        price=intent["limit_price"],
        venue=venue,
        organization=str(ack.get("organization") or ""),
    )


def session_status(
    plan: dict[str, Any],
    broker_name: str,
    mode: str,
    ledger: Ledger,
    started_at: datetime,
    stopped_at: datetime,
    freeze_reason: str | None,
    warmup: dict[str, Any],
    polling_metrics: dict[str, Any],
) -> dict[str, Any]:
    intents = []
    for intent in plan["intents"]:
        record = ledger.get(intent["intent_id"])
        intents.append(
            {
                "intent_id": record.intent_id,
                "state": record.state,
                "broker_order_id": record.broker_order_id,
                "request_hash": record.request_hash,
                "event_count": len(ledger.events(record.intent_id)),
            }
        )
    output = {
        "schema": SESSION_STATUS_SCHEMA,
        "plan_hash": plan["plan_hash"],
        "broker": broker_name,
        "mode": mode,
        "started_at": started_at.isoformat(),
        "stopped_at": stopped_at.isoformat(),
        "entry_freeze": freeze_reason is not None,
        "freeze_reason": freeze_reason,
        "preopen_warmup": warmup,
        "polling_metrics": polling_metrics,
        "intents": intents,
    }
    return output


def validate_ledger_scope(ledger: Ledger, plan: dict[str, Any]) -> None:
    current_intent_ids = {
        str(intent["intent_id"])
        for intent in plan["intents"]
        if isinstance(intent, dict) and "intent_id" in intent
    }
    foreign = [
        binding
        for binding in ledger.nonterminal_bindings()
        if binding["plan_hash"] != plan["plan_hash"]
        or binding["intent_id"] not in current_intent_ids
    ]
    if foreign:
        states = sorted({binding["state"] for binding in foreign})
        raise BlockedError(
            "state directory contains nonterminal intents outside the current "
            f"plan (states={states}); reconcile the prior plan before running "
            "a new one"
        )
    unresolved_states = {
        "RESERVED",
        "SUBMITTING",
        "UNKNOWN",
        "RECONCILING",
        "CANCEL_PENDING",
        "MANUAL_BLOCK",
    }
    unresolved = [
        binding
        for binding in ledger.nonterminal_bindings()
        if binding["state"] in unresolved_states
    ]
    if unresolved:
        states = sorted({binding["state"] for binding in unresolved})
        raise BlockedError(
            "state directory contains unresolved mutation states "
            f"(states={states}); broker evidence and explicit clearance are "
            "required before new entries"
        )


def position_ledger_path(state_directory: Path) -> Path:
    """Return the account/market ledger shared by all session-date directories."""
    if not state_directory.is_absolute():
        raise BlockedError("state_directory must be absolute")
    return state_directory.parent / "positions.sqlite3"


def persist_entry_fill(
    position_ledger: PositionLedger,
    *,
    plan: dict[str, Any],
    intent: dict[str, Any],
    snapshot: dict[str, Any],
    session_date: str,
) -> dict[str, Any] | None:
    """Persist an authoritative cumulative entry fill exactly once per change."""
    raw_filled = snapshot.get("filled_quantity")
    if raw_filled in (None, ""):
        return None
    cumulative = decimal_value(raw_filled, "broker filled_quantity")
    if cumulative < 0 or cumulative != cumulative.to_integral_value():
        raise BlockedError(
            "broker filled_quantity must be a nonnegative whole number"
        )
    if cumulative == 0:
        return None
    raw_average = snapshot.get("average_fill_price")
    if raw_average in (None, ""):
        raise BlockedError(
            "broker reported an entry fill without average_fill_price"
        )
    average = positive_decimal(raw_average, "broker average_fill_price")
    account = plan["frozen_inputs"]["account"]
    fx_to_krw = account.get("fx_to_krw")
    if intent["market"] == "KR":
        fx_to_krw = "1"
    elif fx_to_krw in (None, ""):
        raise BlockedError(
            "a U.S. strategy position requires frozen fx_to_krw"
        )

    position_id = strategy_position_id(plan["plan_hash"], intent)
    stored = position_ledger.get_optional(position_id)
    if stored is None:
        position = position_from_intent(
            plan_hash=plan["plan_hash"],
            intent=intent,
            session_date=session_date,
            fx_to_krw=fx_to_krw,
        )
        event_type = "ENTRY_FILL_CREATED"
    else:
        position = stored.payload
        if (
            position["plan_hash"] != plan["plan_hash"]
            or position["entry_intent_id"] != intent["intent_id"]
        ):
            raise BlockedError(
                "strategy position identity differs from plan entry intent"
            )
        prior_quantity = Decimal(position["acquired_quantity"])
        prior_average = (
            None
            if position["average_entry_price"] is None
            else Decimal(position["average_entry_price"])
        )
        if cumulative == prior_quantity and average == prior_average:
            return position
        if Decimal(position["exited_quantity"]) > 0:
            raise BlockedError(
                "entry fill changed after exit fills were recorded"
            )
        event_type = "ENTRY_FILL_UPDATED"

    updated = record_entry_fill(
        position,
        cumulative_filled_quantity=format(cumulative, "f"),
        average_fill_price=format(average, "f"),
        session_date=session_date,
    )
    position_ledger.put(
        updated,
        event_type=event_type,
        event_payload={
            "broker_order_id": snapshot.get("broker_order_id"),
            "normalized_status": snapshot.get("normalized_status"),
            "ordered_quantity": snapshot.get("ordered_quantity"),
            "filled_quantity": format(cumulative, "f"),
            "average_fill_price": format(average, "f"),
            "remaining_quantity": snapshot.get("remaining_quantity"),
            "session_date": session_date,
        },
    )
    return updated


def run_session(
    plan: dict[str, Any],
    broker_name: str,
    mode: str,
    venues: dict[str, str],
    state_directory: Path,
    *,
    max_cycles: int | None = None,
) -> dict[str, Any]:
    validate_plan(plan)
    validate_mode(plan, broker_name, mode)
    if plan["plan_status"] == "NO_ORDERS":
        state_directory.mkdir(parents=True, exist_ok=True)
        with SingleWriterLock(state_directory / "writer.lock"):
            ledger = Ledger(state_directory / "ledger.sqlite3")
            try:
                validate_ledger_scope(ledger, plan)
            finally:
                ledger.close()
        return {
            "schema": SESSION_STATUS_SCHEMA,
            "plan_hash": plan["plan_hash"],
            "broker": broker_name,
            "mode": mode,
            "status": "NO_ORDERS",
            "mutation_sent": False,
        }
    validate_runtime_capabilities(plan, broker_name)
    venue_by_intent = preflight_venues(plan, venues)
    start = parse_timestamp(plan["entry_window"]["start"], "entry window start")
    end = parse_timestamp(plan["entry_window"]["end"], "entry window end")
    interval = int(plan["entry_window"]["poll_interval_seconds"])
    admission = polling_admission(plan, broker_name, mode)
    broker = create_broker(broker_name)
    preview_by_intent = preflight_submit_requests(
        broker,
        broker_name,
        plan,
        venue_by_intent,
    )
    trading_date = start.date().isoformat()
    state_directory.mkdir(parents=True, exist_ok=True)
    started_at = datetime.now(timezone.utc)
    freeze_reason: str | None = None
    previous_last: dict[str, Decimal] = {}
    acknowledgements: dict[str, dict[str, Any]] = {}
    warmup: dict[str, Any] = {}
    polling_metrics: dict[str, Any] = {
        "admission": admission,
        "telemetry_anchor_intent_id": plan["intents"][0]["intent_id"],
        "cycles_started": 0,
        "cycles_completed": 0,
        "cycles_skipped": 0,
        "quotes_evaluated": 0,
        "market_regime_queries": 0,
        "market_regime_passes": 0,
        "market_regime_blocks": 0,
        "order_status_queries": 0,
        "submits_started": 0,
        "expected_http_requests_started": 0,
        "max_schedule_lateness_ms": 0,
        "max_cycle_duration_ms": 0,
        "max_quote_latency_ms": 0,
        "max_market_regime_latency_ms": 0,
        "max_order_status_latency_ms": 0,
        "max_submit_latency_ms": 0,
        "position_reconciliation": None,
    }

    with SingleWriterLock(state_directory / "writer.lock"):
        ledger = Ledger(state_directory / "ledger.sqlite3")
        position_ledger = PositionLedger(position_ledger_path(state_directory))
        try:
            validate_ledger_scope(ledger, plan)
            position_reconciliation = reconcile_account_positions(
                position_ledger,
                account_snapshot=plan["frozen_inputs"]["account"],
                session_date=trading_date,
            )
            polling_metrics["position_reconciliation"] = {
                "status": position_reconciliation["status"],
                "reconciliation_hash": position_reconciliation[
                    "reconciliation_hash"
                ],
                "managed_position_count": len(
                    position_reconciliation["managed_positions"]
                ),
                "unmanaged_broker_position_count": len(
                    position_reconciliation["unmanaged_broker_positions"]
                ),
            }
            if position_reconciliation["status"] != "READY":
                raise BlockedError(
                    "strategy position ledger differs from the frozen broker "
                    "account snapshot; reconcile MANUAL_BLOCK before new entries"
                )
            warmup = warm_broker_before_open(broker, start)
            for intent in plan["intents"]:
                ledger.create_intent(plan["plan_hash"], intent)
                if ledger.get(intent["intent_id"]).state == "PLANNED":
                    ledger.transition(intent["intent_id"], "WAIT_TRIGGER", {})

            cycle = 0
            while True:
                target = start.timestamp() + cycle * interval
                now = datetime.now(timezone.utc)
                if now.timestamp() >= end.timestamp():
                    freeze_reason = "entry_window_closed"
                    break
                if max_cycles is not None and cycle >= max_cycles:
                    freeze_reason = "max_cycles_reached"
                    break
                if now.timestamp() < target:
                    time.sleep(min(target - now.timestamp(), 60))
                    continue
                if now.timestamp() >= target + interval:
                    skipped_count = max(
                        1,
                        int((now.timestamp() - target) // interval),
                    )
                    if max_cycles is not None:
                        skipped_count = min(
                            skipped_count,
                            max_cycles - cycle,
                        )
                    lateness_ms = int((now.timestamp() - target) * 1000)
                    polling_metrics["cycles_skipped"] += skipped_count
                    polling_metrics["max_schedule_lateness_ms"] = max(
                        polling_metrics["max_schedule_lateness_ms"],
                        lateness_ms,
                    )
                    ledger.append_event(
                        plan["intents"][0]["intent_id"],
                        "CYCLE_SKIPPED",
                        {
                            "first_cycle": cycle,
                            "last_cycle": cycle + skipped_count - 1,
                            "skipped_count": skipped_count,
                            "scheduled_at": datetime.fromtimestamp(
                                target, timezone.utc
                            ).isoformat(),
                            "observed_at": now.isoformat(),
                            "lateness_ms": lateness_ms,
                            "reason": "scheduled_cycle_expired",
                        },
                    )
                    cycle += skipped_count
                    continue

                cycle_started_at = now
                cycle_started_monotonic = time.monotonic()
                cycle_deadline = min(target + interval, end.timestamp())
                lateness_ms = max(0, int((now.timestamp() - target) * 1000))
                polling_metrics["cycles_started"] += 1
                polling_metrics["max_schedule_lateness_ms"] = max(
                    polling_metrics["max_schedule_lateness_ms"],
                    lateness_ms,
                )
                cycle_events: list[tuple[str, str, dict[str, Any]]] = [
                    (
                        plan["intents"][0]["intent_id"],
                        "CYCLE_START",
                        {
                            "cycle": cycle,
                            "scheduled_at": datetime.fromtimestamp(
                                target, timezone.utc
                            ).isoformat(),
                            "started_at": cycle_started_at.isoformat(),
                            "deadline_at": datetime.fromtimestamp(
                                cycle_deadline, timezone.utc
                            ).isoformat(),
                            "lateness_ms": lateness_ms,
                        },
                    )
                ]
                regime_by_exchange: dict[str, dict[str, Any]] = {}
                if plan_uses_qta2(plan):
                    waiting_by_exchange: dict[str, list[dict[str, Any]]] = {}
                    for candidate in plan["intents"]:
                        if ledger.get(candidate["intent_id"]).state == "WAIT_TRIGGER":
                            waiting_by_exchange.setdefault(
                                candidate["exchange"],
                                [],
                            ).append(candidate)
                    for exchange in active_qta2_exchanges(plan):
                        waiting = waiting_by_exchange.get(exchange, [])
                        if not waiting:
                            continue
                        if datetime.now(timezone.utc).timestamp() >= cycle_deadline:
                            freeze_reason = "cycle_latency_budget_exceeded"
                            break
                        regime_started_at = datetime.now(timezone.utc)
                        regime_started_monotonic = time.monotonic()
                        polling_metrics["market_regime_queries"] += 1
                        polling_metrics["expected_http_requests_started"] += 1
                        try:
                            regime_quote = broker.benchmark_quote(
                                exchange=exchange,
                                session_date=trading_date,
                            )
                            regime_observed_at = datetime.now(timezone.utc)
                            regime_decision = evaluate_market_regime(
                                exchange=exchange,
                                quote=regime_quote,
                                observed_at=regime_observed_at,
                                session_date=trading_date,
                            )
                        except (BlockedError, TransportFailure) as exc:
                            regime_decision = {
                                "metric": QTA2_REGIME_METRIC,
                                "exchange": exchange,
                                "benchmark_id": V2_EXCHANGE_CONTRACTS[exchange][
                                    "benchmark_id"
                                ],
                                "minimum_change_bps": format(
                                    QTA2_REGIME_MINIMUM_CHANGE_BPS,
                                    "f",
                                ),
                                "max_age_seconds": QTA2_REGIME_MAX_AGE_SECONDS,
                                "quality_valid": False,
                                "admitted": False,
                                "reasons": ["benchmark_quote_unavailable"],
                                "error": str(exc),
                                "observed_at": datetime.now(
                                    timezone.utc
                                ).isoformat(),
                            }
                        regime_latency_ms = int(
                            (time.monotonic() - regime_started_monotonic) * 1000
                        )
                        regime_decision["latency_ms"] = regime_latency_ms
                        regime_decision["started_at"] = (
                            regime_started_at.isoformat()
                        )
                        regime_by_exchange[exchange] = regime_decision
                        polling_metrics["max_market_regime_latency_ms"] = max(
                            polling_metrics["max_market_regime_latency_ms"],
                            regime_latency_ms,
                        )
                        metric = (
                            "market_regime_passes"
                            if regime_decision["admitted"]
                            else "market_regime_blocks"
                        )
                        polling_metrics[metric] += 1
                        cycle_events.append(
                            (
                                waiting[0]["intent_id"],
                                "MARKET_REGIME_EVALUATED",
                                regime_decision,
                            )
                        )
                    if freeze_reason == "cycle_latency_budget_exceeded":
                        ledger.append_events(cycle_events)
                        break
                for intent in plan["intents"]:
                    if datetime.now(timezone.utc).timestamp() >= cycle_deadline:
                        freeze_reason = "cycle_latency_budget_exceeded"
                        break
                    record = ledger.get(intent["intent_id"])
                    venue = venue_by_intent[intent["intent_id"]]
                    if record.state == "WAIT_TRIGGER":
                        if plan_uses_qta2(plan):
                            regime_decision = regime_by_exchange.get(
                                intent["exchange"]
                            )
                            if (
                                regime_decision is None
                                or not regime_decision["admitted"]
                            ):
                                cycle_events.append(
                                    (
                                        intent["intent_id"],
                                        "ENTRY_BLOCKED_BY_MARKET_REGIME",
                                        {
                                            "exchange": intent["exchange"],
                                            "decision": regime_decision,
                                        },
                                    )
                                )
                                continue
                        if not entry_window_open(end):
                            freeze_reason = "entry_window_closed"
                            break
                        quote_started_at = datetime.now(timezone.utc)
                        quote_started_monotonic = time.monotonic()
                        polling_metrics["expected_http_requests_started"] += (
                            quote_http_request_count(broker_name, intent)
                        )
                        try:
                            quote = (
                                broker.quote(
                                    intent["market"], intent["symbol"], venue=venue
                                )
                                if broker_name != "toss"
                                else broker.quote(intent["market"], intent["symbol"])
                            )
                        except (BlockedError, TransportFailure) as exc:
                            quote_latency_ms = int(
                                (time.monotonic() - quote_started_monotonic) * 1000
                            )
                            polling_metrics["max_quote_latency_ms"] = max(
                                polling_metrics["max_quote_latency_ms"],
                                quote_latency_ms,
                            )
                            cycle_events.append(
                                (
                                    intent["intent_id"],
                                    "QUOTE_BLOCKED",
                                    {
                                        "reason": str(exc),
                                        "started_at": quote_started_at.isoformat(),
                                        "completed_at": datetime.now(
                                            timezone.utc
                                        ).isoformat(),
                                        "latency_ms": quote_latency_ms,
                                    },
                                )
                            )
                            freeze_reason = "quote_failure"
                            break
                        quote_completed_at = datetime.now(timezone.utc)
                        quote_latency_ms = int(
                            (time.monotonic() - quote_started_monotonic) * 1000
                        )
                        polling_metrics["quotes_evaluated"] += 1
                        polling_metrics["max_quote_latency_ms"] = max(
                            polling_metrics["max_quote_latency_ms"],
                            quote_latency_ms,
                        )
                        decision = evaluate_quote(
                            intent,
                            quote,
                            plan["quote_policy"],
                            quote_completed_at,
                            previous_last.get(intent["intent_id"]),
                            require_source_timestamp=True,
                        )
                        decision.update(
                            {
                                "quote_started_at": quote_started_at.isoformat(),
                                "quote_completed_at": quote_completed_at.isoformat(),
                                "quote_latency_ms": quote_latency_ms,
                            }
                        )
                        if quote_completed_at.timestamp() >= cycle_deadline:
                            decision["triggered"] = False
                            decision["reasons"].append("cycle_latency_budget_exceeded")
                            freeze_reason = "cycle_latency_budget_exceeded"
                        if decision["quality_valid"]:
                            previous_last[intent["intent_id"]] = decimal_value(
                                quote["last_price"], "last_price"
                            )
                        cycle_events.append(
                            (
                                intent["intent_id"],
                                "QUOTE_EVALUATED",
                                decision,
                            )
                        )
                        if freeze_reason == "cycle_latency_budget_exceeded":
                            break
                        if not decision["triggered"]:
                            continue
                        if not entry_window_open(end):
                            freeze_reason = "entry_window_closed"
                            break
                        if mode == "shadow":
                            shadow_request = preview_by_intent[intent["intent_id"]]
                            cycle_events.append(
                                (
                                    intent["intent_id"],
                                    "SHADOW_WOULD_SUBMIT",
                                    {"request": shadow_request},
                                )
                            )
                            ledger.append_events(cycle_events)
                            cycle_events = []
                            ledger.transition(
                                intent["intent_id"],
                                "CANCELLED",
                                {"reason": "shadow_only"},
                            )
                            continue
                        submit_safety_seconds = SUBMIT_HTTP_TIMEOUT_SECONDS + float(
                            BROKER_PACING_SECONDS[broker_name]
                        )
                        submit_cutoff = end - timedelta(seconds=submit_safety_seconds)
                        if not entry_window_open(submit_cutoff):
                            ledger.append_events(cycle_events)
                            cycle_events = []
                            ledger.transition(
                                intent["intent_id"],
                                "CANCELLED",
                                {"reason": "submit_safety_cutoff_reached"},
                            )
                            freeze_reason = "submit_safety_cutoff_reached"
                            break
                        ledger.append_events(cycle_events)
                        cycle_events = []
                        ledger.transition(intent["intent_id"], "RESERVED", decision)
                        preview = preview_by_intent[intent["intent_id"]]
                        if not entry_window_open(end):
                            ledger.transition(
                                intent["intent_id"],
                                "CANCELLED",
                                {"reason": "entry_window_closed_before_submit"},
                            )
                            freeze_reason = "entry_window_closed"
                            break
                        if datetime.now(timezone.utc).timestamp() >= cycle_deadline:
                            ledger.transition(
                                intent["intent_id"],
                                "CANCELLED",
                                {"reason": "cycle_latency_budget_exceeded"},
                            )
                            freeze_reason = "cycle_latency_budget_exceeded"
                            break
                        ledger.transition(
                            intent["intent_id"],
                            "SUBMITTING",
                            {"request_hash": preview["request_hash"]},
                            request_hash=preview["request_hash"],
                        )
                        submit_started_at = datetime.now(timezone.utc)
                        submit_started_monotonic = time.monotonic()
                        polling_metrics["submits_started"] += 1
                        polling_metrics["expected_http_requests_started"] += 1
                        try:
                            ack = (
                                broker.submit(intent, deadline_at=end)
                                if broker_name == "toss"
                                else broker.submit(
                                    intent,
                                    venue=venue,
                                    deadline_at=end,
                                )
                            )
                        except AmbiguousMutationError as exc:
                            polling_metrics["max_submit_latency_ms"] = max(
                                polling_metrics["max_submit_latency_ms"],
                                int(
                                    (time.monotonic() - submit_started_monotonic) * 1000
                                ),
                            )
                            ledger.transition(
                                intent["intent_id"],
                                "UNKNOWN",
                                {
                                    "reason": str(exc),
                                    "submit_started_at": (
                                        submit_started_at.isoformat()
                                    ),
                                    "completed_at": datetime.now(
                                        timezone.utc
                                    ).isoformat(),
                                },
                            )
                            freeze_reason = "ambiguous_mutation"
                            break
                        except AuthoritativeMutationRejection as exc:
                            ledger.transition(
                                intent["intent_id"],
                                "REJECTED",
                                {
                                    "reason": str(exc),
                                    "submit_started_at": (
                                        submit_started_at.isoformat()
                                    ),
                                    "completed_at": datetime.now(
                                        timezone.utc
                                    ).isoformat(),
                                },
                            )
                            freeze_reason = "submit_rejected"
                            break
                        except (BlockedError, TransportFailure) as exc:
                            ledger.transition(
                                intent["intent_id"],
                                "UNKNOWN",
                                {
                                    "reason": str(exc),
                                    "submit_started_at": (
                                        submit_started_at.isoformat()
                                    ),
                                    "completed_at": datetime.now(
                                        timezone.utc
                                    ).isoformat(),
                                },
                            )
                            freeze_reason = "ambiguous_mutation"
                            break
                        submit_latency_ms = int(
                            (time.monotonic() - submit_started_monotonic) * 1000
                        )
                        polling_metrics["max_submit_latency_ms"] = max(
                            polling_metrics["max_submit_latency_ms"],
                            submit_latency_ms,
                        )
                        acknowledgements[intent["intent_id"]] = ack
                        ledger.transition(
                            intent["intent_id"],
                            "ACKNOWLEDGED",
                            {
                                "ack": ack,
                                "runner_submit_started_at": (
                                    submit_started_at.isoformat()
                                ),
                                "runner_ack_received_at": datetime.now(
                                    timezone.utc
                                ).isoformat(),
                                "latency_ms": submit_latency_ms,
                            },
                            broker_order_id=ack["broker_order_id"],
                        )
                        if not entry_window_open(end):
                            freeze_reason = "ack_after_entry_window"
                            break
                    elif record.state in {"ACKNOWLEDGED", "PARTIALLY_FILLED"}:
                        ack = acknowledgements.get(intent["intent_id"])
                        if ack is None:
                            for event in reversed(ledger.events(intent["intent_id"])):
                                payload = event["payload"]
                                if event["event_type"] == "STATE_ACKNOWLEDGED":
                                    ack = payload.get("ack")
                                    break
                        if not isinstance(ack, dict):
                            ledger.transition(
                                intent["intent_id"],
                                "UNKNOWN",
                                {"reason": "ack_missing_after_restart"},
                            )
                            freeze_reason = "ack_missing"
                            break
                        status_started_at = datetime.now(timezone.utc)
                        status_started_monotonic = time.monotonic()
                        polling_metrics["order_status_queries"] += 1
                        polling_metrics["expected_http_requests_started"] += 1
                        try:
                            snapshot = current_order(
                                broker,
                                broker_name,
                                intent=intent,
                                ack=ack,
                                venue=venue,
                                trading_date=trading_date,
                            )
                        except (BlockedError, TransportFailure) as exc:
                            status_latency_ms = int(
                                (time.monotonic() - status_started_monotonic) * 1000
                            )
                            polling_metrics["max_order_status_latency_ms"] = max(
                                polling_metrics["max_order_status_latency_ms"],
                                status_latency_ms,
                            )
                            cycle_events.append(
                                (
                                    intent["intent_id"],
                                    "RECONCILE_RETRY",
                                    {
                                        "reason": str(exc),
                                        "started_at": status_started_at.isoformat(),
                                        "completed_at": datetime.now(
                                            timezone.utc
                                        ).isoformat(),
                                        "latency_ms": status_latency_ms,
                                    },
                                )
                            )
                            continue
                        status_latency_ms = int(
                            (time.monotonic() - status_started_monotonic) * 1000
                        )
                        polling_metrics["max_order_status_latency_ms"] = max(
                            polling_metrics["max_order_status_latency_ms"],
                            status_latency_ms,
                        )
                        next_state = snapshot["normalized_status"]
                        try:
                            persist_entry_fill(
                                position_ledger,
                                plan=plan,
                                intent=intent,
                                snapshot=snapshot,
                                session_date=trading_date,
                            )
                        except BlockedError as exc:
                            ledger.transition(
                                intent["intent_id"],
                                "UNKNOWN",
                                {
                                    "reason": (
                                        "strategy position ledger sync failed: "
                                        f"{exc}"
                                    ),
                                    "snapshot": snapshot,
                                },
                            )
                            freeze_reason = "position_ledger_sync_failed"
                            break
                        if next_state != record.state:
                            if next_state not in {
                                "ACKNOWLEDGED",
                                "PARTIALLY_FILLED",
                                "FILLED",
                                "CANCELLED",
                                "REJECTED",
                            }:
                                ledger.transition(
                                    intent["intent_id"],
                                    "UNKNOWN",
                                    {"snapshot": snapshot},
                                )
                                freeze_reason = "unknown_broker_status"
                                break
                            ledger.transition(
                                intent["intent_id"], next_state, {"snapshot": snapshot}
                            )
                cycle_duration_ms = int(
                    (time.monotonic() - cycle_started_monotonic) * 1000
                )
                polling_metrics["cycles_completed"] += 1
                polling_metrics["max_cycle_duration_ms"] = max(
                    polling_metrics["max_cycle_duration_ms"],
                    cycle_duration_ms,
                )
                if (
                    not freeze_reason
                    and datetime.now(timezone.utc).timestamp() >= cycle_deadline
                ):
                    freeze_reason = "cycle_latency_budget_exceeded"
                cycle_events.append(
                    (
                        plan["intents"][0]["intent_id"],
                        "CYCLE_END",
                        {
                            "cycle": cycle,
                            "completed_at": datetime.now(timezone.utc).isoformat(),
                            "duration_ms": cycle_duration_ms,
                            "freeze_reason": freeze_reason,
                        },
                    )
                )
                ledger.append_events(cycle_events)
                if freeze_reason and freeze_reason != "entry_window_closed":
                    break
                cycle += 1

            for intent in plan["intents"]:
                record = ledger.get(intent["intent_id"])
                if record.state == "WAIT_TRIGGER":
                    ledger.transition(
                        intent["intent_id"],
                        "CANCELLED",
                        {
                            "reason": (
                                "entry_window_closed"
                                if freeze_reason == "entry_window_closed"
                                else str(freeze_reason or "entry_window_ended")
                            )
                        },
                    )

            if plan["order_policy"]["cancel_remainder_at_window_end"]:
                for intent in plan["intents"]:
                    record = ledger.get(intent["intent_id"])
                    if record.state not in {"ACKNOWLEDGED", "PARTIALLY_FILLED"}:
                        continue
                    ack = acknowledgements.get(intent["intent_id"])
                    if ack is None:
                        for event in reversed(ledger.events(intent["intent_id"])):
                            if event["event_type"] == "STATE_ACKNOWLEDGED":
                                ack = event["payload"].get("ack")
                                break
                    if not isinstance(ack, dict):
                        ledger.transition(
                            intent["intent_id"],
                            "UNKNOWN",
                            {"reason": "cannot_cancel_without_ack"},
                        )
                        continue
                    cancel_quantity: str | None = None
                    if broker_name in {"kis-paper", "kis-live"}:
                        try:
                            cancel_snapshot = current_order(
                                broker,
                                broker_name,
                                intent=intent,
                                ack=ack,
                                venue=venue_by_intent[intent["intent_id"]],
                                trading_date=trading_date,
                            )
                            persist_entry_fill(
                                position_ledger,
                                plan=plan,
                                intent=intent,
                                snapshot=cancel_snapshot,
                                session_date=trading_date,
                            )
                            next_state = cancel_snapshot["normalized_status"]
                            if next_state in {"FILLED", "CANCELLED"} or (
                                next_state == "REJECTED"
                                and record.state == "ACKNOWLEDGED"
                            ):
                                if next_state != record.state:
                                    ledger.transition(
                                        intent["intent_id"],
                                        next_state,
                                        {"snapshot": cancel_snapshot},
                                    )
                                continue
                            if next_state == "REJECTED":
                                raise BlockedError(
                                    "KIS partially filled order cannot regress to rejected"
                                )
                            if next_state not in {
                                "ACKNOWLEDGED",
                                "PARTIALLY_FILLED",
                            }:
                                raise BlockedError(
                                    "KIS cancel preflight returned an unknown state"
                                )
                            if (
                                next_state == "PARTIALLY_FILLED"
                                and record.state == "ACKNOWLEDGED"
                            ):
                                ledger.transition(
                                    intent["intent_id"],
                                    "PARTIALLY_FILLED",
                                    {"snapshot": cancel_snapshot},
                                )
                            cancel_quantity = require_remaining_quantity(
                                cancel_snapshot
                            )
                            ledger.append_event(
                                intent["intent_id"],
                                "CANCEL_PREFLIGHT",
                                {"snapshot": cancel_snapshot},
                            )
                        except (BlockedError, TransportFailure) as exc:
                            ledger.transition(
                                intent["intent_id"],
                                "UNKNOWN",
                                {"reason": f"cancel preflight failed: {exc}"},
                            )
                            continue
                    ledger.transition(intent["intent_id"], "CANCEL_PENDING", {})
                    try:
                        result = cancel_order(
                            broker,
                            broker_name,
                            intent=intent,
                            ack=ack,
                            venue=venue_by_intent[intent["intent_id"]],
                            quantity=cancel_quantity,
                        )
                    except (AmbiguousMutationError, TransportFailure) as exc:
                        ledger.transition(
                            intent["intent_id"],
                            "UNKNOWN",
                            {"reason": str(exc)},
                        )
                    except BlockedError as exc:
                        ledger.transition(
                            intent["intent_id"],
                            "UNKNOWN",
                            {"reason": str(exc)},
                        )
                    else:
                        ledger.append_event(
                            intent["intent_id"], "CANCEL_ACCEPTED", result
                        )
            return session_status(
                plan,
                broker_name,
                mode,
                ledger,
                started_at,
                datetime.now(timezone.utc),
                freeze_reason,
                warmup,
                polling_metrics,
            )
        finally:
            position_ledger.close()
            ledger.close()


def self_test() -> None:
    now = datetime.now(timezone.utc)
    intent = {
        "market": "US",
        "symbol": "AAPL",
        "entry_trigger": "100",
        "limit_price": "101",
    }
    policy = {
        "max_age_seconds": 5,
        "max_spread_bps": "25",
        "max_gap_bps": "100",
        "trigger_mode": "AT_OR_ABOVE",
    }
    quote = {
        "market": "US",
        "symbol": "AAPL",
        "last_price": "100.5",
        "best_ask": "100.6",
        "best_bid": "100.4",
        "received_at": now.isoformat(),
        "trade_timestamp": now.isoformat(),
        "book_timestamp": now.isoformat(),
        "raw_status": "OK",
    }
    decision = evaluate_quote(
        intent, quote, policy, now, None, require_source_timestamp=True
    )
    assert decision["triggered"]
    crossed_policy = {**policy, "trigger_mode": "CROSS_FROM_BELOW"}
    assert not evaluate_quote(
        intent, quote, crossed_policy, now, None, require_source_timestamp=True
    )["triggered"]
    assert evaluate_quote(
        intent,
        quote,
        crossed_policy,
        now,
        Decimal(99),
        require_source_timestamp=True,
    )["triggered"]
    stale_source = {
        **quote,
        "trade_timestamp": (
            now.replace(microsecond=0) - timedelta(seconds=10)
        ).isoformat(),
        "book_timestamp": (
            now.replace(microsecond=0) - timedelta(seconds=10)
        ).isoformat(),
    }
    stale_decision = evaluate_quote(
        intent,
        stale_source,
        policy,
        now,
        None,
        require_source_timestamp=True,
    )
    assert "stale_source_quote" in stale_decision["reasons"]
    assert not stale_decision["quality_valid"]
    assert not evaluate_quote(
        intent,
        quote,
        crossed_policy,
        now,
        (
            Decimal(stale_decision["last_price"])
            if stale_decision["quality_valid"]
            else None
        ),
        require_source_timestamp=True,
    )["triggered"]
    mixed_future_source = {
        **quote,
        "trade_timestamp": (now + timedelta(seconds=10)).isoformat(),
    }
    future_decision = evaluate_quote(
        intent,
        mixed_future_source,
        policy,
        now,
        None,
        require_source_timestamp=True,
    )
    assert "stale_source_quote" in future_decision["reasons"]
    missing_source = {
        **quote,
        "trade_timestamp": None,
        "book_timestamp": None,
    }
    missing_decision = evaluate_quote(
        intent,
        missing_source,
        policy,
        now,
        None,
        require_source_timestamp=True,
    )
    assert "source_timestamp_missing" in missing_decision["reasons"]
    one_missing_source = {
        **quote,
        "book_timestamp": None,
    }
    one_missing_decision = evaluate_quote(
        intent,
        one_missing_source,
        policy,
        now,
        None,
        require_source_timestamp=True,
    )
    assert "source_timestamp_missing" in one_missing_decision["reasons"]
    invalid_source = {
        **quote,
        "trade_timestamp": "not-a-timestamp",
    }
    invalid_decision = evaluate_quote(
        intent,
        invalid_source,
        policy,
        now,
        None,
        require_source_timestamp=True,
    )
    assert "trade_timestamp_invalid" in invalid_decision["reasons"]
    for raw_status in (
        "PENDING_REPLACE",
        "REPLACED",
        "CANCEL_REJECTED",
        "REPLACE_REJECTED",
    ):
        assert (
            normalize_toss_order({"status": raw_status})["normalized_status"]
            == "UNKNOWN"
        )
    try:
        validate_runtime_capabilities({"intents": [intent]}, "kis-paper")
    except BlockedError as exc:
        assert "U.S. paper sessions" in str(exc)
    else:
        raise AssertionError("KIS U.S. paper session must be blocked")
    one_intent_plan = {
        "intents": [intent],
        "entry_window": {"poll_interval_seconds": 4},
    }
    assert (
        polling_admission(one_intent_plan, "kis-paper", "paper")[
            "minimum_poll_interval_seconds"
        ]
        == 4
    )
    too_fast_plan = {
        "intents": [intent],
        "entry_window": {"poll_interval_seconds": 3},
    }
    try:
        polling_admission(too_fast_plan, "kis-paper", "paper")
    except BlockedError as exc:
        assert "poll_interval_seconds >= 4" in str(exc)
    else:
        raise AssertionError("KIS paper pacing must reject an infeasible cycle")
    four_intent_shadow_plan = {
        "intents": [intent, intent, intent, intent],
        "entry_window": {"poll_interval_seconds": 1},
    }
    assert quote_http_request_count("kis-live", intent) == 1
    assert quote_http_request_count("toss", intent) == 2
    assert (
        polling_admission(four_intent_shadow_plan, "kis-live", "shadow")[
            "minimum_poll_interval_seconds"
        ]
        == 1
    )

    class FailingWarmupBroker:
        def token(self) -> str:
            raise TransportFailure("fixture authentication outage")

    try:
        warm_broker_before_open(
            FailingWarmupBroker(),
            datetime.now(timezone.utc) + timedelta(seconds=1),
        )
    except BlockedError as exc:
        assert "pre-open broker authentication failed" in str(exc)
    else:
        raise AssertionError("pre-open transport failure must fail closed")
    assert entry_window_open(now + timedelta(seconds=1), now)
    assert not entry_window_open(now, now)
    assert require_remaining_quantity({"remaining_quantity": "2"}) == "2"
    v2_intent = {
        **intent,
        "exchange": "NASDAQ",
        "venue": "NASD",
    }
    assert venue_for({}, v2_intent) == "NASD"
    assert venue_for({"NASDAQ:AAPL": "NASD"}, v2_intent) == "NASD"
    try:
        venue_for({"US:AAPL": "NYSE"}, v2_intent)
    except BlockedError as exc:
        assert "conflicts with hashed intent venue" in str(exc)
    else:
        raise AssertionError("a venue map must not override the hashed v2 venue")
    try:
        venue_for({}, intent)
    except BlockedError as exc:
        assert "venue map missing US:AAPL" in str(exc)
    else:
        raise AssertionError("legacy intents still require an external venue map")

    class CaptureCancelBroker:
        def __init__(self) -> None:
            self.kwargs: dict[str, Any] | None = None

        def cancel(self, **kwargs: Any) -> dict[str, Any]:
            self.kwargs = kwargs
            return {"accepted": True}

    capture = CaptureCancelBroker()
    cancel_order(
        capture,
        "kis-paper",
        intent=intent,
        ack={"broker_order_id": "12345", "organization": "06000"},
        venue="NASD",
        quantity="2",
    )
    assert capture.kwargs is not None
    assert capture.kwargs["symbol"] == "AAPL"
    assert capture.kwargs["quantity"] == "2"
    print(
        canonical_json(
            {
                "self_test": "PASS",
                "spread_bps": decision["spread_bps"],
                "live_gate": "BLOCKED",
            }
        )
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command")

    preview = subparsers.add_parser("preview")
    preview.add_argument("--plan", required=True)
    preview.add_argument(
        "--broker", required=True, choices=("toss", "kis-paper", "kis-live")
    )
    preview.add_argument("--venue-map")
    preview.add_argument("--output")

    run = subparsers.add_parser("run")
    run.add_argument("--plan", required=True)
    run.add_argument(
        "--broker", required=True, choices=("toss", "kis-paper", "kis-live")
    )
    run.add_argument("--mode", required=True, choices=("paper", "shadow", "live"))
    run.add_argument("--venue-map")
    run.add_argument("--state-dir", required=True)
    run.add_argument("--output")
    run.add_argument("--max-cycles", type=int)

    subparsers.add_parser("self-test")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.command == "self-test":
        self_test()
        return 0
    if args.command not in {"preview", "run"}:
        emit_json(
            {
                "schema": SESSION_STATUS_SCHEMA,
                "status": "BLOCKED",
                "reason": "choose preview, run, or self-test",
            }
        )
        return 2
    try:
        if args.command == "run" and args.broker in {"kis-paper", "kis-live"}:
            require_kis_runtime_credentials(
                load_kis_credentials(args.broker.split("-", 1)[1])
            )
        plan = load_json_object(args.plan)
        venues = (
            normalize_venue_map(load_json_object(args.venue_map))
            if args.venue_map
            else {}
        )
        if args.command == "preview":
            output = preview_requests(plan, args.broker, venues)
        else:
            if args.max_cycles is not None and args.max_cycles <= 0:
                raise BlockedError("--max-cycles must be positive")
            output = run_session(
                plan,
                args.broker,
                args.mode,
                venues,
                Path(args.state_dir).resolve(),
                max_cycles=args.max_cycles,
            )
    except (BlockedError, OSError, ValueError, json.JSONDecodeError) as exc:
        output = {
            "schema": SESSION_STATUS_SCHEMA,
            "status": "BLOCKED",
            "reason": str(exc),
        }
        emit_json(output, args.output)
        return 2
    emit_json(output, args.output)
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""Preview or run a deterministic first-hour shadow/paper polling session."""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

from broker_adapters import (
    AmbiguousMutationError,
    KisBroker,
    TossBroker,
    TransportFailure,
)
from execution_core import (
    PLAN_SCHEMA,
    BlockedError,
    Ledger,
    SingleWriterLock,
    canonical_json,
    decimal_value,
    emit_json,
    load_json_object,
    sha256_json,
)

VENUE_SCHEMA = "qta-venue-map/v1"
SESSION_RECEIPT_SCHEMA = "qta-session-receipt/v1"


def validate_plan(plan: dict[str, Any]) -> None:
    if plan.get("schema") != PLAN_SCHEMA:
        raise BlockedError(f"plan schema must be {PLAN_SCHEMA}")
    if plan.get("plan_status") not in {"READY", "NO_ORDERS"}:
        raise BlockedError("plan_status must be READY or NO_ORDERS")
    claimed = plan.get("plan_hash")
    if not isinstance(claimed, str) or len(claimed) != 64:
        raise BlockedError("plan_hash is invalid")
    unhashed = dict(plan)
    unhashed.pop("plan_hash")
    if sha256_json(unhashed) != claimed:
        raise BlockedError("plan_hash does not match plan contents")
    if not isinstance(plan.get("intents"), list):
        raise BlockedError("plan.intents must be an array")


def normalize_venue_map(value: dict[str, Any]) -> dict[str, str]:
    if set(value) != {"schema", "venues"} or value.get("schema") != VENUE_SCHEMA:
        raise BlockedError(f"venue map schema must be {VENUE_SCHEMA}")
    venues = value["venues"]
    if not isinstance(venues, dict):
        raise BlockedError("venue map venues must be an object")
    normalized: dict[str, str] = {}
    for key, venue in venues.items():
        if not isinstance(key, str) or ":" not in key or not isinstance(venue, str):
            raise BlockedError("venue keys must be MARKET:SYMBOL strings")
        market, symbol = key.split(":", 1)
        normalized[f"{market.upper()}:{symbol.upper()}"] = venue.upper()
    return dict(sorted(normalized.items()))


def venue_for(venues: dict[str, str], intent: dict[str, Any]) -> str:
    key = f"{str(intent['market']).upper()}:{str(intent['symbol']).upper()}"
    if key not in venues:
        raise BlockedError(f"venue map missing {key}")
    return venues[key]


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
    reasons: list[str] = []
    if quote.get("raw_status") != "OK":
        reasons.append("quote_status_not_ok")
    if quote.get("market") != intent.get("market") or quote.get("symbol") != intent.get(
        "symbol"
    ):
        reasons.append("quote_instrument_mismatch")
    last = decimal_value(quote.get("last_price"), "last_price")
    ask = decimal_value(quote.get("best_ask"), "best_ask")
    bid = decimal_value(quote.get("best_bid"), "best_bid")
    if bid <= 0 or ask <= 0 or last <= 0 or ask < bid:
        reasons.append("malformed_bid_ask")
    received = parse_timestamp(quote.get("received_at"), "received_at")
    age = (
        now.astimezone(timezone.utc) - received.astimezone(timezone.utc)
    ).total_seconds()
    if age < -1 or age > int(quote_policy["max_age_seconds"]):
        reasons.append("stale_quote")
    source_timestamp_present = all(
        bool(quote.get(field))
        for field in ("trade_timestamp", "book_timestamp")
    )
    if require_source_timestamp and not source_timestamp_present:
        reasons.append("source_timestamp_missing")
    source_ages: list[float] = []
    for field in ("trade_timestamp", "book_timestamp"):
        value = quote.get(field)
        if not value:
            continue
        try:
            source_time = parse_timestamp(value, field)
        except BlockedError:
            reasons.append(f"{field}_invalid")
            continue
        source_ages.append(
            (
                now.astimezone(timezone.utc)
                - source_time.astimezone(timezone.utc)
            ).total_seconds()
        )
    source_age = max(source_ages) if source_ages else None
    if any(
        item < -1 or item > int(quote_policy["max_age_seconds"])
        for item in source_ages
    ):
        reasons.append("stale_source_quote")
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
        "reasons": reasons,
        "last_price": format(last, "f"),
        "best_ask": format(ask, "f"),
        "best_bid": format(bid, "f"),
        "spread_bps": format(spread_bps, "f"),
        "quote_age_seconds": f"{age:.6f}",
        "source_timestamp_present": source_timestamp_present,
        "source_age_seconds": None
        if source_age is None
        else f"{source_age:.6f}",
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
        "average_filled_price": execution.get("averageFilledPrice"),
        "raw": raw,
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


def create_broker(broker_name: str) -> Any:
    if broker_name == "toss":
        return TossBroker(
            client_id=require_environment("QTA_TOSS_CLIENT_ID"),
            client_secret=require_environment("QTA_TOSS_CLIENT_SECRET"),
            account_seq=int(require_environment("QTA_TOSS_ACCOUNT_SEQ")),
            access_token=os.environ.get("QTA_TOSS_ACCESS_TOKEN"),
            access_token_expires_at=os.environ.get(
                "QTA_TOSS_ACCESS_TOKEN_EXPIRES_AT"
            ),
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
    context = plan.get("context")
    if not isinstance(context, dict):
        raise BlockedError("plan.context must be an object")
    expected_broker = "toss" if broker_name == "toss" else "kis"
    if context.get("broker") != expected_broker:
        raise BlockedError("plan broker does not match selected adapter")
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


def validate_runtime_capabilities(
    plan: dict[str, Any], broker_name: str
) -> None:
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


def entry_window_open(end: datetime, now: datetime | None = None) -> bool:
    observed = now or datetime.now(timezone.utc)
    return observed.timestamp() < end.timestamp()


def require_remaining_quantity(snapshot: dict[str, Any]) -> str:
    remaining = decimal_value(
        snapshot.get("remaining_quantity"), "remaining_quantity"
    )
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


def session_receipt(
    plan: dict[str, Any],
    broker_name: str,
    mode: str,
    ledger: Ledger,
    started_at: datetime,
    stopped_at: datetime,
    freeze_reason: str | None,
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
        "schema": SESSION_RECEIPT_SCHEMA,
        "plan_hash": plan["plan_hash"],
        "broker": broker_name,
        "mode": mode,
        "started_at": started_at.isoformat(),
        "stopped_at": stopped_at.isoformat(),
        "entry_freeze": freeze_reason is not None,
        "freeze_reason": freeze_reason,
        "intents": intents,
    }
    output["receipt_hash"] = sha256_json(output)
    return output


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
        return {
            "schema": SESSION_RECEIPT_SCHEMA,
            "plan_hash": plan["plan_hash"],
            "broker": broker_name,
            "mode": mode,
            "status": "NO_ORDERS",
            "mutation_sent": False,
        }
    validate_runtime_capabilities(plan, broker_name)
    broker = create_broker(broker_name)
    start = parse_timestamp(plan["entry_window"]["start"], "entry window start")
    end = parse_timestamp(plan["entry_window"]["end"], "entry window end")
    interval = int(plan["entry_window"]["poll_interval_seconds"])
    if broker_name == "kis-paper" and interval < 3:
        raise BlockedError(
            "KIS paper quote+orderbook polling requires at least 3 seconds"
        )
    trading_date = start.date().isoformat()
    state_directory.mkdir(parents=True, exist_ok=True)
    started_at = datetime.now(timezone.utc)
    freeze_reason: str | None = None
    previous_last: dict[str, Decimal] = {}
    acknowledgements: dict[str, dict[str, Any]] = {}

    with SingleWriterLock(state_directory / "writer.lock"):
        ledger = Ledger(state_directory / "ledger.sqlite3")
        try:
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
                    cycle += 1
                    continue

                for intent in plan["intents"]:
                    record = ledger.get(intent["intent_id"])
                    venue = venue_for(venues, intent)
                    if record.state == "WAIT_TRIGGER":
                        if not entry_window_open(end):
                            freeze_reason = "entry_window_closed"
                            break
                        try:
                            quote = (
                                broker.quote(
                                    intent["market"], intent["symbol"], venue=venue
                                )
                                if broker_name != "toss"
                                else broker.quote(intent["market"], intent["symbol"])
                            )
                        except (BlockedError, TransportFailure) as exc:
                            ledger.append_event(
                                intent["intent_id"],
                                "QUOTE_BLOCKED",
                                {"reason": str(exc)},
                            )
                            freeze_reason = "quote_failure"
                            break
                        decision = evaluate_quote(
                            intent,
                            quote,
                            plan["quote_policy"],
                            datetime.now(timezone.utc),
                            previous_last.get(intent["intent_id"]),
                            require_source_timestamp=True,
                        )
                        previous_last[intent["intent_id"]] = decimal_value(
                            quote["last_price"], "last_price"
                        )
                        ledger.append_event(
                            intent["intent_id"], "QUOTE_EVALUATED", decision
                        )
                        if not decision["triggered"]:
                            continue
                        if not entry_window_open(end):
                            freeze_reason = "entry_window_closed"
                            break
                        if mode == "shadow":
                            shadow_request = (
                                broker.preview_submit(intent)
                                if broker_name == "toss"
                                else broker.preview_submit(intent, venue=venue)
                            )
                            ledger.append_event(
                                intent["intent_id"],
                                "SHADOW_WOULD_SUBMIT",
                                {"request": shadow_request},
                            )
                            ledger.transition(
                                intent["intent_id"],
                                "CANCELLED",
                                {"reason": "shadow_only"},
                            )
                            continue
                        ledger.transition(intent["intent_id"], "RESERVED", decision)
                        preview = (
                            broker.preview_submit(intent)
                            if broker_name == "toss"
                            else broker.preview_submit(intent, venue=venue)
                        )
                        if not entry_window_open(end):
                            ledger.transition(
                                intent["intent_id"],
                                "CANCELLED",
                                {"reason": "entry_window_closed_before_submit"},
                            )
                            freeze_reason = "entry_window_closed"
                            break
                        ledger.transition(
                            intent["intent_id"],
                            "SUBMITTING",
                            {"request_hash": preview["request_hash"]},
                            request_hash=preview["request_hash"],
                        )
                        try:
                            ack = (
                                broker.submit(intent)
                                if broker_name == "toss"
                                else broker.submit(intent, venue=venue)
                            )
                        except AmbiguousMutationError as exc:
                            ledger.transition(
                                intent["intent_id"],
                                "UNKNOWN",
                                {"reason": str(exc)},
                            )
                            freeze_reason = "ambiguous_mutation"
                            break
                        except BlockedError as exc:
                            ledger.transition(
                                intent["intent_id"],
                                "REJECTED",
                                {"reason": str(exc)},
                            )
                            freeze_reason = "submit_rejected"
                            break
                        acknowledgements[intent["intent_id"]] = ack
                        ledger.transition(
                            intent["intent_id"],
                            "ACKNOWLEDGED",
                            {"ack": ack},
                            broker_order_id=ack["broker_order_id"],
                        )
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
                            ledger.append_event(
                                intent["intent_id"],
                                "RECONCILE_RETRY",
                                {"reason": str(exc)},
                            )
                            continue
                        next_state = snapshot["normalized_status"]
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
                if freeze_reason and freeze_reason != "entry_window_closed":
                    break
                cycle += 1

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
                                venue=venue_for(venues, intent),
                                trading_date=trading_date,
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
                            venue=venue_for(venues, intent),
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
            return session_receipt(
                plan,
                broker_name,
                mode,
                ledger,
                started_at,
                datetime.now(timezone.utc),
                freeze_reason,
            )
        finally:
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
        "trade_timestamp": (now.replace(microsecond=0) - timedelta(seconds=10)).isoformat(),
        "book_timestamp": (now.replace(microsecond=0) - timedelta(seconds=10)).isoformat(),
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
    assert entry_window_open(now + timedelta(seconds=1), now)
    assert not entry_window_open(now, now)
    assert require_remaining_quantity({"remaining_quantity": "2"}) == "2"

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
    preview.add_argument("--venue-map", required=True)
    preview.add_argument("--output")

    run = subparsers.add_parser("run")
    run.add_argument("--plan", required=True)
    run.add_argument(
        "--broker", required=True, choices=("toss", "kis-paper", "kis-live")
    )
    run.add_argument("--mode", required=True, choices=("paper", "shadow", "live"))
    run.add_argument("--venue-map", required=True)
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
                "schema": SESSION_RECEIPT_SCHEMA,
                "status": "BLOCKED",
                "reason": "choose preview, run, or self-test",
            }
        )
        return 2
    try:
        plan = load_json_object(args.plan)
        venues = normalize_venue_map(load_json_object(args.venue_map))
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
            "schema": SESSION_RECEIPT_SCHEMA,
            "status": "BLOCKED",
            "reason": str(exc),
        }
        emit_json(output, args.output)
        return 2
    emit_json(output, args.output)
    return 0


if __name__ == "__main__":
    sys.exit(main())

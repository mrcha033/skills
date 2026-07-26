#!/usr/bin/env python3
"""Reconcile local nonterminal intents against broker order records."""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from broker_adapters import TransportFailure
from execution_core import (
    BlockedError,
    Ledger,
    SingleWriterLock,
    canonical_json,
    emit_json,
    load_json_object,
    sha256_json,
)
from run_session import (
    create_broker,
    current_order,
    normalize_venue_map,
    parse_timestamp,
    validate_plan,
    venue_for,
)

RECONCILIATION_SCHEMA = "qta-reconciliation-receipt/v1"


def latest_ack(ledger: Ledger, intent_id: str) -> dict[str, Any] | None:
    for event in reversed(ledger.events(intent_id)):
        if event["event_type"] == "STATE_ACKNOWLEDGED":
            ack = event["payload"].get("ack")
            return ack if isinstance(ack, dict) else None
    return None


def reconcile(
    plan: dict[str, Any],
    broker_name: str,
    venues: dict[str, str],
    state_directory: Path,
    *,
    broker: Any | None = None,
) -> dict[str, Any]:
    validate_plan(plan)
    state_directory.mkdir(parents=True, exist_ok=True)
    trading_date = (
        parse_timestamp(plan["entry_window"]["start"], "entry window start")
        .date()
        .isoformat()
    )
    broker = broker or create_broker(broker_name)
    changes: list[dict[str, Any]] = []
    blocked: list[dict[str, Any]] = []

    with SingleWriterLock(state_directory / "writer.lock"):
        ledger = Ledger(state_directory / "ledger.sqlite3")
        try:
            intent_by_id = {intent["intent_id"]: intent for intent in plan["intents"]}
            for record in ledger.nonterminal():
                intent = intent_by_id.get(record.intent_id)
                if intent is None:
                    raise BlockedError(
                        f"ledger intent is absent from plan: {record.intent_id}"
                    )
                if record.state in {
                    "PLANNED",
                    "WAIT_TRIGGER",
                    "RESERVED",
                    "SUBMITTING",
                }:
                    if record.state == "SUBMITTING":
                        ledger.transition(
                            record.intent_id,
                            "UNKNOWN",
                            {"reason": "restart_during_submitting"},
                        )
                        ledger.transition(record.intent_id, "RECONCILING", {})
                        ledger.transition(
                            record.intent_id,
                            "MANUAL_BLOCK",
                            {"reason": "restart_during_submitting"},
                        )
                    else:
                        ledger.transition(
                            record.intent_id,
                            "MANUAL_BLOCK",
                            {"reason": f"restart_from_{record.state.lower()}"},
                        )
                    blocked.append(
                        {
                            "intent_id": record.intent_id,
                            "reason": f"unsafe restart state {record.state}",
                        }
                    )
                    continue
                if record.state == "UNKNOWN" and not record.broker_order_id:
                    ledger.transition(record.intent_id, "RECONCILING", {})
                    ledger.transition(
                        record.intent_id,
                        "MANUAL_BLOCK",
                        {"reason": "unknown mutation without broker order id"},
                    )
                    blocked.append(
                        {
                            "intent_id": record.intent_id,
                            "reason": "unknown mutation without broker order id",
                        }
                    )
                    continue
                ack = latest_ack(ledger, record.intent_id)
                if ack is None and record.broker_order_id:
                    ack = {"broker_order_id": record.broker_order_id}
                if not isinstance(ack, dict):
                    if record.state == "UNKNOWN":
                        ledger.transition(record.intent_id, "RECONCILING", {})
                    ledger.transition(
                        record.intent_id,
                        "MANUAL_BLOCK",
                        {"reason": "broker acknowledgement unavailable"},
                    )
                    blocked.append(
                        {
                            "intent_id": record.intent_id,
                            "reason": "broker acknowledgement unavailable",
                        }
                    )
                    continue
                if record.state == "UNKNOWN":
                    ledger.transition(record.intent_id, "RECONCILING", {})
                    record = ledger.get(record.intent_id)
                try:
                    snapshot = current_order(
                        broker,
                        broker_name,
                        intent=intent,
                        ack=ack,
                        venue=venue_for(venues, intent),
                        trading_date=trading_date,
                    )
                except (BlockedError, TransportFailure) as exc:
                    ledger.append_event(
                        record.intent_id,
                        "RECONCILE_BLOCKED",
                        {"reason": str(exc)},
                    )
                    blocked.append({"intent_id": record.intent_id, "reason": str(exc)})
                    continue
                next_state = snapshot["normalized_status"]
                if next_state == "UNKNOWN":
                    if record.state != "RECONCILING":
                        ledger.transition(record.intent_id, "UNKNOWN", snapshot)
                        ledger.transition(record.intent_id, "RECONCILING", {})
                    ledger.transition(
                        record.intent_id,
                        "MANUAL_BLOCK",
                        {"snapshot": snapshot},
                    )
                    blocked.append(
                        {
                            "intent_id": record.intent_id,
                            "reason": "broker status is unknown",
                        }
                    )
                    continue
                if next_state == record.state:
                    ledger.append_event(
                        record.intent_id, "RECONCILE_NO_CHANGE", snapshot
                    )
                    continue
                if record.state == "CANCEL_PENDING" and next_state in {
                    "ACKNOWLEDGED",
                    "PARTIALLY_FILLED",
                }:
                    ledger.append_event(
                        record.intent_id, "CANCEL_STILL_PENDING", snapshot
                    )
                    continue
                ledger.transition(record.intent_id, next_state, {"snapshot": snapshot})
                changes.append(
                    {
                        "intent_id": record.intent_id,
                        "from": record.state,
                        "to": next_state,
                    }
                )
            final = [
                {
                    "intent_id": intent["intent_id"],
                    "state": ledger.get(intent["intent_id"]).state,
                    "broker_order_id": ledger.get(intent["intent_id"]).broker_order_id,
                }
                for intent in plan["intents"]
            ]
        finally:
            ledger.close()
    output = {
        "schema": RECONCILIATION_SCHEMA,
        "plan_hash": plan["plan_hash"],
        "broker": broker_name,
        "reconciled_at": datetime.now(timezone.utc).isoformat(),
        "status": "BLOCKED" if blocked else "READY",
        "changes": changes,
        "blocked": blocked,
        "intents": final,
    }
    output["receipt_hash"] = sha256_json(output)
    return output


def self_test() -> None:
    intent = {
        "intent_id": "1" * 32,
        "client_order_id": "qta-" + "1" * 28,
        "market": "US",
        "symbol": "AAPL",
        "currency": "USD",
        "side": "BUY",
        "order_type": "LIMIT",
        "time_in_force": "DAY",
        "quantity": "1",
        "entry_trigger": "100",
        "limit_price": "101",
        "stop_price": "90",
        "take_profit_price": "120",
        "initial_state": "PLANNED",
    }
    unhashed = {
        "schema": "qta-order-plan/v1",
        "plan_status": "READY",
        "execution_version": "open1h-exec-1.0.0",
        "context": {
            "broker": "kis",
            "environment": "paper",
            "account_alias": "paper",
        },
        "entry_window": {
            "start": "2026-07-27T09:30:00-04:00",
            "end": "2026-07-27T10:30:00-04:00",
            "timezone": "America/New_York",
            "poll_interval_seconds": 3,
        },
        "quote_policy": {
            "max_age_seconds": 5,
            "max_spread_bps": "25",
            "max_gap_bps": "20",
            "trigger_mode": "AT_OR_ABOVE",
        },
        "order_policy": {
            "ttl_seconds": 30,
            "allow_partial_fill": True,
            "cancel_remainder_at_window_end": True,
        },
        "settled_cash_start": "200",
        "borrowed_buying_power_excluded": "0",
        "settled_cash_unreserved": "99",
        "intents": [intent],
        "skipped": [],
    }
    plan = {**unhashed, "plan_hash": sha256_json(unhashed)}
    venues = {"US:AAPL": "NASD"}
    with tempfile.TemporaryDirectory(prefix="qta-reconcile-") as directory:
        ledger = Ledger(Path(directory) / "ledger.sqlite3")
        ledger.create_intent(plan["plan_hash"], intent)
        ledger.transition(intent["intent_id"], "WAIT_TRIGGER", {})
        ledger.transition(intent["intent_id"], "RESERVED", {})
        ledger.transition(
            intent["intent_id"],
            "SUBMITTING",
            {"request_hash": "a" * 64},
            request_hash="a" * 64,
        )
        ledger.transition(
            intent["intent_id"], "UNKNOWN", {"reason": "accepted then timeout"}
        )
        ledger.close()

        class NeverCalledBroker:
            pass

        receipt = reconcile(
            plan,
            "kis-paper",
            venues,
            Path(directory),
            broker=NeverCalledBroker(),
        )
        assert receipt["status"] == "BLOCKED"
        assert receipt["intents"][0]["state"] == "MANUAL_BLOCK"
    print(
        canonical_json(
            {
                "self_test": "PASS",
                "schema": RECONCILIATION_SCHEMA,
                "unknown_without_order_id": "MANUAL_BLOCK",
            }
        )
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan")
    parser.add_argument("--broker", choices=("toss", "kis-paper", "kis-live"))
    parser.add_argument("--venue-map")
    parser.add_argument("--state-dir")
    parser.add_argument("--output")
    parser.add_argument("--self-test", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.self_test:
        self_test()
        return 0
    missing = [
        name
        for name in ("plan", "broker", "venue_map", "state_dir")
        if not getattr(args, name)
    ]
    if missing:
        emit_json(
            {
                "schema": RECONCILIATION_SCHEMA,
                "status": "BLOCKED",
                "reason": "missing arguments: " + ", ".join(missing),
            },
            args.output,
        )
        return 2
    try:
        receipt = reconcile(
            load_json_object(args.plan),
            args.broker,
            normalize_venue_map(load_json_object(args.venue_map)),
            Path(args.state_dir).resolve(),
        )
    except (BlockedError, OSError, ValueError, json.JSONDecodeError) as exc:
        emit_json(
            {
                "schema": RECONCILIATION_SCHEMA,
                "status": "BLOCKED",
                "reason": str(exc),
            },
            args.output,
        )
        return 2
    emit_json(receipt, args.output)
    return 0 if receipt["status"] == "READY" else 2


if __name__ == "__main__":
    sys.exit(main())

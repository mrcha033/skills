#!/usr/bin/env python3
"""Regression tests for deterministic strategy-position management."""

from __future__ import annotations

import importlib.util
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "skills" / "quant-stock-polling-trader" / "scripts"
sys.path.insert(0, str(SCRIPTS))
SPEC = importlib.util.spec_from_file_location(
    "qta_position_lifecycle",
    SCRIPTS / "position_lifecycle.py",
)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError("cannot load position_lifecycle.py")
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)

import broker_adapters as BROKERS
import run_session as RUNNER


def policy() -> dict[str, object]:
    return {
        "schema": MODULE.POSITION_POLICY_SCHEMA,
        "close_liquidation_seconds_before_close": 600,
        "exit_order_ttl_seconds": 15,
        "max_exit_replacements": 3,
        "quote_max_age_seconds": 5,
        "partial_entry_action": "CANCEL_REMAINDER_MANAGE_FILLED",
        "gap_down_action": "MARKETABLE_LIMIT",
        "daily_loss_action": "ENTRY_FREEZE_LIQUIDATE",
        "overnight_residual_action": "EXIT_ONLY_NEXT_SESSION",
    }


def intent() -> dict[str, str]:
    return {
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


def open_position(quantity: str = "10") -> dict[str, object]:
    value = intent()
    value["quantity"] = quantity
    position = MODULE.position_from_intent(
        plan_hash="a" * 64,
        intent=value,
        session_date="2026-07-29",
        fx_to_krw="1",
    )
    return MODULE.record_entry_fill(
        position,
        cumulative_filled_quantity=quantity,
        average_fill_price="70000",
        session_date="2026-07-29",
    )


def quote(
    now: datetime,
    *,
    last: str,
    bid: str,
    ask: str,
) -> dict[str, str]:
    return {
        "market": "KR",
        "symbol": "005930",
        "last_price": last,
        "best_bid": bid,
        "best_ask": ask,
        "trade_timestamp": now.isoformat(),
        "book_timestamp": now.isoformat(),
        "received_at": now.isoformat(),
    }


def account_snapshot(
    *,
    strategy_quantity: str,
    open_orders: list[dict[str, str]] | None = None,
) -> dict[str, object]:
    return {
        "schema": "qta-account-snapshot/v2",
        "broker": "kis",
        "environment": "shadow",
        "account_alias": "live-main",
        "market": "KR",
        "currency": "KRW",
        "as_of": "2026-07-30T08:50:00+09:00",
        "settled_cash": "150000",
        "borrowed_buying_power": "0",
        "fx_to_krw": "1",
        "positions": [
            {
                "market": "KR",
                "exchange": "KOSPI",
                "symbol": "005930",
                "quantity": strategy_quantity,
                "market_value_krw": "700000",
            },
            {
                "market": "KR",
                "exchange": "KOSDAQ",
                "symbol": "035900",
                "quantity": "1",
                "market_value_krw": "100000",
            },
        ],
        "open_orders": open_orders or [],
    }


class PositionLifecycleTests(unittest.TestCase):
    def setUp(self) -> None:
        self.zone = timezone(timedelta(hours=9))
        self.open = datetime(2026, 7, 29, 9, 0, tzinfo=self.zone)
        self.close = datetime(2026, 7, 29, 15, 30, tzinfo=self.zone)

    def decide(
        self,
        position: dict[str, object],
        observed: datetime,
        *,
        last: str,
        bid: str,
        ask: str,
        first: bool = False,
        daily_loss: bool = False,
    ) -> dict[str, object]:
        return MODULE.exit_decision(
            position,
            quote(observed, last=last, bid=bid, ask=ask),
            policy(),
            observed_at=observed,
            regular_close=self.close,
            first_valid_quote_of_session=first,
            daily_loss_breached=daily_loss,
        )

    def test_gap_below_stop_exits_at_current_bid(self) -> None:
        decision = self.decide(
            open_position(),
            self.open,
            last="64000",
            bid="63900",
            ask="64000",
            first=True,
        )
        self.assertEqual(decision["action"], "SUBMIT_EXIT")
        self.assertEqual(decision["reason"], "STOP_GAP")
        self.assertEqual(decision["limit_price"], "63900")

    def test_stop_take_profit_and_hold_are_distinct(self) -> None:
        position = open_position()
        stop = self.decide(
            position,
            self.open + timedelta(minutes=1),
            last="65000",
            bid="65000",
            ask="65100",
        )
        target = self.decide(
            position,
            self.open + timedelta(minutes=2),
            last="80100",
            bid="80000",
            ask="80100",
        )
        hold = self.decide(
            position,
            self.open + timedelta(minutes=3),
            last="72000",
            bid="71900",
            ask="72000",
        )
        self.assertEqual(stop["reason"], "STOP")
        self.assertEqual(target["reason"], "TAKE_PROFIT")
        self.assertEqual(hold["action"], "HOLD")

    def test_close_liquidation_begins_ten_minutes_before_close(self) -> None:
        before = self.decide(
            open_position(),
            self.close - timedelta(seconds=601),
            last="72000",
            bid="71900",
            ask="72000",
        )
        at_cutoff = self.decide(
            open_position(),
            self.close - timedelta(seconds=600),
            last="72000",
            bid="71900",
            ask="72000",
        )
        self.assertEqual(before["action"], "HOLD")
        self.assertEqual(at_cutoff["reason"], "MARKET_CLOSE")

    def test_residual_after_close_is_manual_block(self) -> None:
        decision = self.decide(
            open_position(),
            self.close,
            last="72000",
            bid="71900",
            ask="72000",
        )
        self.assertEqual(decision["action"], "MANUAL_BLOCK")
        self.assertEqual(decision["reason"], "MARKET_CLOSED_WITH_RESIDUAL")
        self.assertIsNone(decision["quote"])

    def test_daily_loss_has_priority(self) -> None:
        decision = self.decide(
            open_position(),
            self.open + timedelta(minutes=1),
            last="80000",
            bid="79900",
            ask="80000",
            daily_loss=True,
        )
        self.assertEqual(decision["reason"], "DAILY_LOSS_LIMIT")

    def test_partial_entry_cancels_remainder_and_manages_fill(self) -> None:
        position = MODULE.position_from_intent(
            plan_hash="a" * 64,
            intent=intent(),
            session_date="2026-07-29",
            fx_to_krw="1",
        )
        position = MODULE.record_entry_fill(
            position,
            cumulative_filled_quantity="3",
            average_fill_price="70000",
            session_date="2026-07-29",
        )
        self.assertTrue(
            MODULE.partial_entry_requires_cancel(
                position, broker_remaining_quantity="7"
            )
        )
        self.assertEqual(MODULE.open_quantity(position), Decimal(3))

    def test_partial_exit_preserves_only_remaining_quantity(self) -> None:
        position = MODULE.record_exit_fill(
            open_position(),
            cumulative_exited_quantity="4",
            average_exit_price="68000",
        )
        self.assertEqual(position["status"], "EXIT_PARTIAL")
        self.assertEqual(MODULE.open_quantity(position), Decimal(6))

    def test_carry_rolls_to_exit_only_and_does_not_become_new_entry(self) -> None:
        partially_exited = MODULE.record_exit_fill(
            open_position(),
            cumulative_exited_quantity="2",
            average_exit_price="68000",
        )
        carried = MODULE.roll_position_session(
            partially_exited, session_date="2026-07-30"
        )
        self.assertEqual(carried["status"], "CARRY_EXIT_ONLY")
        self.assertIsNone(carried["daily_baseline_price"])
        based = MODULE.set_daily_baseline(
            carried,
            session_date="2026-07-30",
            price="69000",
        )
        with self.assertRaisesRegex(MODULE.BlockedError, "immutable"):
            MODULE.set_daily_baseline(
                based,
                session_date="2026-07-30",
                price="69100",
            )
        finished = MODULE.record_exit_fill(
            based,
            cumulative_exited_quantity="10",
            average_exit_price="69600",
        )
        self.assertEqual(finished["status"], "CLOSED")
        self.assertEqual(finished["daily_realized_pnl_native"], "8000")

    def test_loss_threshold_is_exact_and_current_150k_cannot_reach_2m(self) -> None:
        self.assertFalse(
            MODULE.daily_loss_limit_breached("150000", "2000000")
        )
        self.assertTrue(
            MODULE.daily_loss_limit_breached("2000000", "2000000")
        )

    def test_stale_or_mismatched_quote_never_triggers_exit(self) -> None:
        observed = self.open + timedelta(seconds=10)
        stale = quote(
            self.open,
            last="64000",
            bid="63900",
            ask="64000",
        )
        with self.assertRaisesRegex(MODULE.BlockedError, "stale"):
            MODULE.exit_decision(
                open_position(),
                stale,
                policy(),
                observed_at=observed,
                regular_close=self.close,
                first_valid_quote_of_session=True,
                daily_loss_breached=False,
            )

    def test_position_registry_survives_session_directories(self) -> None:
        carried = MODULE.roll_position_session(
            open_position(), session_date="2026-07-30"
        )
        with tempfile.TemporaryDirectory(prefix="qta-position-test-") as temporary:
            path = Path(temporary) / "account" / "KR" / "positions.sqlite3"
            ledger = MODULE.PositionLedger(path)
            try:
                ledger.put(
                    carried,
                    event_type="POSITION_CARRIED",
                    event_payload={"from": "2026-07-29", "to": "2026-07-30"},
                )
            finally:
                ledger.close()
            reopened = MODULE.PositionLedger(path)
            try:
                stored = reopened.open_positions()
                self.assertEqual(len(stored), 1)
                self.assertEqual(stored[0].status, "CARRY_EXIT_ONLY")
            finally:
                reopened.close()

    def test_entry_fill_snapshot_creates_and_updates_persistent_position(
        self,
    ) -> None:
        plan = {
            "plan_hash": "a" * 64,
            "frozen_inputs": {"account": {"fx_to_krw": "1"}},
        }
        with tempfile.TemporaryDirectory(prefix="qta-fill-ledger-") as temporary:
            path = Path(temporary) / "account" / "KR" / "positions.sqlite3"
            ledger = MODULE.PositionLedger(path)
            try:
                self.assertIsNone(
                    RUNNER.persist_entry_fill(
                        ledger,
                        plan=plan,
                        intent=intent(),
                        snapshot={
                            "filled_quantity": "0",
                            "average_fill_price": None,
                        },
                        session_date="2026-07-29",
                    )
                )
                self.assertEqual(ledger.open_positions(), [])
                first = RUNNER.persist_entry_fill(
                    ledger,
                    plan=plan,
                    intent=intent(),
                    snapshot={
                        "broker_order_id": "123",
                        "normalized_status": "PARTIALLY_FILLED",
                        "ordered_quantity": "10",
                        "filled_quantity": "3",
                        "average_fill_price": "70000",
                        "remaining_quantity": "7",
                    },
                    session_date="2026-07-29",
                )
                self.assertIsNotNone(first)
                self.assertEqual(first["acquired_quantity"], "3")
                position_id = first["position_id"]
                self.assertEqual(len(ledger.events(position_id)), 1)
                repeated = RUNNER.persist_entry_fill(
                    ledger,
                    plan=plan,
                    intent=intent(),
                    snapshot={
                        "broker_order_id": "123",
                        "normalized_status": "PARTIALLY_FILLED",
                        "ordered_quantity": "10",
                        "filled_quantity": "3",
                        "average_fill_price": "70000",
                        "remaining_quantity": "7",
                    },
                    session_date="2026-07-29",
                )
                self.assertEqual(repeated, first)
                self.assertEqual(len(ledger.events(position_id)), 1)
                updated = RUNNER.persist_entry_fill(
                    ledger,
                    plan=plan,
                    intent=intent(),
                    snapshot={
                        "broker_order_id": "123",
                        "normalized_status": "PARTIALLY_FILLED",
                        "ordered_quantity": "10",
                        "filled_quantity": "5",
                        "average_fill_price": "70100",
                        "remaining_quantity": "5",
                    },
                    session_date="2026-07-29",
                )
                self.assertEqual(updated["acquired_quantity"], "5")
                self.assertEqual(updated["average_entry_price"], "70100")
                self.assertEqual(len(ledger.events(position_id)), 2)
            finally:
                ledger.close()

    def test_entry_fill_without_average_price_is_blocked(self) -> None:
        plan = {
            "plan_hash": "a" * 64,
            "frozen_inputs": {"account": {"fx_to_krw": "1"}},
        }
        with tempfile.TemporaryDirectory(prefix="qta-fill-ledger-") as temporary:
            ledger = MODULE.PositionLedger(Path(temporary) / "positions.sqlite3")
            try:
                with self.assertRaisesRegex(
                    MODULE.BlockedError, "average_fill_price"
                ):
                    RUNNER.persist_entry_fill(
                        ledger,
                        plan=plan,
                        intent=intent(),
                        snapshot={
                            "filled_quantity": "1",
                            "average_fill_price": None,
                        },
                        session_date="2026-07-29",
                    )
                self.assertEqual(ledger.open_positions(), [])
            finally:
                ledger.close()

    def test_kis_order_snapshots_expose_authoritative_average_fill_price(
        self,
    ) -> None:
        broker = BROKERS.KisBroker(
            app_key="key",
            app_secret="secret",
            account_prefix="12345678",
            account_product="01",
            environment="live",
            access_token="token",
        )
        cases = (
            (
                "KR",
                {
                    "odno": "123",
                    "ord_qty": "10",
                    "tot_ccld_qty": "3",
                    "rmn_qty": "7",
                    "avg_prvs": "70000",
                },
                "70000",
            ),
            (
                "US",
                {
                    "odno": "123",
                    "ft_ord_qty": "10",
                    "ft_ccld_qty": "3",
                    "nccs_qty": "7",
                    "ft_ccld_unpr3": "201.25",
                },
                "201.25",
            ),
        )
        for market, record, expected in cases:
            broker.order_history = lambda **_kwargs: [record]
            snapshot = broker.get_order(
                market=market,
                trading_date="2026-07-29",
                symbol="005930" if market == "KR" else "AAPL",
                venue="KRX" if market == "KR" else "NASD",
                broker_order_id="123",
            )
            self.assertEqual(snapshot["average_fill_price"], expected)
            self.assertEqual(snapshot["normalized_status"], "PARTIALLY_FILLED")

    def test_account_reconciliation_rolls_exact_carry_and_lists_manual_holdings(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory(prefix="qta-reconcile-") as temporary:
            ledger = MODULE.PositionLedger(Path(temporary) / "positions.sqlite3")
            try:
                position = open_position()
                ledger.put(
                    position,
                    event_type="ENTRY_FILL_CREATED",
                    event_payload={"filled_quantity": "10"},
                )
                receipt = MODULE.reconcile_account_positions(
                    ledger,
                    account_snapshot=account_snapshot(strategy_quantity="10"),
                    session_date="2026-07-30",
                )
                self.assertEqual(receipt["status"], "READY")
                self.assertEqual(
                    receipt["managed_positions"][0]["state"],
                    "CARRY_EXIT_ONLY",
                )
                self.assertEqual(
                    receipt["unmanaged_broker_positions"],
                    [
                        {
                            "exchange": "KOSDAQ",
                            "symbol": "035900",
                            "quantity": "1",
                        }
                    ],
                )
                self.assertEqual(receipt["api_mutation_count"], 0)
                self.assertFalse(receipt["live_enabled"])
            finally:
                ledger.close()

    def test_account_quantity_mismatch_becomes_manual_block(self) -> None:
        with tempfile.TemporaryDirectory(prefix="qta-reconcile-") as temporary:
            ledger = MODULE.PositionLedger(Path(temporary) / "positions.sqlite3")
            try:
                position = open_position()
                ledger.put(
                    position,
                    event_type="ENTRY_FILL_CREATED",
                    event_payload={"filled_quantity": "10"},
                )
                receipt = MODULE.reconcile_account_positions(
                    ledger,
                    account_snapshot=account_snapshot(strategy_quantity="9"),
                    session_date="2026-07-30",
                )
                self.assertEqual(receipt["status"], "MANUAL_BLOCK")
                self.assertIn(
                    "broker_quantity_differs_from_strategy_ledger",
                    receipt["managed_positions"][0]["reasons"],
                )
                self.assertEqual(
                    ledger.open_positions()[0].status,
                    "MANUAL_BLOCK",
                )
            finally:
                ledger.close()


if __name__ == "__main__":
    unittest.main()

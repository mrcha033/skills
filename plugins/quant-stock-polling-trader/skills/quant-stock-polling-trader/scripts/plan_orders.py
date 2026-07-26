#!/usr/bin/env python3
"""Create a side-effect-free deterministic order plan from frozen JSON inputs."""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path
from typing import Any

from execution_core import (
    ACCOUNT_SCHEMA,
    EXECUTION_POLICY_SCHEMA,
    EXPOSURE_SCHEMA,
    PLAN_SCHEMA,
    RISK_SCHEMA,
    SCREEN_SCHEMA,
    BlockedError,
    canonical_json,
    canonical_screen_hash,
    emit_json,
    load_json_object,
    market_session_from_source,
    plan_orders,
)


def build_from_paths(args: argparse.Namespace) -> dict[str, Any]:
    return plan_orders(
        load_json_object(args.screen),
        load_json_object(args.account),
        load_json_object(args.exposure),
        load_json_object(args.risk),
        load_json_object(args.execution),
    )


def fixture_inputs() -> tuple[dict[str, Any], ...]:
    qta = {
        "source_skill": "quant-stock-technical",
        "result_schema": "quant-stock-technical/v1",
        "calculation_status": "READY",
        "setup_status": "READY",
        "method_version": "qta-1.0.0",
        "ticker": "005930",
        "entry_price": 70000,
        "stop_price": 65000,
        "take_profit_price": 80000,
    }
    screen = {
        "schema": SCREEN_SCHEMA,
        "screen_status": "READY",
        "method_version": "qta-1.0.0",
        "selector_version": "qta-screen-1.0.0",
        "selected": {
            "KR": [
                {
                    "rank": 1,
                    "instrument": {
                        "market": "KR",
                        "ticker": "005930",
                        "tick_size": "100",
                    },
                    "qta": qta,
                }
            ],
            "US": [],
        },
    }
    screen["screen_hash"] = canonical_screen_hash(screen)
    account = {
        "schema": ACCOUNT_SCHEMA,
        "broker": "kis",
        "environment": "paper",
        "account_alias": "paper-kr",
        "broker_account_identity_hash": (
            "5fb0e7c56b21e275d437f5fc8835ab1a8673813af62257601ffc901224d594ab"
        ),
        "market": "KR",
        "currency": "KRW",
        "as_of": "2026-07-27T08:50:00+09:00",
        "settled_cash": "200000",
        "borrowed_buying_power": "5000000",
        "fx_to_krw": "1",
        "positions": [],
        "open_orders": [],
    }
    exposure = {
        "schema": EXPOSURE_SCHEMA,
        "as_of": "2026-07-27T08:50:00+09:00",
        "positions": [],
    }
    risk = {
        "schema": RISK_SCHEMA,
        "base_currency": "KRW",
        "per_trade_risk_krw": "10000",
        "max_symbol_notional_krw": "200000",
        "max_concurrent_positions": 1,
        "max_daily_loss_krw": "20000",
        "round_trip_cost_bps": "20",
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
        "market": "KR",
        "timezone": "Asia/Seoul",
        "entry_window_start": "2026-07-27T09:00:00+09:00",
        "entry_window_end": "2026-07-27T10:00:00+09:00",
        "market_session": market_session_from_source(
            Path(__file__).resolve().parents[1]
            / "references"
            / "fixtures"
            / "kr-market-session-2026-07-27.json"
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
    return screen, account, exposure, risk, execution


def self_test() -> None:
    values = fixture_inputs()
    direct = plan_orders(*values)
    with tempfile.TemporaryDirectory(prefix="qta-plan-cli-") as directory:
        names = ("screen", "account", "exposure", "risk", "execution")
        paths: list[str] = []
        for name, value in zip(names, values):
            path = Path(directory) / f"{name}.json"
            path.write_text(canonical_json(value), encoding="utf-8")
            paths.append(str(path))
        args = argparse.Namespace(
            screen=paths[0],
            account=paths[1],
            exposure=paths[2],
            risk=paths[3],
            execution=paths[4],
        )
        loaded = build_from_paths(args)
    assert direct == loaded
    assert direct["plan_status"] == "READY"
    assert direct["borrowed_buying_power_excluded"] == "5000000"
    assert direct["intents"][0]["symbol"] == "005930"
    print(
        canonical_json(
            {
                "self_test": "PASS",
                "schema": PLAN_SCHEMA,
                "plan_hash": direct["plan_hash"],
            }
        )
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--screen")
    parser.add_argument("--account")
    parser.add_argument("--exposure")
    parser.add_argument("--risk")
    parser.add_argument("--execution")
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
        for name in ("screen", "account", "exposure", "risk", "execution")
        if not getattr(args, name)
    ]
    if missing:
        emit_json(
            {
                "schema": PLAN_SCHEMA,
                "plan_status": "BLOCKED",
                "reason": "missing arguments: " + ", ".join(missing),
            },
            args.output,
        )
        return 2
    try:
        plan = build_from_paths(args)
    except (BlockedError, OSError, ValueError, json.JSONDecodeError) as exc:
        emit_json(
            {
                "schema": PLAN_SCHEMA,
                "plan_status": "BLOCKED",
                "reason": str(exc),
            },
            args.output,
        )
        return 2
    emit_json(plan, args.output)
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""Regression suite for deterministic screening and broker execution helpers."""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
EXECUTION_SCRIPTS = ROOT / "skills" / "quant-stock-polling-trader" / "scripts"
TECHNICAL_SCRIPTS = ROOT / "skills" / "quant-stock-technical" / "scripts"
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


def run(*arguments: str) -> None:
    result = subprocess.run(
        [sys.executable, "-B", *arguments],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, (
        f"{' '.join(arguments)} failed\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    assert '"self_test"' in result.stdout or '"self_test": "PASS"' in result.stdout


def v2_instrument(
    exchange: str,
    canonical_symbol: str,
    data_symbol: str,
    broker_symbol: str,
) -> dict[str, object]:
    contracts = {
        "KOSPI": ("KR", "KOSPI_COMPOSITE", "KRW", "KRX"),
        "KOSDAQ": ("KR", "KOSDAQ_COMPOSITE", "KRW", "KRX"),
        "NYSE": ("US", "NYSE_COMPOSITE", "USD", "NYSE"),
        "NASDAQ": ("US", "NASDAQ_COMPOSITE", "USD", "NASD"),
    }
    market, benchmark_id, currency, venue = contracts[exchange]
    return {
        "market": market,
        "exchange": exchange,
        "canonical_symbol": canonical_symbol,
        "data_symbol": data_symbol,
        "broker_symbol": broker_symbol,
        "instrument_type": "COMMON",
        "benchmark_id": benchmark_id,
        "currency": currency,
        "venue": venue,
        "ticker_csv": f"/fixture/data/{data_symbol}.csv",
        "benchmark_csv": f"/fixture/data/{benchmark_id}.csv",
        "tick_contract": {
            "schema": "qta-tick-contract/v1",
            "kind": "RESOLVED_PRICE_LADDER",
            "rule_id": f"{exchange.lower()}-tick",
            "effective_date": "2026-07-24",
            "reference_price": "100",
            "resolved_tick_size": "0.01" if market == "US" else "100",
        },
        "source_name": "regression-fixture",
        "ticker_csv_sha256": "a" * 64,
        "benchmark_csv_sha256": "b" * 64,
        "broker_tradability_verified": True,
        "official_source_id": f"official:{exchange}",
        "broker_source_id": f"kis:{exchange}",
    }


def opinion(score: float) -> str:
    if score >= 80:
        return "강한 긍정"
    if score >= 60:
        return "긍정"
    if score >= 40:
        return "중립"
    if score >= 20:
        return "부정"
    return "강한 부정"


def v2_qta(
    market: str,
    canonical_symbol: str,
    *,
    total_score: float = 70.0,
    short_score: float = 70.0,
    medium_score: float = 70.0,
    long_score: float = 70.0,
    risk_score: float = 30.0,
) -> dict[str, object]:
    entry_price, stop_price, take_profit_price = (
        (70000.0, 65000.0, 80000.0) if market == "KR" else (100.0, 90.0, 120.0)
    )
    return {
        "source_skill": "quant-stock-technical",
        "result_schema": "quant-stock-technical/v1",
        "calculation_status": "READY",
        "setup_status": "READY" if total_score >= 60 else "CONDITIONAL",
        "method_version": "qta-1.0.0",
        "analysis_date": "2026-07-24",
        "market": market,
        "ticker": canonical_symbol,
        "source_name": "regression-fixture",
        "shared_sessions": 1000,
        "score_basis": (
            "ticker-relative historical percentile; not probability of profit"
        ),
        "short": {"opinion": opinion(short_score), "score": short_score},
        "medium": {"opinion": opinion(medium_score), "score": medium_score},
        "long": {"opinion": opinion(long_score), "score": long_score},
        "risk": {"score": risk_score, "counterpoint": "regression fixture"},
        "entry_price": entry_price,
        "stop_price": stop_price,
        "take_profit_price": take_profit_price,
        "total_score": total_score,
        "reference_observations": {
            feature: 756 for feature in sorted(QTA_REFERENCE_FIELDS)
        },
        "assumptions": [
            "input rows are finalized completed daily sessions",
            (
                "prices are corporate-action adjusted and aligned to "
                "analysis-date raw price scale"
            ),
            "fees, tax, FX, slippage, position size, and execution are excluded",
        ],
    }


def v2_selected_item(
    exchange: str,
    exchange_rank: int,
    canonical_symbol: str,
    data_symbol: str,
    broker_symbol: str,
) -> dict[str, object]:
    market = "KR" if exchange in {"KOSPI", "KOSDAQ"} else "US"
    return {
        "exchange_rank": exchange_rank,
        "instrument": v2_instrument(
            exchange,
            canonical_symbol,
            data_symbol,
            broker_symbol,
        ),
        "qta": v2_qta(market, canonical_symbol),
    }


def rehash_screen(execution_core: object, screen: dict[str, object]) -> None:
    screen.pop("screen_hash", None)
    screen["screen_hash"] = execution_core.canonical_screen_hash(screen)


def rehash_plan(execution_core: object, plan: dict[str, object]) -> None:
    plan.pop("plan_hash", None)
    plan["plan_hash"] = execution_core.sha256_json(plan)


def complete_v2_screen(
    execution_core: object,
    selected: dict[str, list[dict[str, object]]],
) -> dict[str, object]:
    exchanges = ("KOSPI", "KOSDAQ", "NYSE", "NASDAQ")
    selector = {
        "selector_version": execution_core.V2_SELECTOR_VERSION,
        "min_total_score": "0",
        "eligible_setup_statuses": ["READY"],
        "top_k_by_exchange": {
            exchange: len(selected[exchange]) for exchange in exchanges
        },
        "min_selected_by_exchange": {exchange: 0 for exchange in exchanges},
        "max_blocked_fraction": "0",
    }
    decisions = [
        {
            "market": item["instrument"]["market"],
            "exchange": exchange,
            "canonical_symbol": item["instrument"]["canonical_symbol"],
            "instrument": deepcopy(item["instrument"]),
            "eligible": True,
            "reasons": [],
            "exchange_rank": item["exchange_rank"],
            "selected": True,
            "qta": deepcopy(item["qta"]),
        }
        for exchange in exchanges
        for item in selected[exchange]
    ]
    screen = {
        "source_skill": "quant-stock-technical",
        "schema": execution_core.SCREEN_SCHEMA_V2,
        "screen_status": "READY",
        "method_version": "qta-1.0.0",
        "selector_version": execution_core.V2_SELECTOR_VERSION,
        "analysis_date": "2026-07-24",
        "manifest_hash": "c" * 64,
        "selector_hash": execution_core.sha256_json(selector),
        "blocked_count": 0,
        "blocked_fraction": "0",
        "instrument_count": len(decisions),
        "selector": selector,
        "selected": selected,
        "decisions": decisions,
    }
    rehash_screen(execution_core, screen)
    return screen


def main() -> None:
    run("skills/quant-stock-technical/scripts/analyze_stock.py", "--self-test")
    run("skills/quant-stock-technical/scripts/screen_universe.py", "--self-test")
    run(
        "skills/quant-stock-polling-trader/scripts/execution_core.py",
        "--self-test",
    )
    run(
        "skills/quant-stock-polling-trader/scripts/freeze_market_session.py",
        "--self-test",
    )
    run(
        "skills/quant-stock-polling-trader/scripts/broker_adapters.py",
        "--self-test",
    )
    run("skills/quant-stock-polling-trader/scripts/plan_orders.py", "--self-test")
    run("skills/quant-stock-polling-trader/scripts/run_session.py", "self-test")
    run(
        "skills/quant-stock-polling-trader/scripts/reconcile.py",
        "--self-test",
    )
    sys.path.insert(0, str(TECHNICAL_SCRIPTS))
    import screen_universe

    sys.path.insert(0, str(EXECUTION_SCRIPTS))
    import execution_core
    import plan_orders
    import run_session

    def assert_blocked(callable_object: object, expected: str) -> None:
        try:
            callable_object()
        except execution_core.BlockedError as exc:
            assert expected in str(exc), str(exc)
        else:
            raise AssertionError(f"expected BlockedError containing {expected!r}")

    assert execution_core.normalize_symbol("KR", "5930") == "005930"
    assert execution_core.normalize_symbol("KR", "0156t0") == "0156T0"
    assert "PARTIALLY_FILLED" not in execution_core.TRANSITIONS["CANCEL_PENDING"]
    redacted_identity = execution_core.redact(
        {
            "account_seq": "7",
            "accountSeq": "7",
            "account_prefix": "12345678",
            "account_product": "01",
            "ACNT_PRDT_CD": "01",
        }
    )
    assert set(redacted_identity.values()) == {"[REDACTED]"}
    assert_blocked(
        lambda: execution_core.normalize_symbol("KR", "156T0"),
        "invalid KR symbol",
    )
    legacy_inputs = list(plan_orders.fixture_inputs())
    rehash_screen(execution_core, legacy_inputs[0])
    plan = execution_core.plan_orders(*legacy_inputs)
    assert "exchange" not in plan["intents"][0]
    assert (
        plan["context"]["broker_account_identity_hash"]
        == legacy_inputs[1]["broker_account_identity_hash"]
    )
    legacy_account_without_identity = deepcopy(legacy_inputs[1])
    legacy_account_without_identity.pop("broker_account_identity_hash")
    assert_blocked(
        lambda: execution_core.plan_orders(
            legacy_inputs[0],
            legacy_account_without_identity,
            *legacy_inputs[2:],
        ),
        "account snapshot fields mismatch",
    )
    old_plan_without_identity = deepcopy(plan)
    old_plan_without_identity["context"].pop("broker_account_identity_hash")
    old_plan_without_identity["frozen_inputs"]["account"].pop(
        "broker_account_identity_hash"
    )
    rehash_plan(execution_core, old_plan_without_identity)
    assert_blocked(
        lambda: run_session.validate_plan(old_plan_without_identity),
        "plan.context fields mismatch",
    )
    fixture_identity_environment = {
        "QTA_ACCOUNT_BINDING_KEY": "fixture-account-binding-key-0001",
        "QTA_KIS_ACCOUNT_PREFIX": "00000000",
        "QTA_KIS_ACCOUNT_PRODUCT": "01",
    }
    with patch.dict(os.environ, fixture_identity_environment, clear=True):
        identity_receipt = run_session.account_identity_hash_receipt(
            "kis-paper",
            "paper",
        )
        assert (
            identity_receipt["broker_account_identity_hash"]
            == plan["context"]["broker_account_identity_hash"]
        )
        assert "00000000" not in execution_core.canonical_json(identity_receipt)
        arm = run_session.create_trading_arm(plan, "kis-paper", "paper")
        run_session.validate_account_authorization(
            plan,
            "kis-paper",
            "paper",
            arm,
        )
        assert "00000000" not in execution_core.canonical_json(plan)
        assert "00000000" not in execution_core.canonical_json(arm)
    with patch.dict(
        os.environ,
        {
            **fixture_identity_environment,
            "QTA_KIS_ACCOUNT_PREFIX": "99999999",
        },
        clear=True,
    ):
        assert_blocked(
            lambda: run_session.validate_account_authorization(
                plan,
                "kis-paper",
                "paper",
                arm,
            ),
            "runtime broker account identity does not match",
        )
    with patch.dict(
        os.environ,
        {
            **fixture_identity_environment,
            "QTA_KIS_ACCOUNT_PRODUCT": "02",
        },
        clear=True,
    ):
        assert_blocked(
            lambda: run_session.validate_account_authorization(
                plan,
                "kis-paper",
                "paper",
                arm,
            ),
            "runtime broker account identity does not match",
        )
    runtime_plan = deepcopy(plan)
    runtime_now = datetime.now(timezone.utc)
    runtime_plan["entry_window"]["start"] = (
        runtime_now - timedelta(milliseconds=100)
    ).isoformat()
    runtime_plan["entry_window"]["end"] = (
        runtime_now + timedelta(seconds=8)
    ).isoformat()
    runtime_plan["entry_window"]["poll_interval_seconds"] = 4
    assert_blocked(
        lambda: run_session.preflight_venues(runtime_plan, {}),
        "venue map missing KR:005930",
    )

    class ShadowQuoteBroker:
        def token(self) -> str:
            return "not-logged"

        def preview_submit(
            self,
            intent: dict[str, object],
            *,
            venue: str,
        ) -> dict[str, object]:
            return {
                "request_hash": "c" * 64,
                "venue": venue,
                "intent_id": intent["intent_id"],
            }

        def quote(
            self,
            market: str,
            symbol: str,
            *,
            venue: str,
        ) -> dict[str, object]:
            observed = datetime.now(timezone.utc).isoformat()
            return {
                "market": market,
                "symbol": symbol,
                "last_price": "69000",
                "best_ask": "69100",
                "best_bid": "68900",
                "received_at": observed,
                "trade_timestamp": observed,
                "book_timestamp": observed,
                "raw_status": "OK",
            }

    with (
        tempfile.TemporaryDirectory(prefix="qta-run-smoke-") as state_directory,
        patch.object(run_session, "validate_plan"),
        patch.object(run_session, "validate_mode"),
        patch.object(run_session, "validate_runtime_capabilities"),
        patch.object(run_session, "validate_account_authorization"),
        patch.object(run_session, "create_broker", return_value=ShadowQuoteBroker()),
        patch.object(
            run_session,
            "warm_broker_before_open",
            return_value={
                "scheduled_at": runtime_now.isoformat(),
                "started_at": runtime_now.isoformat(),
                "completed_at": runtime_now.isoformat(),
                "latency_ms": 0,
                "token_material_logged": False,
            },
        ),
    ):
        runtime_receipt = run_session.run_session(
            runtime_plan,
            "kis-paper",
            "paper",
            {"KR:005930": "KRX"},
            Path(state_directory),
            arm={"arm_hash": "a" * 64},
            max_cycles=1,
        )
    assert runtime_receipt["freeze_reason"] == "max_cycles_reached"
    assert runtime_receipt["polling_metrics"]["cycles_started"] == 1
    assert runtime_receipt["polling_metrics"]["cycles_completed"] == 1
    assert runtime_receipt["polling_metrics"]["quotes_evaluated"] == 1
    assert runtime_receipt["polling_metrics"]["expected_http_requests_started"] == 2
    assert runtime_receipt["arm_hash"] == "a" * 64
    with tempfile.TemporaryDirectory(prefix="qta-cross-plan-") as state_directory:
        foreign_ledger = execution_core.Ledger(Path(state_directory) / "ledger.sqlite3")
        foreign_ledger.create_intent("b" * 64, {"intent_id": "old-intent"})
        foreign_ledger.transition("old-intent", "WAIT_TRIGGER", {})
        foreign_ledger.transition("old-intent", "RESERVED", {})
        foreign_ledger.transition(
            "old-intent",
            "SUBMITTING",
            {"request_hash": "e" * 64},
            request_hash="e" * 64,
        )
        foreign_ledger.transition("old-intent", "UNKNOWN", {})
        foreign_ledger.transition("old-intent", "RECONCILING", {})
        foreign_ledger.transition("old-intent", "MANUAL_BLOCK", {})
        foreign_ledger.close()

        class NeverQuoteBroker:
            def token(self) -> str:
                raise AssertionError("foreign-plan block must precede warm-up")

            def preview_submit(
                self,
                intent: dict[str, object],
                *,
                venue: str,
            ) -> dict[str, object]:
                return {
                    "request_hash": "d" * 64,
                    "venue": venue,
                    "intent_id": intent["intent_id"],
                }

            def quote(self, *_: object, **__: object) -> dict[str, object]:
                raise AssertionError("foreign-plan block must precede quote")

        with (
            patch.object(run_session, "validate_plan"),
            patch.object(run_session, "validate_mode"),
            patch.object(run_session, "validate_runtime_capabilities"),
            patch.object(run_session, "validate_account_authorization"),
            patch.object(
                run_session,
                "create_broker",
                return_value=NeverQuoteBroker(),
            ),
        ):
            assert_blocked(
                lambda: run_session.run_session(
                    runtime_plan,
                    "kis-paper",
                    "paper",
                    {"KR:005930": "KRX"},
                    Path(state_directory),
                    arm={"arm_hash": "a" * 64},
                    max_cycles=1,
                ),
                "nonterminal intents outside the current plan",
            )
    with tempfile.TemporaryDirectory(prefix="qta-manual-block-") as state_directory:
        blocked_ledger = execution_core.Ledger(Path(state_directory) / "ledger.sqlite3")
        blocked_intent = runtime_plan["intents"][0]
        blocked_ledger.create_intent(runtime_plan["plan_hash"], blocked_intent)
        blocked_ledger.transition(blocked_intent["intent_id"], "WAIT_TRIGGER", {})
        blocked_ledger.transition(blocked_intent["intent_id"], "RESERVED", {})
        blocked_ledger.transition(
            blocked_intent["intent_id"],
            "SUBMITTING",
            {"request_hash": "f" * 64},
            request_hash="f" * 64,
        )
        blocked_ledger.transition(blocked_intent["intent_id"], "UNKNOWN", {})
        blocked_ledger.transition(blocked_intent["intent_id"], "RECONCILING", {})
        blocked_ledger.transition(blocked_intent["intent_id"], "MANUAL_BLOCK", {})
        assert_blocked(
            lambda: run_session.validate_ledger_scope(
                blocked_ledger,
                runtime_plan,
            ),
            "unresolved mutation states",
        )
        blocked_ledger.close()
    assert_blocked(
        lambda: run_session.validate_account_authorization(
            plan,
            "kis-paper",
            "paper",
            None,
        ),
        "trading arm artifact is required",
    )
    tampered_arm = deepcopy(arm)
    tampered_arm["trading_date"] = "2026-07-28"
    assert_blocked(
        lambda: run_session.validate_account_authorization(
            plan,
            "kis-paper",
            "paper",
            tampered_arm,
        ),
        "trading arm hash does not match",
    )
    toss_inputs = deepcopy(legacy_inputs)
    toss_inputs[1].update(
        {
            "broker": "toss",
            "environment": "shadow",
            "account_alias": "toss-shadow-kr",
            "broker_account_identity_hash": (
                "41572c0d88d62a56667e2f7aa5de0bb2a0e202a3946d13b5e1eb4640bdc453ae"
            ),
        }
    )
    toss_plan = execution_core.plan_orders(*toss_inputs)
    toss_identity_environment = {
        "QTA_ACCOUNT_BINDING_KEY": "fixture-account-binding-key-0001",
        "QTA_TOSS_ACCOUNT_SEQ": "1",
    }
    with patch.dict(os.environ, toss_identity_environment, clear=True):
        toss_arm = run_session.create_trading_arm(
            toss_plan,
            "toss",
            "shadow",
        )
        run_session.validate_account_authorization(
            toss_plan,
            "toss",
            "shadow",
            toss_arm,
        )
    with patch.dict(
        os.environ,
        {**toss_identity_environment, "QTA_TOSS_ACCOUNT_SEQ": "2"},
        clear=True,
    ):
        assert_blocked(
            lambda: run_session.validate_account_authorization(
                toss_plan,
                "toss",
                "shadow",
                toss_arm,
            ),
            "runtime broker account identity does not match",
        )
    stale_legacy_inputs = deepcopy(legacy_inputs)
    stale_legacy_inputs[1]["as_of"] = "2026-07-27T08:00:00+09:00"
    stale_legacy_inputs[2]["as_of"] = "2026-07-27T08:00:00+09:00"
    assert_blocked(
        lambda: execution_core.plan_orders(*stale_legacy_inputs),
        "account snapshots exceed snapshot_max_age_seconds",
    )
    tampered_plan_quantity = deepcopy(plan)
    tampered_plan_quantity["intents"][0]["quantity"] = "999999"
    rehash_plan(execution_core, tampered_plan_quantity)
    assert_blocked(
        lambda: run_session.validate_plan(tampered_plan_quantity),
        "intent_hash does not match intent contents",
    )
    tampered_plan_limit = deepcopy(plan)
    tampered_plan_limit["intents"][0]["limit_price"] = "1"
    rehash_plan(execution_core, tampered_plan_limit)
    assert_blocked(
        lambda: run_session.validate_plan(tampered_plan_limit),
        "intent_hash does not match intent contents",
    )
    tampered_plan_window = deepcopy(plan)
    tampered_plan_window["entry_window"]["start"] = "2026-07-27T08:00:00+09:00"
    rehash_plan(execution_core, tampered_plan_window)
    assert_blocked(
        lambda: run_session.validate_plan(tampered_plan_window),
        "must be the first hour of the regular session",
    )
    tampered_plan_broker = deepcopy(plan)
    tampered_plan_broker["context"]["broker"] = "toss"
    rehash_plan(execution_core, tampered_plan_broker)
    assert_blocked(
        lambda: run_session.validate_plan(tampered_plan_broker),
        "intent_id does not match deterministic seed",
    )
    tampered_order_policy = deepcopy(plan)
    tampered_order_policy["order_policy"]["cancel_remainder_at_window_end"] = False
    rehash_plan(execution_core, tampered_order_policy)
    assert_blocked(
        lambda: run_session.validate_plan(tampered_order_policy),
        "does not match the embedded execution policy",
    )
    tampered_quote_policy = deepcopy(plan)
    tampered_quote_policy["quote_policy"]["max_spread_bps"] = "9999"
    rehash_plan(execution_core, tampered_quote_policy)
    assert_blocked(
        lambda: run_session.validate_plan(tampered_quote_policy),
        "does not match the embedded execution policy",
    )
    tampered_plan_status = deepcopy(plan)
    tampered_plan_status["plan_status"] = "NO_ORDERS"
    rehash_plan(execution_core, tampered_plan_status)
    assert_blocked(
        lambda: run_session.validate_plan(tampered_plan_status),
        "plan_status does not match intents",
    )
    fully_rehashed_quantity = deepcopy(plan)
    fully_rehashed_intent = fully_rehashed_quantity["intents"][0]
    fully_rehashed_intent["quantity"] = "2"
    fully_rehashed_intent["reserved_cash"] = format(
        execution_core.decimal_value(
            plan["intents"][0]["reserved_cash"],
            "reserved_cash",
        )
        * 2,
        "f",
    )
    fully_rehashed_quantity["settled_cash_unreserved"] = format(
        execution_core.decimal_value(
            fully_rehashed_quantity["settled_cash_start"],
            "settled_cash_start",
        )
        - execution_core.decimal_value(
            fully_rehashed_intent["reserved_cash"],
            "reserved_cash",
        ),
        "f",
    )
    fully_rehashed_seed = {
        "plan_seed": execution_core.sha256_json(fully_rehashed_quantity["context"]),
        "rank": fully_rehashed_intent["rank"],
        "market": fully_rehashed_intent["market"],
        "symbol": fully_rehashed_intent["symbol"],
        "side": fully_rehashed_intent["side"],
        "order_type": fully_rehashed_intent["order_type"],
        "time_in_force": fully_rehashed_intent["time_in_force"],
        "quantity": fully_rehashed_intent["quantity"],
        "limit_price": fully_rehashed_intent["limit_price"],
    }
    fully_rehashed_intent["intent_id"] = execution_core.sha256_json(
        fully_rehashed_seed
    )[:32]
    fully_rehashed_intent["client_order_id"] = (
        f"qta-{fully_rehashed_intent['intent_id'][:28]}"
    )
    fully_rehashed_intent.pop("intent_hash")
    fully_rehashed_intent["intent_hash"] = execution_core.sha256_json(
        fully_rehashed_intent
    )
    rehash_plan(execution_core, fully_rehashed_quantity)
    assert_blocked(
        lambda: run_session.validate_plan(fully_rehashed_quantity),
        "does not match deterministic rebuild from frozen_inputs",
    )
    tampered_legacy_qta = deepcopy(legacy_inputs[0])
    tampered_legacy_qta["selected"]["KR"][0]["qta"]["entry_price"] = 71000
    assert_blocked(
        lambda: execution_core.plan_orders(
            tampered_legacy_qta,
            *legacy_inputs[1:],
        ),
        "screen_hash does not match canonical screen payload",
    )
    preview = run_session.preview_requests(plan, "kis-paper", {"KR:005930": "KRX"})
    assert preview["status"] == "READY"
    assert preview["mutation_sent"] is False
    assert preview["previews"][0]["request"]["tr_id"] == "VTTC0012U"
    toss_preview = run_session.preview_requests(plan, "toss", {})
    assert toss_preview["status"] == "READY"
    assert toss_preview["account_bound"] is False
    assert len(toss_preview["previews"][0]["request"]["request_hash"]) == 64
    toss_environment = {
        "QTA_TOSS_CLIENT_ID": "test-client",
        "QTA_TOSS_CLIENT_SECRET": "test-secret",
        "QTA_TOSS_ACCOUNT_SEQ": "1",
        "QTA_TOSS_ACCESS_TOKEN": "test-token",
    }
    with patch.dict(os.environ, toss_environment, clear=True):
        try:
            run_session.create_broker("toss")
        except execution_core.BlockedError as exc:
            assert "expiration timestamp" in str(exc)
        else:
            raise AssertionError("Toss external token without expiry must be blocked")
    toss_environment["QTA_TOSS_ACCESS_TOKEN_EXPIRES_AT"] = "2099-01-01T00:00:00+00:00"
    with patch.dict(os.environ, toss_environment, clear=True):
        toss_broker = run_session.create_broker("toss")
        assert toss_broker.account_seq == 1
    try:
        run_session.validate_mode(plan, "kis-paper", "live")
    except execution_core.BlockedError:
        pass
    else:
        raise AssertionError("live promotion must remain blocked")

    _, account, _, risk, execution = plan_orders.fixture_inputs()
    account.update(
        {
            "account_alias": "paper-us-v2",
            "market": "US",
            "currency": "USD",
            "settled_cash": "5000",
            "fx_to_krw": "1",
            "as_of": "2026-07-27T09:00:00-04:00",
        }
    )
    risk.update(
        {
            "per_trade_risk_krw": "100",
            "max_symbol_notional_krw": "1000",
            "max_concurrent_positions": 3,
        }
    )
    execution.update(
        {
            "market": "US",
            "timezone": "America/New_York",
            "entry_window_start": "2026-07-27T09:30:00-04:00",
            "entry_window_end": "2026-07-27T10:30:00-04:00",
            "market_session": execution_core.market_session_from_source(
                ROOT
                / "skills"
                / "quant-stock-polling-trader"
                / "references"
                / "fixtures"
                / "us-market-session-2026-07-27.json"
            ),
        }
    )
    nyse_rank_two = v2_selected_item("NYSE", 2, "ZZZ", "ZZZ", "ZZZ")
    nyse_rank_one = v2_selected_item("NYSE", 1, "BRK.B", "BRK-B", "BRK-B")
    nasdaq_rank_one = v2_selected_item(
        "NASDAQ",
        1,
        "BRK.B",
        "BRK-B",
        "BRK-B",
    )
    screen_v2 = complete_v2_screen(
        execution_core,
        {
            "KOSPI": [],
            "KOSDAQ": [],
            "NYSE": [nyse_rank_one, nyse_rank_two],
            "NASDAQ": [nasdaq_rank_one],
        },
    )
    exposure_v2 = {
        "schema": execution_core.EXPOSURE_SCHEMA_V2,
        "as_of": "2026-07-27T09:00:00-04:00",
        "positions": [
            {
                "broker": "toss",
                "market": "US",
                "exchange": "NYSE",
                "symbol": "BRK-B",
                "quantity": "0.5",
                "market_value_krw": "70000",
            }
        ],
    }
    partial_self_hashed_screen = {
        "schema": execution_core.SCREEN_SCHEMA_V2,
        "screen_status": "READY",
        "method_version": "qta-1.0.0",
        "selector_version": execution_core.V2_SELECTOR_VERSION,
        "selected": screen_v2["selected"],
    }
    rehash_screen(execution_core, partial_self_hashed_screen)
    assert_blocked(
        lambda: execution_core.plan_orders(
            partial_self_hashed_screen,
            account,
            exposure_v2,
            risk,
            execution,
        ),
        "qta-screen/v2 fields mismatch",
    )
    minimal_qta_screen = deepcopy(screen_v2)
    minimal_qta = {
        "source_skill": "quant-stock-technical",
        "result_schema": "quant-stock-technical/v1",
        "calculation_status": "READY",
        "setup_status": "READY",
        "method_version": "qta-1.0.0",
        "market": "US",
        "ticker": "BRK.B",
        "entry_price": 100.0,
        "stop_price": 90.0,
        "take_profit_price": 120.0,
    }
    minimal_qta_screen["selected"]["NASDAQ"][0]["qta"] = deepcopy(minimal_qta)
    for decision in minimal_qta_screen["decisions"]:
        if decision["exchange"] == "NASDAQ":
            decision["qta"] = deepcopy(minimal_qta)
    rehash_screen(execution_core, minimal_qta_screen)
    assert_blocked(
        lambda: execution_core.plan_orders(
            minimal_qta_screen,
            account,
            exposure_v2,
            risk,
            execution,
        ),
        "qta fields mismatch",
    )
    mismatched_qta_date = deepcopy(screen_v2)
    mismatched_qta_date["selected"]["NASDAQ"][0]["qta"]["analysis_date"] = "2026-07-23"
    for decision in mismatched_qta_date["decisions"]:
        if decision["exchange"] == "NASDAQ":
            decision["qta"]["analysis_date"] = "2026-07-23"
    rehash_screen(execution_core, mismatched_qta_date)
    assert_blocked(
        lambda: execution_core.plan_orders(
            mismatched_qta_date,
            account,
            exposure_v2,
            risk,
            execution,
        ),
        "analysis_date must equal screen.analysis_date",
    )
    hacked_status_selector = deepcopy(screen_v2)
    hacked_status_selector["selector"]["eligible_setup_statuses"] = ["HACKED"]
    hacked_status_selector["selector_hash"] = execution_core.sha256_json(
        hacked_status_selector["selector"]
    )
    rehash_screen(execution_core, hacked_status_selector)
    assert_blocked(
        lambda: execution_core.plan_orders(
            hacked_status_selector,
            account,
            exposure_v2,
            risk,
            execution,
        ),
        "subset of READY and CONDITIONAL",
    )
    wrong_nested_score = deepcopy(screen_v2)
    wrong_nested_score["selected"]["NASDAQ"][0]["qta"]["risk"]["score"] = 101.0
    for decision in wrong_nested_score["decisions"]:
        if decision["exchange"] == "NASDAQ":
            decision["qta"]["risk"]["score"] = 101.0
    rehash_screen(execution_core, wrong_nested_score)
    assert_blocked(
        lambda: execution_core.plan_orders(
            wrong_nested_score,
            account,
            exposure_v2,
            risk,
            execution,
        ),
        "risk.score must be >= 0 and <= 100",
    )
    toss_v2_account = deepcopy(account)
    toss_v2_account["broker"] = "toss"
    assert_blocked(
        lambda: execution_core.plan_orders(
            screen_v2,
            toss_v2_account,
            exposure_v2,
            risk,
            execution,
        ),
        "requires account broker kis",
    )
    v2_plan = execution_core.plan_orders(
        screen_v2,
        account,
        exposure_v2,
        risk,
        execution,
    )
    blocked_decision_screen = deepcopy(screen_v2)
    blocked_decision_screen["selector"]["max_blocked_fraction"] = "1"
    blocked_decision_screen["selector_hash"] = execution_core.sha256_json(
        blocked_decision_screen["selector"]
    )
    blocked_decision_screen["selected"]["NASDAQ"] = []
    for decision in blocked_decision_screen["decisions"]:
        if decision["exchange"] == "NASDAQ":
            decision["qta"] = {
                "source_skill": "quant-stock-technical",
                "result_schema": "quant-stock-technical/v1",
                "calculation_status": "BLOCKED",
                "reason": "fixture data unavailable",
                "method_version": "qta-1.0.0",
                "market": "US",
                "ticker": "BRK.B",
            }
            decision["eligible"] = False
            decision["reasons"] = ["calculation_not_ready"]
            decision["exchange_rank"] = None
            decision["selected"] = False
    blocked_decision_screen["blocked_count"] = 1
    blocked_decision_screen["blocked_fraction"] = "0.3333333333333333333333333333"
    rehash_screen(execution_core, blocked_decision_screen)
    blocked_decision_plan = execution_core.plan_orders(
        blocked_decision_screen,
        account,
        exposure_v2,
        risk,
        execution,
    )
    assert [item["symbol"] for item in blocked_decision_plan["intents"]] == ["ZZZ"]
    malformed_blocked_decision = deepcopy(blocked_decision_screen)
    for decision in malformed_blocked_decision["decisions"]:
        if decision["exchange"] == "NASDAQ":
            decision["qta"]["analysis_date"] = "2026-07-24"
    rehash_screen(execution_core, malformed_blocked_decision)
    assert_blocked(
        lambda: execution_core.plan_orders(
            malformed_blocked_decision,
            account,
            exposure_v2,
            risk,
            execution,
        ),
        "qta fields mismatch",
    )
    conditional_screen_v2 = deepcopy(screen_v2)
    conditional_screen_v2["selector"]["eligible_setup_statuses"] = [
        "CONDITIONAL",
        "READY",
    ]
    conditional_screen_v2["selected"]["NASDAQ"][0]["qta"]["setup_status"] = (
        "CONDITIONAL"
    )
    conditional_screen_v2["selected"]["NASDAQ"][0]["qta"]["total_score"] = 50.0
    for horizon in ("short", "medium", "long"):
        conditional_screen_v2["selected"]["NASDAQ"][0]["qta"][horizon] = {
            "opinion": "중립",
            "score": 50.0,
        }
    for decision in conditional_screen_v2["decisions"]:
        if decision["exchange"] == "NASDAQ":
            decision["qta"]["setup_status"] = "CONDITIONAL"
            decision["qta"]["total_score"] = 50.0
            for horizon in ("short", "medium", "long"):
                decision["qta"][horizon] = {
                    "opinion": "중립",
                    "score": 50.0,
                }
    conditional_screen_v2["selector_hash"] = execution_core.sha256_json(
        conditional_screen_v2["selector"]
    )
    rehash_screen(execution_core, conditional_screen_v2)
    execution_core.plan_orders(
        conditional_screen_v2,
        account,
        exposure_v2,
        risk,
        execution,
    )
    rounded_boundary_screen = deepcopy(screen_v2)
    rounded_boundary_screen["selector"]["eligible_setup_statuses"] = [
        "CONDITIONAL",
        "READY",
    ]
    rounded_boundary_payloads = [
        rounded_boundary_screen["selected"]["NASDAQ"][0]["qta"],
        *[
            decision["qta"]
            for decision in rounded_boundary_screen["decisions"]
            if decision["exchange"] == "NASDAQ"
        ],
    ]
    for payload in rounded_boundary_payloads:
        payload["setup_status"] = "CONDITIONAL"
        payload["total_score"] = 60.0
        for horizon in ("short", "medium", "long"):
            payload[horizon] = {"opinion": "중립", "score": 60.0}
    rounded_boundary_screen["selector_hash"] = execution_core.sha256_json(
        rounded_boundary_screen["selector"]
    )
    rehash_screen(execution_core, rounded_boundary_screen)
    execution_core.plan_orders(
        rounded_boundary_screen,
        account,
        exposure_v2,
        risk,
        execution,
    )
    assert_blocked(
        lambda: run_session.validate_mode(v2_plan, "toss", "shadow"),
        "plan broker does not match selected adapter",
    )
    reversed_screen_v2 = deepcopy(screen_v2)
    reversed_screen_v2["selected"]["NYSE"].reverse()
    assert_blocked(
        lambda: execution_core.plan_orders(
            reversed_screen_v2,
            account,
            exposure_v2,
            risk,
            execution,
        ),
        "screen_hash does not match canonical screen payload",
    )
    rehash_screen(execution_core, reversed_screen_v2)
    assert_blocked(
        lambda: execution_core.plan_orders(
            reversed_screen_v2,
            account,
            exposure_v2,
            risk,
            execution,
        ),
        "ordered and contiguous",
    )
    wrong_rank_screen = deepcopy(screen_v2)
    wrong_rank_screen["selected"]["NYSE"].reverse()
    for exchange_rank, item in enumerate(
        wrong_rank_screen["selected"]["NYSE"],
        start=1,
    ):
        item["exchange_rank"] = exchange_rank
        for decision in wrong_rank_screen["decisions"]:
            if (
                decision["exchange"] == "NYSE"
                and decision["canonical_symbol"]
                == item["instrument"]["canonical_symbol"]
            ):
                decision["exchange_rank"] = exchange_rank
    rehash_screen(execution_core, wrong_rank_screen)
    assert_blocked(
        lambda: execution_core.plan_orders(
            wrong_rank_screen,
            account,
            exposure_v2,
            risk,
            execution,
        ),
        "exchange_rank does not match qta ranking_key",
    )
    assert v2_plan["context"]["exchange_order"] == ["NYSE", "NASDAQ"]
    assert v2_plan["context"]["candidate_order_contract"].startswith(
        "exchange_contract_order"
    )
    assert [
        (item["exchange"], item["exchange_rank"], item["rank"], item["symbol"])
        for item in v2_plan["intents"]
    ] == [
        ("NYSE", 2, 2, "ZZZ"),
        ("NASDAQ", 1, 3, "BRK-B"),
    ]
    assert v2_plan["intents"][1]["canonical_symbol"] == "BRK.B"
    assert v2_plan["intents"][1]["data_symbol"] == "BRK-B"
    assert v2_plan["intents"][1]["broker_symbol"] == "BRK-B"
    assert v2_plan["intents"][1]["venue"] == "NASD"
    assert v2_plan["skipped"] == [
        {
            "market": "US",
            "symbol": "BRK-B",
            "reason": "existing_exposure",
            "exchange": "NYSE",
            "exchange_rank": 1,
            "canonical_symbol": "BRK.B",
        }
    ]
    assert v2_plan["context"]["screen_hash"] == screen_v2["screen_hash"]
    assert (
        v2_plan["context"]["market_session_hash"]
        == execution["market_session"]["session_hash"]
    )
    rehashed_session_tamper = deepcopy(v2_plan)
    rehashed_session_tamper["context"]["market_session_hash"] = "0" * 64
    rehashed_session_tamper.pop("plan_hash")
    rehashed_session_tamper["plan_hash"] = execution_core.sha256_json(
        rehashed_session_tamper
    )
    assert_blocked(
        lambda: run_session.validate_plan(rehashed_session_tamper),
        "market_session_hash does not match market_session",
    )

    tampered_qta = deepcopy(screen_v2)
    tampered_qta["selected"]["NASDAQ"][0]["qta"]["entry_price"] = 101
    assert_blocked(
        lambda: execution_core.plan_orders(
            tampered_qta,
            account,
            exposure_v2,
            risk,
            execution,
        ),
        "screen_hash does not match canonical screen payload",
    )
    forged_total = deepcopy(screen_v2)
    forged_total_payloads = [
        forged_total["selected"]["NASDAQ"][0]["qta"],
        *[
            decision["qta"]
            for decision in forged_total["decisions"]
            if decision["exchange"] == "NASDAQ"
        ],
    ]
    for payload in forged_total_payloads:
        payload["total_score"] = 99.0
    rehash_screen(execution_core, forged_total)
    assert_blocked(
        lambda: execution_core.plan_orders(
            forged_total,
            account,
            exposure_v2,
            risk,
            execution,
        ),
        "total_score does not match the qta-1.0.0 score formula",
    )
    forged_opinion = deepcopy(screen_v2)
    forged_opinion_payloads = [
        forged_opinion["selected"]["NASDAQ"][0]["qta"],
        *[
            decision["qta"]
            for decision in forged_opinion["decisions"]
            if decision["exchange"] == "NASDAQ"
        ],
    ]
    for payload in forged_opinion_payloads:
        payload["short"]["opinion"] = "강한 부정"
    rehash_screen(execution_core, forged_opinion)
    assert_blocked(
        lambda: execution_core.plan_orders(
            forged_opinion,
            account,
            exposure_v2,
            risk,
            execution,
        ),
        "opinion does not match its score",
    )
    off_tick_target = deepcopy(screen_v2)
    off_tick_payloads = [
        off_tick_target["selected"]["NASDAQ"][0]["qta"],
        *[
            decision["qta"]
            for decision in off_tick_target["decisions"]
            if decision["exchange"] == "NASDAQ"
        ],
    ]
    for payload in off_tick_payloads:
        payload["take_profit_price"] = 120.005
    rehash_screen(execution_core, off_tick_target)
    assert_blocked(
        lambda: execution_core.plan_orders(
            off_tick_target,
            account,
            exposure_v2,
            risk,
            execution,
        ),
        "must align to the resolved tick",
    )
    forged_target = deepcopy(screen_v2)
    forged_target_payloads = [
        forged_target["selected"]["NASDAQ"][0]["qta"],
        *[
            decision["qta"]
            for decision in forged_target["decisions"]
            if decision["exchange"] == "NASDAQ"
        ],
    ]
    for payload in forged_target_payloads:
        payload["take_profit_price"] = 121.0
    rehash_screen(execution_core, forged_target)
    assert_blocked(
        lambda: execution_core.plan_orders(
            forged_target,
            account,
            exposure_v2,
            risk,
            execution,
        ),
        "must equal the qta-1.0.0 2R target",
    )
    tampered_metadata = deepcopy(screen_v2)
    tampered_metadata["selected"]["NASDAQ"][0]["instrument"]["source_name"] = (
        "tampered-but-structurally-valid"
    )
    assert_blocked(
        lambda: execution_core.plan_orders(
            tampered_metadata,
            account,
            exposure_v2,
            risk,
            execution,
        ),
        "screen_hash does not match canonical screen payload",
    )
    uppercase_hash = deepcopy(screen_v2)
    uppercase_hash["screen_hash"] = str(uppercase_hash["screen_hash"]).upper()
    assert_blocked(
        lambda: execution_core.plan_orders(
            uppercase_hash,
            account,
            exposure_v2,
            risk,
            execution,
        ),
        "lowercase SHA-256",
    )
    wrong_us_offset = deepcopy(execution)
    wrong_us_offset.update(
        {
            "entry_window_start": "2026-07-27T09:30:00-05:00",
            "entry_window_end": "2026-07-27T10:30:00-05:00",
        }
    )
    assert_blocked(
        lambda: execution_core.normalized_execution_policy(wrong_us_offset),
        "local clock and UTC offset must match America/New_York",
    )
    wrong_us_window = deepcopy(execution)
    wrong_us_window.update(
        {
            "entry_window_start": "2026-07-27T10:00:00-04:00",
            "entry_window_end": "2026-07-27T11:00:00-04:00",
        }
    )
    assert_blocked(
        lambda: execution_core.normalized_execution_policy(wrong_us_window),
        "US entry window must be 09:30-10:30 America/New_York",
    )
    winter_us_window = deepcopy(execution)
    winter_us_window.update(
        {
            "entry_window_start": "2026-12-07T09:30:00-05:00",
            "entry_window_end": "2026-12-07T10:30:00-05:00",
            "market_session": execution_core.market_session_from_source(
                ROOT
                / "skills"
                / "quant-stock-polling-trader"
                / "references"
                / "fixtures"
                / "us-market-session-2026-12-07.json"
            ),
        }
    )
    normalized_winter = execution_core.normalized_execution_policy(winter_us_window)
    assert normalized_winter["entry_window_start"].endswith("-05:00")
    sunday_us_window = deepcopy(execution)
    sunday_us_window.update(
        {
            "entry_window_start": "2026-07-26T09:30:00-04:00",
            "entry_window_end": "2026-07-26T10:30:00-04:00",
            "market_session": execution_core.market_session_from_source(
                ROOT
                / "skills"
                / "quant-stock-polling-trader"
                / "references"
                / "fixtures"
                / "us-market-session-2026-07-26-invalid.json"
            ),
        }
    )
    assert_blocked(
        lambda: execution_core.normalized_execution_policy(sunday_us_window),
        "session_date must be a weekday",
    )
    oversized_snapshot_age = deepcopy(execution)
    oversized_snapshot_age["snapshot_max_age_seconds"] = 3601
    assert_blocked(
        lambda: execution_core.normalized_execution_policy(oversized_snapshot_age),
        "snapshot_max_age_seconds must be <= 3600",
    )
    tampered_market_session = deepcopy(execution)
    tampered_market_session["market_session"]["source_id"] = "forged-source"
    tampered_market_session["market_session"]["session_hash"] = (
        execution_core.canonical_market_session_hash(
            tampered_market_session["market_session"]
        )
    )
    assert_blocked(
        lambda: execution_core.normalized_execution_policy(tampered_market_session),
        "do not match the hashed source snapshot",
    )
    _, _, _, _, wrong_kr_offset = plan_orders.fixture_inputs()
    wrong_kr_offset.update(
        {
            "entry_window_start": "2026-07-27T09:00:00+08:00",
            "entry_window_end": "2026-07-27T10:00:00+08:00",
        }
    )
    assert_blocked(
        lambda: execution_core.normalized_execution_policy(wrong_kr_offset),
        "local clock and UTC offset must match Asia/Seoul",
    )

    mismatched_snapshot = deepcopy(exposure_v2)
    mismatched_snapshot["as_of"] = "2026-07-27T09:00:01-04:00"
    assert_blocked(
        lambda: execution_core.plan_orders(
            screen_v2,
            account,
            mismatched_snapshot,
            risk,
            execution,
        ),
        "must share one frozen as_of instant",
    )
    stale_snapshot_account = deepcopy(account)
    stale_snapshot = deepcopy(exposure_v2)
    stale_snapshot_account["as_of"] = "2026-07-26T09:00:00-04:00"
    stale_snapshot["as_of"] = stale_snapshot_account["as_of"]
    assert_blocked(
        lambda: execution_core.plan_orders(
            screen_v2,
            stale_snapshot_account,
            stale_snapshot,
            risk,
            execution,
        ),
        "must be from the entry session local date",
    )
    late_snapshot_account = deepcopy(account)
    late_snapshot = deepcopy(exposure_v2)
    late_snapshot_account["as_of"] = "2026-07-27T09:31:00-04:00"
    late_snapshot["as_of"] = late_snapshot_account["as_of"]
    assert_blocked(
        lambda: execution_core.plan_orders(
            screen_v2,
            late_snapshot_account,
            late_snapshot,
            risk,
            execution,
        ),
        "must be frozen no later than entry_window_start",
    )
    too_old_snapshot_account = deepcopy(account)
    too_old_snapshot = deepcopy(exposure_v2)
    too_old_snapshot_account["as_of"] = "2026-07-27T08:00:00-04:00"
    too_old_snapshot["as_of"] = too_old_snapshot_account["as_of"]
    assert_blocked(
        lambda: execution_core.plan_orders(
            screen_v2,
            too_old_snapshot_account,
            too_old_snapshot,
            risk,
            execution,
        ),
        "exceed snapshot_max_age_seconds",
    )
    same_day_analysis = deepcopy(screen_v2)
    same_day_analysis["analysis_date"] = "2026-07-27"
    for exchange_items in same_day_analysis["selected"].values():
        for selected_item in exchange_items:
            selected_item["qta"]["analysis_date"] = "2026-07-27"
    for decision in same_day_analysis["decisions"]:
        if decision["qta"]["calculation_status"] == "READY":
            decision["qta"]["analysis_date"] = "2026-07-27"
    rehash_screen(execution_core, same_day_analysis)
    assert_blocked(
        lambda: execution_core.plan_orders(
            same_day_analysis,
            account,
            exposure_v2,
            risk,
            execution,
        ),
        "analysis_date must equal market_session.previous_session_date",
    )
    changed_selector = deepcopy(screen_v2)
    changed_selector["selector"]["min_total_score"] = "1"
    rehash_screen(execution_core, changed_selector)
    assert_blocked(
        lambda: execution_core.plan_orders(
            changed_selector,
            account,
            exposure_v2,
            risk,
            execution,
        ),
        "selector_hash does not match screen.selector",
    )

    account_with_open_buy = deepcopy(account)
    account_with_open_buy["open_orders"] = [
        {
            "side": "buy",
            "market": "us",
            "exchange": "nasdaq",
            "symbol": "brk-b",
        }
    ]
    open_buy_plan = execution_core.plan_orders(
        screen_v2,
        account_with_open_buy,
        exposure_v2,
        risk,
        execution,
    )
    assert [item["symbol"] for item in open_buy_plan["intents"]] == ["ZZZ"]
    assert {
        (item["exchange"], item["symbol"], item["reason"])
        for item in open_buy_plan["skipped"]
    } == {
        ("NYSE", "BRK-B", "existing_exposure"),
        ("NASDAQ", "BRK-B", "existing_open_buy_order"),
    }

    occupied_risk = deepcopy(risk)
    occupied_risk["max_concurrent_positions"] = 2
    occupied_plan = execution_core.plan_orders(
        screen_v2,
        account_with_open_buy,
        exposure_v2,
        occupied_risk,
        execution,
    )
    assert occupied_plan["intents"] == []
    assert {item["reason"] for item in occupied_plan["skipped"]} == {
        "existing_exposure",
        "existing_open_buy_order",
        "max_concurrent_positions",
    }

    duplicate_account_position = deepcopy(account)
    duplicate_account_position["positions"] = [
        {
            "market": "us",
            "exchange": "nyse",
            "symbol": "brk-b",
        }
    ]
    unique_slot_plan = execution_core.plan_orders(
        screen_v2,
        duplicate_account_position,
        exposure_v2,
        occupied_risk,
        execution,
    )
    assert [item["symbol"] for item in unique_slot_plan["intents"]] == ["ZZZ"]
    assert unique_slot_plan["skipped"][-1]["reason"] == "max_concurrent_positions"

    missing_open_order_market = deepcopy(account_with_open_buy)
    del missing_open_order_market["open_orders"][0]["market"]
    assert_blocked(
        lambda: execution_core.plan_orders(
            screen_v2,
            missing_open_order_market,
            exposure_v2,
            risk,
            execution,
        ),
        "missing fields needed for exposure key",
    )
    missing_open_order_exchange = deepcopy(account_with_open_buy)
    del missing_open_order_exchange["open_orders"][0]["exchange"]
    assert_blocked(
        lambda: execution_core.plan_orders(
            screen_v2,
            missing_open_order_exchange,
            exposure_v2,
            risk,
            execution,
        ),
        "requires exchange for qta-screen/v2",
    )

    _, kr_account, _, kr_risk, kr_execution = plan_orders.fixture_inputs()
    kr_instrument = v2_instrument(
        "KOSDAQ",
        "0156T0",
        "0156T0.KQ",
        "0156T0",
    )
    kr_selector = screen_universe.normalize_selector_v2(
        {
            "selector_version": "qta-screen-1.1.0",
            "min_total_score": "0",
            "eligible_setup_statuses": ["READY"],
            "top_k_by_exchange": {
                "KOSPI": 0,
                "KOSDAQ": 1,
                "NYSE": 0,
                "NASDAQ": 0,
            },
            "min_selected_by_exchange": {
                "KOSPI": 0,
                "KOSDAQ": 1,
                "NYSE": 0,
                "NASDAQ": 0,
            },
            "max_blocked_fraction": "0",
        }
    )
    kr_qta = v2_qta("KR", "0156T0")
    kr_screen_v2 = screen_universe.finalize_screen_v2(
        {
            "analysis_date": "2026-07-24",
            "manifest_hash": "d" * 64,
            "instruments": [kr_instrument],
        },
        kr_selector,
        [kr_qta],
    )
    kr_exposure_v2 = {
        "schema": execution_core.EXPOSURE_SCHEMA_V2,
        "as_of": "2026-07-27T08:50:00+09:00",
        "positions": [],
    }
    kr_v2_plan = execution_core.plan_orders(
        kr_screen_v2,
        kr_account,
        kr_exposure_v2,
        kr_risk,
        kr_execution,
    )
    assert kr_v2_plan["intents"][0]["symbol"] == "0156T0"
    assert kr_v2_plan["intents"][0]["exchange"] == "KOSDAQ"
    assert kr_v2_plan["intents"][0]["venue"] == "KRX"

    legacy_exposure = {
        "schema": execution_core.EXPOSURE_SCHEMA,
        "as_of": exposure_v2["as_of"],
        "positions": [],
    }
    assert_blocked(
        lambda: execution_core.plan_orders(
            screen_v2,
            account,
            legacy_exposure,
            risk,
            execution,
        ),
        "requires exposure schema qta-exposure-snapshot/v2",
    )
    invalid_keys = deepcopy(screen_v2)
    del invalid_keys["selected"]["KOSDAQ"]
    rehash_screen(execution_core, invalid_keys)
    assert_blocked(
        lambda: execution_core.plan_orders(
            invalid_keys,
            account,
            exposure_v2,
            risk,
            execution,
        ),
        "selected must contain exactly",
    )
    invalid_venue = deepcopy(screen_v2)
    invalid_venue["selected"]["NASDAQ"][0]["instrument"]["venue"] = "NASDAQ"
    rehash_screen(execution_core, invalid_venue)
    assert_blocked(
        lambda: execution_core.plan_orders(
            invalid_venue,
            account,
            exposure_v2,
            risk,
            execution,
        ),
        ".venue must be NASD",
    )
    invalid_metadata = deepcopy(screen_v2)
    invalid_metadata["selected"]["NASDAQ"][0]["instrument"]["unexpected"] = True
    rehash_screen(execution_core, invalid_metadata)
    assert_blocked(
        lambda: execution_core.plan_orders(
            invalid_metadata,
            account,
            exposure_v2,
            risk,
            execution,
        ),
        "fields mismatch",
    )
    invalid_qta_symbol = deepcopy(screen_v2)
    invalid_qta_symbol["selected"]["NASDAQ"][0]["qta"]["ticker"] = "OTHER"
    rehash_screen(execution_core, invalid_qta_symbol)
    assert_blocked(
        lambda: execution_core.plan_orders(
            invalid_qta_symbol,
            account,
            exposure_v2,
            risk,
            execution,
        ),
        "qta.ticker must equal instrument.canonical_symbol",
    )
    print("quant screening and execution regression: PASS")


if __name__ == "__main__":
    main()

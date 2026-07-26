#!/usr/bin/env python3
"""Regression suite for deterministic screening and broker execution helpers."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
EXECUTION_SCRIPTS = ROOT / "skills" / "quant-stock-polling-trader" / "scripts"


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


def main() -> None:
    run("skills/quant-stock-technical/scripts/analyze_stock.py", "--self-test")
    run("skills/quant-stock-technical/scripts/screen_universe.py", "--self-test")
    run(
        "skills/quant-stock-polling-trader/scripts/execution_core.py",
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
    sys.path.insert(0, str(EXECUTION_SCRIPTS))
    import execution_core
    import plan_orders
    import run_session

    plan = execution_core.plan_orders(*plan_orders.fixture_inputs())
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
    toss_environment["QTA_TOSS_ACCESS_TOKEN_EXPIRES_AT"] = (
        "2099-01-01T00:00:00+00:00"
    )
    with patch.dict(os.environ, toss_environment, clear=True):
        toss_broker = run_session.create_broker("toss")
        assert toss_broker.account_seq == 1
    try:
        run_session.validate_mode(plan, "kis-paper", "live")
    except execution_core.BlockedError:
        pass
    else:
        raise AssertionError("live promotion must remain blocked")
    print("quant screening and execution regression: PASS")


if __name__ == "__main__":
    main()

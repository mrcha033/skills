#!/usr/bin/env python3
"""Hash and validate one frozen market-session source JSON file."""

from __future__ import annotations

import argparse
import sys

from execution_core import (
    ENTRY_WINDOWS,
    BlockedError,
    emit_json,
    market_session_from_source,
    normalized_market_session,
)


def build(source: str) -> dict[str, object]:
    session = market_session_from_source(source)
    market = session.get("market")
    if market not in ENTRY_WINDOWS:
        raise BlockedError("market-session source market must be KR or US")
    return normalized_market_session(
        session,
        expected_market=market,
        expected_timezone=ENTRY_WINDOWS[market]["timezone"],
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source")
    parser.add_argument("--output")
    parser.add_argument("--self-test", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    source = args.source
    if args.self_test:
        from pathlib import Path

        source = str(
            Path(__file__).resolve().parents[1]
            / "references"
            / "fixtures"
            / "kr-market-session-2026-07-27.json"
        )
    if source is None:
        raise BlockedError("--source is required")
    result = build(source)
    if args.self_test:
        result = {
            "self_test": "PASS",
            "schema": result["schema"],
            "session_hash": result["session_hash"],
        }
    emit_json(result, args.output)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (BlockedError, OSError, ValueError) as exc:
        emit_json({"status": "BLOCKED", "reason": str(exc)})
        sys.exit(2)

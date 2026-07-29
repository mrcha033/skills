#!/usr/bin/env python3
"""First-hour-aligned QTA 2.0 daily setup score."""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from datetime import date
from pathlib import Path
from typing import Any

import analyze_stock as qta1


METHOD_VERSION = "qta-2.0.0"
VALIDATION_STATUS = "RESEARCH_ONLY"
LIQUIDITY_FLOOR = {
    "KR": 1_000_000_000.0,
    "US": 1_000_000.0,
}
MARKET_REGIME_POLICY = {
    "metric": "same_session_previous_close_return_bps",
    "minimum_change_bps": 0.0,
    "max_age_seconds": 90,
    "status": "REQUIRED",
}


def calculate(
    ticker_bars: list[qta1.Bar],
    benchmark_bars: list[qta1.Bar],
    market: str,
    ticker: str,
    tick_size: float,
    source_name: str,
) -> dict[str, Any]:
    """Reuse the causal QTA1 features but score the first-hour horizon."""
    market = market.upper()
    if market not in LIQUIDITY_FLOOR:
        raise qta1.BlockedError("QTA 2.0 market must be KR or US")
    result = qta1.calculate(
        ticker_bars,
        benchmark_bars,
        market,
        ticker,
        tick_size,
        source_name,
    )
    short = float(result["short"]["score"])
    medium = float(result["medium"]["score"])
    risk = float(result["risk"]["score"])
    total = min(
        100.0,
        max(
            0.0,
            0.50 * short
            + 0.30 * medium
            + 0.20 * (100.0 - risk),
        ),
    )
    median_turnover = statistics.median(
        bar.close * bar.volume for bar in ticker_bars[-20:]
    )
    minimum_turnover = LIQUIDITY_FLOOR[market]
    liquidity_ready = median_turnover >= minimum_turnover
    result.update(
        {
            "method_version": METHOD_VERSION,
            "validation_status": VALIDATION_STATUS,
            "score_basis": (
                "ticker-relative historical percentiles aligned to first-hour "
                "continuation; not probability of profit"
            ),
            "liquidity": {
                "median_20_session_turnover": round(median_turnover, 2),
                "minimum_turnover": minimum_turnover,
                "currency": "KRW" if market == "KR" else "USD",
                "status": "READY" if liquidity_ready else "BLOCKED",
            },
            "market_regime": dict(MARKET_REGIME_POLICY),
            "total_score": round(total, 2),
            "setup_status": (
                "READY"
                if total >= 60.0 and liquidity_ready
                else "CONDITIONAL"
            ),
            "assumptions": [
                *result["assumptions"],
                "QTA 2.0 is research-only until multi-session walk-forward validation",
                "same-session benchmark regime admission is required downstream",
            ],
        }
    )
    return result


def self_test() -> None:
    ticker = qta1.synthetic_bars(1100, 0.0006, 0.0)
    benchmark = qta1.synthetic_bars(1100, 0.0003, 1.0)
    result = calculate(
        ticker,
        benchmark,
        "US",
        "SYNTH",
        0.01,
        "deterministic-self-test",
    )
    expected = min(
        100.0,
        max(
            0.0,
            0.50 * result["short"]["score"]
            + 0.30 * result["medium"]["score"]
            + 0.20 * (100.0 - result["risk"]["score"]),
        ),
    )
    assert abs(result["total_score"] - round(expected, 2)) <= 0.01
    assert result["method_version"] == METHOD_VERSION
    assert result["validation_status"] == VALIDATION_STATUS
    print(
        json.dumps(
            {
                "self_test": "PASS",
                "method_version": METHOD_VERSION,
                "validation_status": VALIDATION_STATUS,
                "total_score": result["total_score"],
            },
            sort_keys=True,
        )
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ticker-csv")
    parser.add_argument("--benchmark-csv")
    parser.add_argument("--market")
    parser.add_argument("--ticker")
    parser.add_argument("--tick-size", type=float)
    parser.add_argument("--source-name", default="user-supplied-eod-csv")
    parser.add_argument("--analysis-date")
    parser.add_argument("--self-test", action="store_true")
    return parser.parse_args()


def blocked(reason: str, market: Any, ticker: Any) -> dict[str, Any]:
    return {
        "source_skill": "quant-stock-technical",
        "result_schema": "quant-stock-technical/v1",
        "calculation_status": "BLOCKED",
        "reason": reason,
        "method_version": METHOD_VERSION,
        "market": str(market or "").upper(),
        "ticker": str(ticker or "").strip().upper(),
    }


def main() -> int:
    args = parse_args()
    if args.self_test:
        self_test()
        return 0
    missing = [
        name
        for name in (
            "ticker_csv",
            "benchmark_csv",
            "market",
            "ticker",
            "tick_size",
        )
        if getattr(args, name) is None
    ]
    if missing:
        print(
            json.dumps(
                blocked(
                    "missing arguments: " + ", ".join(missing),
                    args.market,
                    args.ticker,
                ),
                indent=2,
            )
        )
        return 2
    try:
        requested = (
            date.fromisoformat(args.analysis_date)
            if args.analysis_date
            else None
        )
        ticker_rows, benchmark_rows = qta1.align(
            qta1.read_csv(str(Path(args.ticker_csv))),
            qta1.read_csv(str(Path(args.benchmark_csv))),
            requested,
        )
        result = calculate(
            ticker_rows,
            benchmark_rows,
            args.market,
            args.ticker,
            args.tick_size,
            args.source_name,
        )
    except (qta1.BlockedError, OSError, ValueError) as exc:
        print(
            json.dumps(
                blocked(str(exc), args.market, args.ticker),
                ensure_ascii=False,
                indent=2,
            )
        )
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())

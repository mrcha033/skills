#!/usr/bin/env python3
"""Run a deterministic selected-candidate opening-hour walk-forward study."""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from concurrent.futures import ProcessPoolExecutor
from datetime import date
from decimal import Decimal, InvalidOperation, ROUND_HALF_EVEN
from pathlib import Path
from typing import Any, Iterable

import analyze_stock as qta1
import analyze_stock_v2 as qta2
import collect_open1h_research as intraday
import evaluate_open1h_research as evaluation
import fetch_kis_kr_eod as shared
import screen_universe as screen


SCHEMA = "qta-selected-open1h-walk-forward/v1"
UNIVERSE_MODE = "STATIC_SOURCE_MANIFEST_SURVIVOR_BIASED"
METHODS = (qta1.METHOD_VERSION, qta2.METHOD_VERSION)
EXCHANGES = ("KOSPI", "KOSDAQ")


class WalkForwardBlockedError(ValueError):
    """Raised when a walk-forward study cannot be completed causally."""


def decimal_value(value: Any, field: str) -> Decimal:
    if isinstance(value, bool):
        raise WalkForwardBlockedError(f"{field} must be decimal")
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise WalkForwardBlockedError(f"{field} must be decimal") from exc
    if not parsed.is_finite():
        raise WalkForwardBlockedError(f"{field} must be finite")
    return parsed


def decimal_text(value: Decimal, places: str = "0.01") -> str:
    return format(
        value.quantize(Decimal(places), rounding=ROUND_HALF_EVEN),
        "f",
    )


def absolute_file(value: str, field: str) -> Path:
    path = Path(value).expanduser()
    if not path.is_absolute() or not path.is_file() or path.is_symlink():
        raise WalkForwardBlockedError(
            f"{field} must be an absolute regular non-symlink file"
        )
    return path


def absolute_directory(value: str, field: str) -> Path:
    path = Path(value).expanduser()
    if not path.is_absolute():
        raise WalkForwardBlockedError(f"{field} must be absolute")
    if path.exists() and (not path.is_dir() or path.is_symlink()):
        raise WalkForwardBlockedError(
            f"{field} must be a non-symlink directory"
        )
    return path


def load_manifest(path: Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        manifest = screen.normalize_manifest_v2(raw)
    except (OSError, json.JSONDecodeError, screen.ScreenBlockedError) as exc:
        raise WalkForwardBlockedError(
            f"source manifest validation failed: {exc}"
        ) from exc
    instruments = [
        instrument
        for instrument in manifest["instruments"]
        if instrument["market"] == "KR"
        and instrument["exchange"] in EXCHANGES
    ]
    if not instruments:
        raise WalkForwardBlockedError(
            "source manifest has no KOSPI/KOSDAQ instruments"
        )
    instruments.sort(
        key=lambda item: (
            EXCHANGES.index(item["exchange"]),
            item["canonical_symbol"],
        )
    )
    for instrument in instruments:
        ticker_path = absolute_file(
            instrument["ticker_csv"], "instrument.ticker_csv"
        )
        benchmark_path = absolute_file(
            instrument["benchmark_csv"], "instrument.benchmark_csv"
        )
        if shared.sha256_file(ticker_path) != instrument["ticker_csv_sha256"]:
            raise WalkForwardBlockedError(
                f"ticker hash mismatch: {instrument['canonical_symbol']}"
            )
        if (
            shared.sha256_file(benchmark_path)
            != instrument["benchmark_csv_sha256"]
        ):
            raise WalkForwardBlockedError(
                f"benchmark hash mismatch: {instrument['benchmark_id']}"
            )
    return manifest, instruments


def shared_session_pairs(
    instruments: list[dict[str, Any]],
    *,
    end_session: date,
    sessions: int,
) -> list[tuple[date, date]]:
    benchmark_paths = {
        exchange: next(
            Path(item["benchmark_csv"])
            for item in instruments
            if item["exchange"] == exchange
        )
        for exchange in EXCHANGES
    }
    date_sets = [
        {row.day for row in qta1.read_csv(str(path))}
        for path in benchmark_paths.values()
    ]
    common = sorted(
        day for day in set.intersection(*date_sets) if day <= end_session
    )
    if len(common) < sessions + 1:
        raise WalkForwardBlockedError(
            f"benchmarks expose only {max(0, len(common) - 1)} usable sessions"
        )
    targets = common[-sessions:]
    positions = {day: index for index, day in enumerate(common)}
    return [(common[positions[target] - 1], target) for target in targets]


def blocked_payload(
    instrument: dict[str, Any],
    reason: str,
    method_version: str,
) -> dict[str, Any]:
    return {
        "source_skill": "quant-stock-technical",
        "result_schema": "quant-stock-technical/v1",
        "calculation_status": "BLOCKED",
        "reason": reason,
        "method_version": method_version,
        "market": "KR",
        "ticker": instrument["canonical_symbol"],
    }


def analyze_pair_task(
    task: tuple[dict[str, Any], str],
) -> dict[str, Any]:
    instrument, analysis_date_text = task
    analysis_date = date.fromisoformat(analysis_date_text)
    try:
        ticker_rows, benchmark_rows = qta1.align(
            qta1.read_csv(instrument["ticker_csv"]),
            qta1.read_csv(instrument["benchmark_csv"]),
            analysis_date,
        )
        historical_tick = shared.tick_size(
            instrument["exchange"],
            Decimal(str(ticker_rows[-1].close)),
        )
        common = {
            "ticker_bars": ticker_rows,
            "benchmark_bars": benchmark_rows,
            "market": "KR",
            "ticker": instrument["canonical_symbol"],
            "tick_size": float(historical_tick),
            "source_name": instrument["source_name"],
        }
        payloads = {
            qta1.METHOD_VERSION: qta1.calculate(**common),
            qta2.METHOD_VERSION: qta2.calculate(**common),
        }
        previous_close = Decimal(str(ticker_rows[-1].close))
    except (qta1.BlockedError, OSError, ValueError) as exc:
        payloads = {
            method: blocked_payload(instrument, str(exc), method)
            for method in METHODS
        }
        previous_close = None
    return {
        "instrument": instrument,
        "previous_close": (
            format(previous_close, "f") if previous_close is not None else None
        ),
        "payloads": payloads,
    }


def eligible(payload: dict[str, Any]) -> bool:
    return (
        payload.get("calculation_status") == "READY"
        and payload.get("setup_status") == "READY"
        and decimal_value(payload.get("total_score"), "total_score")
        >= Decimal("60")
    )


def select_candidates(
    analyses: list[dict[str, Any]],
    *,
    method: str,
    top_k: int,
) -> list[dict[str, Any]]:
    selected: list[dict[str, Any]] = []
    for exchange in EXCHANGES:
        ready = [
            item
            for item in analyses
            if item["instrument"]["exchange"] == exchange
            and eligible(item["payloads"][method])
        ]
        ready.sort(key=lambda item: screen.ranking_key(item["payloads"][method]))
        for rank, item in enumerate(ready[:top_k], start=1):
            selected.append(
                {
                    **item,
                    "exchange_rank": rank,
                }
            )
    return selected


def bars_cache_path(
    output_directory: Path,
    *,
    session_date: str,
    instrument: dict[str, Any],
) -> Path:
    return (
        output_directory
        / "bars"
        / session_date
        / instrument["exchange"]
        / f"{instrument['canonical_symbol']}.json"
    )


def load_or_fetch_bars(
    client: shared.KisReadClient,
    output_directory: Path,
    *,
    session_date: str,
    instrument: dict[str, Any],
) -> tuple[dict[str, Any], bool]:
    path = bars_cache_path(
        output_directory,
        session_date=session_date,
        instrument=instrument,
    )
    if path.exists():
        try:
            artifact = intraday.normalize_bars_artifact(
                json.loads(path.read_text(encoding="utf-8"))
            )
        except (OSError, json.JSONDecodeError, intraday.ResearchBlockedError) as exc:
            raise WalkForwardBlockedError(
                f"invalid cached bars: {path}: {exc}"
            ) from exc
        if (
            artifact["session_date"] != session_date
            or artifact["exchange"] != instrument["exchange"]
            or artifact["canonical_symbol"] != instrument["canonical_symbol"]
            or artifact["broker_symbol"] != instrument["broker_symbol"]
        ):
            raise WalkForwardBlockedError(
                f"cached bars identity mismatch: {path}"
            )
        return artifact, True
    raw_rows = intraday.fetch_raw_rows(
        client,
        market="KR",
        exchange=instrument["exchange"],
        broker_symbol=instrument["broker_symbol"],
        session_date=session_date,
    )
    artifact = intraday.bars_artifact(
        instrument=instrument,
        market="KR",
        session_date=session_date,
        raw_rows=raw_rows,
    )
    shared.atomic_write_json(path, artifact)
    return artifact, False


def benchmark_rows(
    instruments: list[dict[str, Any]],
) -> dict[str, dict[date, qta1.RawBar]]:
    output: dict[str, dict[date, qta1.RawBar]] = {}
    for exchange in EXCHANGES:
        path = next(
            item["benchmark_csv"]
            for item in instruments
            if item["exchange"] == exchange
        )
        output[exchange] = {row.day: row for row in qta1.read_csv(path)}
    return output


def open_regime_bps(
    rows: dict[date, qta1.RawBar],
    *,
    analysis_date: date,
    session_date: date,
) -> Decimal:
    previous = rows.get(analysis_date)
    current = rows.get(session_date)
    if previous is None or current is None:
        raise WalkForwardBlockedError(
            "benchmark is missing an analysis/session pair"
        )
    return (
        Decimal(str(current.open)) / Decimal(str(previous.close)) - Decimal(1)
    ) * Decimal(10000)


def compact_candidate(
    *,
    method: str,
    analysis_date: date,
    session_date: date,
    selected: dict[str, Any],
    artifact: dict[str, Any] | None,
    bars_error: str | None,
    regime_bps: Decimal,
) -> dict[str, Any]:
    instrument = selected["instrument"]
    qta = selected["payloads"][method]
    base = {
        "method_version": method,
        "analysis_date": analysis_date.isoformat(),
        "session_date": session_date.isoformat(),
        "exchange": instrument["exchange"],
        "canonical_symbol": instrument["canonical_symbol"],
        "broker_symbol": instrument["broker_symbol"],
        "exchange_rank": selected["exchange_rank"],
        "qta_total_score": decimal_text(
            decimal_value(qta["total_score"], "qta_total_score")
        ),
        "qta_short_score": decimal_text(
            decimal_value(qta["short"]["score"], "qta_short_score")
        ),
        "qta_medium_score": decimal_text(
            decimal_value(qta["medium"]["score"], "qta_medium_score")
        ),
        "qta_long_score": decimal_text(
            decimal_value(qta["long"]["score"], "qta_long_score")
        ),
        "qta_risk_score": decimal_text(
            decimal_value(qta["risk"]["score"], "qta_risk_score")
        ),
        "entry_price": format(decimal_value(qta["entry_price"], "entry"), "f"),
        "stop_price": format(decimal_value(qta["stop_price"], "stop"), "f"),
        "take_profit_price": format(
            decimal_value(qta["take_profit_price"], "take_profit"), "f"
        ),
        "previous_close": selected["previous_close"],
        "market_open_regime_bps": decimal_text(regime_bps),
        "market_open_regime_pass": regime_bps >= 0,
        "bars_status": "READY" if artifact is not None else "BLOCKED",
        "bars_reason": bars_error,
    }
    if artifact is None:
        return {
            **base,
            "coverage_status": None,
            "bar_count": 0,
            "return_60m_bps": None,
            "maximum_excursion_bps": None,
            "minimum_excursion_bps": None,
            "entry_touched_by_minute_high": None,
            "entry_crossed_by_bar_close": None,
            "entry_cross_bar_time": None,
            "end_return_from_entry_bps": None,
            "bars_hash": None,
        }
    bars = artifact["bars"]
    summary = artifact["summary"]
    entry = decimal_value(qta["entry_price"], "entry")
    cross_time = evaluation.first_bar_close_cross(bars, entry)
    end_close = Decimal(summary["end_close"])
    return {
        **base,
        "coverage_status": intraday.coverage_summary(
            bars, market="KR"
        )["coverage_status"],
        "bar_count": len(bars),
        "return_60m_bps": summary["return_60m_bps"],
        "maximum_excursion_bps": summary["maximum_excursion_bps"],
        "minimum_excursion_bps": summary["minimum_excursion_bps"],
        "entry_touched_by_minute_high": (
            Decimal(summary["window_high"]) >= entry
        ),
        "entry_crossed_by_bar_close": cross_time is not None,
        "entry_cross_bar_time": cross_time,
        "end_return_from_entry_bps": (
            intraday.bps(end_close, entry) if cross_time is not None else None
        ),
        "bars_hash": artifact["bars_hash"],
    }


def mean(values: list[Decimal]) -> str | None:
    if not values:
        return None
    return decimal_text(sum(values, Decimal(0)) / Decimal(len(values)))


def median(values: list[Decimal]) -> str | None:
    if not values:
        return None
    return decimal_text(statistics.median(values))


def fraction(numerator: int, denominator: int) -> str | None:
    if denominator == 0:
        return None
    return decimal_text(
        Decimal(numerator) / Decimal(denominator),
        "0.000001",
    )


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    ready = [row for row in rows if row["bars_status"] == "READY"]
    returns = [
        decimal_value(row["return_60m_bps"], "return_60m_bps")
        for row in ready
    ]
    crossed = [
        row for row in ready if row["entry_crossed_by_bar_close"] is True
    ]
    entry_returns = [
        decimal_value(row["end_return_from_entry_bps"], "entry return")
        for row in crossed
    ]
    regime_evaluated = [
        row
        for row in ready
        if isinstance(row["market_open_regime_pass"], bool)
    ]
    regime_ready = [
        row for row in ready if row["market_open_regime_pass"] is True
    ]
    regime_returns = [
        decimal_value(row["return_60m_bps"], "regime return")
        for row in regime_ready
    ]
    regime_crossed = [
        row
        for row in crossed
        if row["market_open_regime_pass"] is True
    ]
    regime_entry_returns = [
        decimal_value(row["end_return_from_entry_bps"], "regime entry return")
        for row in regime_crossed
    ]
    session_means: list[Decimal] = []
    for session_date in sorted({row["session_date"] for row in ready}):
        values = [
            decimal_value(row["return_60m_bps"], "session return")
            for row in ready
            if row["session_date"] == session_date
        ]
        if values:
            session_means.append(
                sum(values, Decimal(0)) / Decimal(len(values))
            )
    return {
        "selected_count": len(rows),
        "bars_ready_count": len(ready),
        "bars_blocked_count": len(rows) - len(ready),
        "mean_return_60m_bps": mean(returns),
        "median_return_60m_bps": median(returns),
        "positive_fraction": fraction(
            sum(value > 0 for value in returns), len(returns)
        ),
        "entry_cross_count": len(crossed),
        "entry_cross_fraction": fraction(len(crossed), len(ready)),
        "mean_end_return_from_entry_bps": mean(entry_returns),
        "median_end_return_from_entry_bps": median(entry_returns),
        "positive_end_return_from_entry_fraction": fraction(
            sum(value > 0 for value in entry_returns),
            len(entry_returns),
        ),
        "market_open_regime_evaluated_count": len(regime_evaluated),
        "market_open_regime_pass_count": len(regime_ready),
        "market_open_regime_pass_fraction": fraction(
            len(regime_ready), len(regime_evaluated)
        ),
        "market_open_regime_pass_mean_return_60m_bps": mean(regime_returns),
        "market_open_regime_entry_cross_count": len(regime_crossed),
        "market_open_regime_cross_mean_end_return_from_entry_bps": mean(
            regime_entry_returns
        ),
        "mean_equal_weight_session_return_60m_bps": mean(session_means),
    }


def grouped_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        method: {
            exchange: summarize(
                [
                    row
                    for row in rows
                    if row["method_version"] == method
                    and row["exchange"] == exchange
                ]
            )
            for exchange in EXCHANGES
        }
        for method in METHODS
    }


def run(args: argparse.Namespace) -> dict[str, Any]:
    manifest_path = absolute_file(args.manifest, "manifest")
    output_directory = absolute_directory(
        args.output_directory, "output_directory"
    )
    output_directory.mkdir(parents=True, exist_ok=True)
    manifest, instruments = load_manifest(manifest_path)
    pairs = shared_session_pairs(
        instruments,
        end_session=date.fromisoformat(args.end_session),
        sessions=args.sessions,
    )
    benchmark = benchmark_rows(instruments)
    key, secret, token = shared.load_api_credentials(args.environment)
    client = shared.KisReadClient(
        environment=args.environment,
        app_key=key,
        app_secret=secret,
        access_token=token,
        interval_ms=args.request_interval_ms,
    )
    workers = min(8, max(1, args.workers))
    candidate_rows: list[dict[str, Any]] = []
    session_receipts: list[dict[str, Any]] = []
    cache_hits = 0
    for analysis_date, session_date in pairs:
        tasks = [
            (instrument, analysis_date.isoformat())
            for instrument in instruments
        ]
        with ProcessPoolExecutor(max_workers=workers) as executor:
            analyses = list(
                executor.map(analyze_pair_task, tasks, chunksize=16)
            )
        selections = {
            method: select_candidates(
                analyses,
                method=method,
                top_k=args.top_k,
            )
            for method in METHODS
        }
        selected_by_key: dict[
            tuple[str, str], dict[str, Any]
        ] = {}
        for method_rows in selections.values():
            for item in method_rows:
                instrument = item["instrument"]
                selected_by_key[
                    (instrument["exchange"], instrument["canonical_symbol"])
                ] = item
        artifacts: dict[tuple[str, str], dict[str, Any] | None] = {}
        errors: dict[tuple[str, str], str | None] = {}
        for key_value, item in sorted(selected_by_key.items()):
            try:
                artifact, cache_hit = load_or_fetch_bars(
                    client,
                    output_directory,
                    session_date=session_date.isoformat(),
                    instrument=item["instrument"],
                )
                artifacts[key_value] = artifact
                errors[key_value] = None
                cache_hits += int(cache_hit)
            except (
                shared.EodBlockedError,
                intraday.ResearchBlockedError,
                WalkForwardBlockedError,
            ) as exc:
                artifacts[key_value] = None
                errors[key_value] = str(exc)
        for method, method_rows in selections.items():
            for item in method_rows:
                instrument = item["instrument"]
                key_value = (
                    instrument["exchange"],
                    instrument["canonical_symbol"],
                )
                regime = open_regime_bps(
                    benchmark[instrument["exchange"]],
                    analysis_date=analysis_date,
                    session_date=session_date,
                )
                candidate_rows.append(
                    compact_candidate(
                        method=method,
                        analysis_date=analysis_date,
                        session_date=session_date,
                        selected=item,
                        artifact=artifacts[key_value],
                        bars_error=errors[key_value],
                        regime_bps=regime,
                    )
                )
        session_receipts.append(
            {
                "analysis_date": analysis_date.isoformat(),
                "session_date": session_date.isoformat(),
                "analyzed_instrument_count": len(analyses),
                "qta1_blocked_count": sum(
                    item["payloads"][qta1.METHOD_VERSION].get(
                        "calculation_status"
                    )
                    != "READY"
                    for item in analyses
                ),
                "qta2_blocked_count": sum(
                    item["payloads"][qta2.METHOD_VERSION].get(
                        "calculation_status"
                    )
                    != "READY"
                    for item in analyses
                ),
                "qta1_selected_count": len(
                    selections[qta1.METHOD_VERSION]
                ),
                "qta2_selected_count": len(
                    selections[qta2.METHOD_VERSION]
                ),
                "unique_bar_request_candidates": len(selected_by_key),
                "bars_blocked_count": sum(
                    artifact is None for artifact in artifacts.values()
                ),
            }
        )
        print(
            json.dumps(session_receipts[-1], sort_keys=True),
            file=sys.stderr,
            flush=True,
        )
    candidate_rows.sort(
        key=lambda item: (
            item["session_date"],
            METHODS.index(item["method_version"]),
            EXCHANGES.index(item["exchange"]),
            item["exchange_rank"],
            item["canonical_symbol"],
        )
    )
    without_hash = {
        "schema": SCHEMA,
        "source_skill": "quant-stock-technical",
        "source_manifest_path": str(manifest_path),
        "source_manifest_hash": manifest["manifest_hash"],
        "source_manifest_as_of": manifest["as_of"],
        "universe_mode": UNIVERSE_MODE,
        "universe_limitations": [
            (
                "the source manifest membership is held static across earlier "
                "sessions, creating survivorship and membership bias"
            ),
            (
                "historical bid-ask spreads and exact quote-cycle market-regime "
                "states are unavailable"
            ),
            (
                "market_open_regime_bps is the exact benchmark opening gap, "
                "not the runner's later intraday regime observation"
            ),
        ],
        "market": "KR",
        "methods": list(METHODS),
        "top_k_per_exchange": args.top_k,
        "minimum_total_score": "60",
        "session_count": len(pairs),
        "first_session_date": pairs[0][1].isoformat(),
        "last_session_date": pairs[-1][1].isoformat(),
        "session_receipts": session_receipts,
        "summary_by_method_and_exchange": grouped_summary(candidate_rows),
        "candidate_rows": candidate_rows,
        "request_count": client.request_count,
        "retry_count": client.retry_count,
        "cache_hit_count": cache_hits,
        "api_mutation_count": 0,
        "live_enabled": False,
        "validation_status": "RESEARCH_ONLY",
    }
    output = {
        **without_hash,
        "walk_forward_hash": shared.sha256_bytes(
            shared.canonical_json(without_hash).encode("utf-8")
        ),
    }
    output_path = output_directory / "walk-forward.json"
    shared.atomic_write_json(output_path, output)
    return output


def self_test() -> None:
    rows = [
        {
            "method_version": qta1.METHOD_VERSION,
            "session_date": "2026-01-02",
            "exchange": "KOSPI",
            "bars_status": "READY",
            "return_60m_bps": "100",
            "entry_crossed_by_bar_close": True,
            "end_return_from_entry_bps": "25",
            "market_open_regime_pass": True,
        },
        {
            "method_version": qta1.METHOD_VERSION,
            "session_date": "2026-01-03",
            "exchange": "KOSPI",
            "bars_status": "READY",
            "return_60m_bps": "-50",
            "entry_crossed_by_bar_close": False,
            "end_return_from_entry_bps": None,
            "market_open_regime_pass": False,
        },
    ]
    summary = summarize(rows)
    assert summary["mean_return_60m_bps"] == "25.00"
    assert summary["positive_fraction"] == "0.500000"
    assert summary["entry_cross_fraction"] == "0.500000"
    assert summary["positive_end_return_from_entry_fraction"] == "1.000000"
    assert summary["market_open_regime_pass_mean_return_60m_bps"] == "100.00"
    assert summary["market_open_regime_entry_cross_count"] == 1
    print(
        json.dumps(
            {
                "schema": SCHEMA,
                "self_test": "PASS",
                "api_mutation_count": 0,
                "live_enabled": False,
            },
            sort_keys=True,
        )
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--manifest")
    parser.add_argument("--output-directory")
    parser.add_argument("--end-session")
    parser.add_argument("--sessions", type=int, default=20)
    parser.add_argument("--top-k", type=int, default=2)
    parser.add_argument("--environment", choices=("live", "paper"), default="live")
    parser.add_argument("--request-interval-ms", type=int, default=120)
    parser.add_argument("--workers", type=int, default=8)
    args = parser.parse_args()
    if not args.self_test:
        missing = [
            name
            for name in ("manifest", "output_directory", "end_session")
            if getattr(args, name) is None
        ]
        if missing:
            parser.error(f"missing required arguments: {', '.join(missing)}")
        try:
            date.fromisoformat(args.end_session)
        except ValueError as exc:
            parser.error("--end-session must be YYYY-MM-DD")
        if args.sessions < 2 or args.sessions > 252:
            parser.error("--sessions must be between 2 and 252")
        if args.top_k < 1 or args.top_k > 20:
            parser.error("--top-k must be between 1 and 20")
        floor = 1000 if args.environment == "paper" else 100
        if args.request_interval_ms < floor:
            parser.error(
                f"--request-interval-ms must be at least {floor}"
            )
        if args.workers < 1 or args.workers > 8:
            parser.error("--workers must be between 1 and 8")
    return args


def main() -> int:
    args = parse_args()
    if args.self_test:
        self_test()
        return 0
    try:
        output = run(args)
    except (
        WalkForwardBlockedError,
        shared.EodBlockedError,
        intraday.ResearchBlockedError,
        OSError,
        ValueError,
    ) as exc:
        print(
            json.dumps(
                {
                    "schema": SCHEMA,
                    "status": "BLOCKED",
                    "reason": str(exc),
                    "api_mutation_count": 0,
                    "live_enabled": False,
                },
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        return 2
    print(
        json.dumps(
            {
                "schema": SCHEMA,
                "status": "READY",
                "session_count": output["session_count"],
                "walk_forward_hash": output["walk_forward_hash"],
                "output": str(
                    Path(args.output_directory) / "walk-forward.json"
                ),
                "request_count": output["request_count"],
                "retry_count": output["retry_count"],
                "api_mutation_count": 0,
                "live_enabled": False,
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

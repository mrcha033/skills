#!/usr/bin/env python3
"""Collect and compare selected candidates from frozen point-in-time screens."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

import analyze_stock as qta1
import collect_open1h_research as intraday
import evaluate_open1h_research as evaluation
import fetch_kis_kr_eod as shared
import screen_universe as screen
import walk_forward_open1h as walk_forward


SCHEMA = "qta-selected-open1h-outcomes/v1"


class SelectedOutcomesBlockedError(ValueError):
    """Raised when selected point-in-time outcomes cannot be verified."""


def absolute_file(value: str, field: str) -> Path:
    path = Path(value).expanduser()
    if not path.is_absolute() or not path.is_file() or path.is_symlink():
        raise SelectedOutcomesBlockedError(
            f"{field} must be an absolute regular non-symlink file"
        )
    return path


def absolute_directory(value: str, field: str) -> Path:
    path = Path(value).expanduser()
    if not path.is_absolute():
        raise SelectedOutcomesBlockedError(f"{field} must be absolute")
    if path.exists() and (not path.is_dir() or path.is_symlink()):
        raise SelectedOutcomesBlockedError(
            f"{field} must be a non-symlink directory"
        )
    return path


def load_manifest(path: Path) -> dict[str, Any]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        manifest = screen.normalize_manifest_v2(raw)
    except (OSError, json.JSONDecodeError, screen.ScreenBlockedError) as exc:
        raise SelectedOutcomesBlockedError(
            f"manifest validation failed: {exc}"
        ) from exc
    supplied_hash = raw.get("manifest_hash")
    if not isinstance(supplied_hash, str):
        raise SelectedOutcomesBlockedError("manifest_hash is required")
    return manifest


def selected_rows(
    screen_value: dict[str, Any],
    *,
    market: str,
) -> list[dict[str, Any]]:
    selected = screen_value.get("selected")
    if not isinstance(selected, dict):
        raise SelectedOutcomesBlockedError(
            "screen.selected must be an exchange object"
        )
    exchanges = intraday.MARKET_CONTRACTS[market]["exchanges"]
    rows: list[dict[str, Any]] = []
    for exchange in exchanges:
        exchange_rows = selected.get(exchange, [])
        if not isinstance(exchange_rows, list):
            raise SelectedOutcomesBlockedError(
                f"screen.selected.{exchange} must be an array"
            )
        for row in exchange_rows:
            if (
                not isinstance(row, dict)
                or not isinstance(row.get("instrument"), dict)
                or not isinstance(row.get("qta"), dict)
            ):
                raise SelectedOutcomesBlockedError(
                    "selected row is missing instrument or QTA"
                )
            if row["instrument"]["market"] != market:
                continue
            rows.append(row)
    rows.sort(
        key=lambda item: (
            exchanges.index(item["instrument"]["exchange"]),
            int(item["exchange_rank"]),
            item["instrument"]["canonical_symbol"],
        )
    )
    return rows


def cache_path(
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


def load_or_fetch(
    client: shared.KisReadClient,
    output_directory: Path,
    *,
    market: str,
    session_date: str,
    instrument: dict[str, Any],
) -> tuple[dict[str, Any], bool]:
    path = cache_path(
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
            raise SelectedOutcomesBlockedError(
                f"cached bars are invalid: {path}: {exc}"
            ) from exc
        if (
            artifact["market"] != market
            or artifact["session_date"] != session_date
            or artifact["exchange"] != instrument["exchange"]
            or artifact["canonical_symbol"] != instrument["canonical_symbol"]
            or artifact["broker_symbol"] != instrument["broker_symbol"]
        ):
            raise SelectedOutcomesBlockedError(
                f"cached bars identity mismatch: {path}"
            )
        return artifact, True
    raw_rows = intraday.fetch_raw_rows(
        client,
        market=market,
        exchange=instrument["exchange"],
        broker_symbol=instrument["broker_symbol"],
        session_date=session_date,
    )
    artifact = intraday.bars_artifact(
        instrument=instrument,
        market=market,
        session_date=session_date,
        raw_rows=raw_rows,
    )
    shared.atomic_write_json(path, artifact)
    return artifact, False


def benchmark_open_regime_bps(
    instrument: dict[str, Any],
    *,
    analysis_date: str,
    session_date: str,
) -> Decimal | None:
    rows = {
        row.day: row
        for row in qta1.read_csv(instrument["benchmark_csv"])
    }
    previous = rows.get(date.fromisoformat(analysis_date))
    current = rows.get(date.fromisoformat(session_date))
    if previous is None or current is None:
        return None
    return (
        Decimal(str(current.open)) / Decimal(str(previous.close)) - Decimal(1)
    ) * Decimal(10000)


def compact(
    *,
    method: str,
    analysis_date: str,
    session_date: str,
    selected: dict[str, Any],
    artifact: dict[str, Any] | None,
    bars_reason: str | None,
) -> dict[str, Any]:
    instrument = selected["instrument"]
    qta = selected["qta"]
    regime = benchmark_open_regime_bps(
        instrument,
        analysis_date=analysis_date,
        session_date=session_date,
    )
    base = {
        "method_version": method,
        "analysis_date": analysis_date,
        "session_date": session_date,
        "exchange": instrument["exchange"],
        "canonical_symbol": instrument["canonical_symbol"],
        "broker_symbol": instrument["broker_symbol"],
        "exchange_rank": selected["exchange_rank"],
        "qta_total_score": walk_forward.decimal_text(
            walk_forward.decimal_value(qta["total_score"], "total_score")
        ),
        "entry_price": format(
            walk_forward.decimal_value(qta["entry_price"], "entry_price"),
            "f",
        ),
        "stop_price": format(
            walk_forward.decimal_value(qta["stop_price"], "stop_price"),
            "f",
        ),
        "take_profit_price": format(
            walk_forward.decimal_value(
                qta["take_profit_price"], "take_profit_price"
            ),
            "f",
        ),
        "market_open_regime_bps": (
            walk_forward.decimal_text(regime) if regime is not None else None
        ),
        "market_open_regime_pass": (
            regime >= 0 if regime is not None else None
        ),
        "bars_status": "READY" if artifact is not None else "BLOCKED",
        "bars_reason": bars_reason,
    }
    if artifact is None:
        return {
            **base,
            "coverage_status": None,
            "bar_count": 0,
            "return_60m_bps": None,
            "entry_touched_by_minute_high": None,
            "entry_crossed_by_bar_close": None,
            "entry_cross_bar_time": None,
            "end_return_from_entry_bps": None,
            "bars_hash": None,
        }
    bars = artifact["bars"]
    summary = artifact["summary"]
    entry = walk_forward.decimal_value(qta["entry_price"], "entry_price")
    cross_time = evaluation.first_bar_close_cross(bars, entry)
    return {
        **base,
        "coverage_status": intraday.coverage_summary(
            bars, market=artifact["market"]
        )["coverage_status"],
        "bar_count": len(bars),
        "return_60m_bps": summary["return_60m_bps"],
        "entry_touched_by_minute_high": (
            Decimal(summary["window_high"]) >= entry
        ),
        "entry_crossed_by_bar_close": cross_time is not None,
        "entry_cross_bar_time": cross_time,
        "end_return_from_entry_bps": (
            intraday.bps(Decimal(summary["end_close"]), entry)
            if cross_time is not None
            else None
        ),
        "bars_hash": artifact["bars_hash"],
    }


def run(args: argparse.Namespace) -> dict[str, Any]:
    manifest_path = absolute_file(args.manifest, "manifest")
    output_directory = absolute_directory(
        args.output_directory, "output_directory"
    )
    output_directory.mkdir(parents=True, exist_ok=True)
    manifest = load_manifest(manifest_path)
    if manifest["as_of"] != args.session_date:
        raise SelectedOutcomesBlockedError(
            "manifest.as_of must equal session_date"
        )
    screens: dict[str, dict[str, Any]] = {}
    selections: dict[str, list[dict[str, Any]]] = {}
    for path_text in args.screen:
        path = absolute_file(path_text, "screen")
        value = evaluation.normalized_screen(path)
        method = value["method_version"]
        if method in screens:
            raise SelectedOutcomesBlockedError(
                f"duplicate method screen: {method}"
            )
        if value["manifest_hash"] != manifest["manifest_hash"]:
            raise SelectedOutcomesBlockedError(
                f"{method} screen manifest hash mismatch"
            )
        if value["analysis_date"] != manifest["analysis_date"]:
            raise SelectedOutcomesBlockedError(
                f"{method} screen analysis date mismatch"
            )
        screens[method] = value
        selections[method] = selected_rows(value, market=args.market)
    if not screens:
        raise SelectedOutcomesBlockedError("at least one screen is required")
    selected_by_key: dict[tuple[str, str], dict[str, Any]] = {}
    for method_rows in selections.values():
        for row in method_rows:
            instrument = row["instrument"]
            selected_by_key[
                (instrument["exchange"], instrument["canonical_symbol"])
            ] = instrument
    key, secret, token = shared.load_api_credentials(args.environment)
    client = shared.KisReadClient(
        environment=args.environment,
        app_key=key,
        app_secret=secret,
        access_token=token,
        interval_ms=args.request_interval_ms,
    )
    artifacts: dict[tuple[str, str], dict[str, Any] | None] = {}
    errors: dict[tuple[str, str], str | None] = {}
    cache_hits = 0
    for key_value, instrument in sorted(selected_by_key.items()):
        try:
            artifact, cache_hit = load_or_fetch(
                client,
                output_directory,
                market=args.market,
                session_date=args.session_date,
                instrument=instrument,
            )
            artifacts[key_value] = artifact
            errors[key_value] = None
            cache_hits += int(cache_hit)
        except (
            shared.EodBlockedError,
            intraday.ResearchBlockedError,
            SelectedOutcomesBlockedError,
        ) as exc:
            artifacts[key_value] = None
            errors[key_value] = str(exc)
    rows: list[dict[str, Any]] = []
    for method, method_rows in selections.items():
        for selected in method_rows:
            instrument = selected["instrument"]
            key_value = (
                instrument["exchange"],
                instrument["canonical_symbol"],
            )
            rows.append(
                compact(
                    method=method,
                    analysis_date=manifest["analysis_date"],
                    session_date=args.session_date,
                    selected=selected,
                    artifact=artifacts[key_value],
                    bars_reason=errors[key_value],
                )
            )
    methods = sorted({row["method_version"] for row in rows})
    exchanges = intraday.MARKET_CONTRACTS[args.market]["exchanges"]
    rows.sort(
        key=lambda item: (
            methods.index(item["method_version"]),
            exchanges.index(item["exchange"]),
            int(item["exchange_rank"]),
            item["canonical_symbol"],
        )
    )
    summary = {
        method: {
            exchange: walk_forward.summarize(
                [
                    row
                    for row in rows
                    if row["method_version"] == method
                    and row["exchange"] == exchange
                ]
            )
            for exchange in exchanges
        }
        for method in methods
    }
    without_hash = {
        "schema": SCHEMA,
        "source_skill": "quant-stock-technical",
        "manifest_path": str(manifest_path),
        "manifest_hash": manifest["manifest_hash"],
        "analysis_date": manifest["analysis_date"],
        "session_date": args.session_date,
        "market": args.market,
        "methods": methods,
        "screen_hashes": {
            method: screens[method]["screen_hash"] for method in methods
        },
        "selected_union_count": len(selected_by_key),
        "summary_by_method_and_exchange": summary,
        "candidate_rows": rows,
        "request_count": client.request_count,
        "retry_count": client.retry_count,
        "cache_hit_count": cache_hits,
        "api_mutation_count": 0,
        "live_enabled": False,
        "validation_status": "RESEARCH_ONLY",
    }
    output = {
        **without_hash,
        "outcomes_hash": shared.sha256_bytes(
            shared.canonical_json(without_hash).encode("utf-8")
        ),
    }
    shared.atomic_write_json(output_directory / "selected-outcomes.json", output)
    return output


def self_test() -> None:
    rows = [
        {
            "method_version": "qta-2.0.0",
            "session_date": "2026-07-29",
            "exchange": "NASDAQ",
            "bars_status": "READY",
            "return_60m_bps": "50",
            "entry_crossed_by_bar_close": True,
            "end_return_from_entry_bps": "10",
            "market_open_regime_pass": True,
        }
    ]
    summary = walk_forward.summarize(rows)
    assert summary["mean_return_60m_bps"] == "50.00"
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
    parser.add_argument("--screen", action="append", default=[])
    parser.add_argument("--market", choices=("KR", "US"))
    parser.add_argument("--session-date")
    parser.add_argument("--output-directory")
    parser.add_argument("--environment", choices=("live", "paper"), default="live")
    parser.add_argument("--request-interval-ms", type=int, default=120)
    args = parser.parse_args()
    if not args.self_test:
        missing = [
            name
            for name in (
                "manifest",
                "market",
                "session_date",
                "output_directory",
            )
            if getattr(args, name) is None
        ]
        if missing:
            parser.error(f"missing required arguments: {', '.join(missing)}")
        if not args.screen:
            parser.error("--screen is required at least once")
        try:
            date.fromisoformat(args.session_date)
        except ValueError:
            parser.error("--session-date must be YYYY-MM-DD")
        floor = 1000 if args.environment == "paper" else 100
        if args.request_interval_ms < floor:
            parser.error(
                f"--request-interval-ms must be at least {floor}"
            )
    return args


def main() -> int:
    args = parse_args()
    if args.self_test:
        self_test()
        return 0
    try:
        output = run(args)
    except (
        SelectedOutcomesBlockedError,
        walk_forward.WalkForwardBlockedError,
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
                "outcomes_hash": output["outcomes_hash"],
                "selected_union_count": output["selected_union_count"],
                "request_count": output["request_count"],
                "retry_count": output["retry_count"],
                "api_mutation_count": 0,
                "live_enabled": False,
                "output": str(
                    Path(args.output_directory) / "selected-outcomes.json"
                ),
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

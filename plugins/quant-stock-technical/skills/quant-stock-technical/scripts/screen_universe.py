#!/usr/bin/env python3
"""Run qta-1.0.0 across a frozen universe and apply qta-screen-1.0.0."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

import analyze_stock as qta

MANIFEST_SCHEMA = "qta-universe-manifest/v1"
SCREEN_SCHEMA = "qta-screen/v1"
SELECTOR_VERSION = "qta-screen-1.0.0"
SUPPORTED_MARKETS = {"KR", "US"}


class ScreenBlockedError(ValueError):
    """Raised when a frozen screen contract is invalid."""


def canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def sha256_json(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def load_object(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ScreenBlockedError(f"{path} must contain a JSON object")
    return value


def as_decimal(value: Any, field: str) -> Decimal:
    if isinstance(value, bool):
        raise ScreenBlockedError(f"{field} must be a decimal string or number")
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ScreenBlockedError(f"{field} must be a decimal string or number") from exc
    if not parsed.is_finite():
        raise ScreenBlockedError(f"{field} must be finite")
    return parsed


def normalize_manifest(manifest: dict[str, Any]) -> dict[str, Any]:
    if set(manifest) != {"schema", "analysis_date", "instruments"}:
        raise ScreenBlockedError(
            "manifest fields must be exactly schema, analysis_date, instruments"
        )
    if manifest["schema"] != MANIFEST_SCHEMA:
        raise ScreenBlockedError(f"unsupported manifest schema: {manifest['schema']!r}")
    try:
        date.fromisoformat(str(manifest["analysis_date"]))
    except ValueError as exc:
        raise ScreenBlockedError("analysis_date must be YYYY-MM-DD") from exc
    instruments = manifest["instruments"]
    if not isinstance(instruments, list) or not instruments:
        raise ScreenBlockedError("instruments must be a non-empty array")

    required = {
        "market",
        "ticker",
        "ticker_csv",
        "benchmark_csv",
        "tick_size",
        "source_name",
    }
    normalized: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for index, raw in enumerate(instruments):
        if not isinstance(raw, dict) or set(raw) != required:
            raise ScreenBlockedError(
                f"instruments[{index}] fields must be exactly {sorted(required)}"
            )
        market = str(raw["market"]).upper()
        ticker = str(raw["ticker"]).upper()
        if market not in SUPPORTED_MARKETS:
            raise ScreenBlockedError(f"instruments[{index}].market must be KR or US")
        if not ticker:
            raise ScreenBlockedError(f"instruments[{index}].ticker is empty")
        key = (market, ticker)
        if key in seen:
            raise ScreenBlockedError(f"duplicate instrument: {market}:{ticker}")
        seen.add(key)
        tick_size = as_decimal(raw["tick_size"], f"instruments[{index}].tick_size")
        if tick_size <= 0:
            raise ScreenBlockedError(f"instruments[{index}].tick_size must be positive")
        normalized.append(
            {
                "market": market,
                "ticker": ticker,
                "ticker_csv": str(raw["ticker_csv"]),
                "benchmark_csv": str(raw["benchmark_csv"]),
                "tick_size": format(tick_size, "f"),
                "source_name": str(raw["source_name"]),
            }
        )
    normalized.sort(key=lambda item: (item["market"], item["ticker"]))
    return {
        "schema": MANIFEST_SCHEMA,
        "analysis_date": str(manifest["analysis_date"]),
        "instruments": normalized,
    }


def normalize_selector(selector: dict[str, Any]) -> dict[str, Any]:
    required = {
        "selector_version",
        "min_total_score",
        "eligible_setup_statuses",
        "top_k_by_market",
        "max_blocked_fraction",
    }
    if set(selector) != required:
        raise ScreenBlockedError(f"selector fields must be exactly {sorted(required)}")
    if selector["selector_version"] != SELECTOR_VERSION:
        raise ScreenBlockedError(
            f"unsupported selector version: {selector['selector_version']!r}"
        )
    minimum = as_decimal(selector["min_total_score"], "min_total_score")
    if minimum < 0 or minimum > 100:
        raise ScreenBlockedError("min_total_score must be between 0 and 100")
    maximum_blocked = as_decimal(
        selector["max_blocked_fraction"], "max_blocked_fraction"
    )
    if maximum_blocked < 0 or maximum_blocked > 1:
        raise ScreenBlockedError("max_blocked_fraction must be between 0 and 1")

    statuses = selector["eligible_setup_statuses"]
    if (
        not isinstance(statuses, list)
        or not statuses
        or any(not isinstance(item, str) or not item for item in statuses)
    ):
        raise ScreenBlockedError(
            "eligible_setup_statuses must be a non-empty string array"
        )
    normalized_statuses = sorted(set(statuses))

    top_k = selector["top_k_by_market"]
    if not isinstance(top_k, dict) or set(top_k) != SUPPORTED_MARKETS:
        raise ScreenBlockedError("top_k_by_market must contain exactly KR and US")
    normalized_top_k: dict[str, int] = {}
    for market in sorted(SUPPORTED_MARKETS):
        value = top_k[market]
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ScreenBlockedError(
                f"top_k_by_market.{market} must be an integer >= 0"
            )
        normalized_top_k[market] = value

    return {
        "selector_version": SELECTOR_VERSION,
        "min_total_score": format(minimum, "f"),
        "eligible_setup_statuses": normalized_statuses,
        "top_k_by_market": normalized_top_k,
        "max_blocked_fraction": format(maximum_blocked, "f"),
    }


def blocked_payload(instrument: dict[str, str], reason: str) -> dict[str, Any]:
    return {
        "source_skill": "quant-stock-technical",
        "result_schema": "quant-stock-technical/v1",
        "calculation_status": "BLOCKED",
        "reason": reason,
        "method_version": qta.METHOD_VERSION,
        "market": instrument["market"],
        "ticker": instrument["ticker"],
    }


def analyze_instrument(
    instrument: dict[str, str],
    analysis_date: date,
    manifest_directory: Path,
) -> dict[str, Any]:
    ticker_path = Path(instrument["ticker_csv"])
    benchmark_path = Path(instrument["benchmark_csv"])
    if not ticker_path.is_absolute():
        ticker_path = manifest_directory / ticker_path
    if not benchmark_path.is_absolute():
        benchmark_path = manifest_directory / benchmark_path
    try:
        ticker_rows, benchmark_rows = qta.align(
            qta.read_csv(str(ticker_path)),
            qta.read_csv(str(benchmark_path)),
            analysis_date,
        )
        return qta.calculate(
            ticker_rows,
            benchmark_rows,
            instrument["market"],
            instrument["ticker"],
            float(Decimal(instrument["tick_size"])),
            instrument["source_name"],
        )
    except (qta.BlockedError, OSError, ValueError) as exc:
        return blocked_payload(instrument, str(exc))


def score(payload: dict[str, Any], path: str) -> Decimal:
    value: Any = payload
    for part in path.split("."):
        if not isinstance(value, dict) or part not in value:
            raise ScreenBlockedError(
                f"{payload.get('market')}:{payload.get('ticker')} missing {path}"
            )
        value = value[part]
    return as_decimal(value, path)


def ranking_key(payload: dict[str, Any]) -> tuple[Any, ...]:
    return (
        -score(payload, "total_score"),
        -score(payload, "medium.score"),
        -score(payload, "long.score"),
        -score(payload, "short.score"),
        score(payload, "risk.score"),
        str(payload["market"]),
        str(payload["ticker"]),
    )


def finalize_screen(
    manifest: dict[str, Any],
    selector: dict[str, Any],
    results: list[dict[str, Any]],
) -> dict[str, Any]:
    instrument_by_key = {
        (item["market"], item["ticker"]): {
            "market": item["market"],
            "ticker": item["ticker"],
            "tick_size": item["tick_size"],
        }
        for item in manifest["instruments"]
    }
    result_by_key: dict[tuple[str, str], dict[str, Any]] = {}
    for result in results:
        key = (str(result.get("market")), str(result.get("ticker")))
        if key in result_by_key:
            raise ScreenBlockedError(f"duplicate calculation result: {key}")
        result_by_key[key] = result

    ordered_results: list[dict[str, Any]] = []
    for instrument in manifest["instruments"]:
        key = (instrument["market"], instrument["ticker"])
        if key not in result_by_key:
            raise ScreenBlockedError(f"missing calculation result: {key}")
        ordered_results.append(result_by_key[key])

    blocked_count = sum(
        result.get("calculation_status") != "READY" for result in ordered_results
    )
    blocked_fraction = Decimal(blocked_count) / Decimal(len(ordered_results))
    maximum_blocked = Decimal(selector["max_blocked_fraction"])
    screen_status = "READY" if blocked_fraction <= maximum_blocked else "BLOCKED"

    decisions: list[dict[str, Any]] = []
    eligible_by_market: dict[str, list[dict[str, Any]]] = {
        market: [] for market in SUPPORTED_MARKETS
    }
    statuses = set(selector["eligible_setup_statuses"])
    minimum = Decimal(selector["min_total_score"])
    for payload in ordered_results:
        reasons: list[str] = []
        if payload.get("calculation_status") != "READY":
            reasons.append("calculation_not_ready")
        else:
            if payload.get("setup_status") not in statuses:
                reasons.append("setup_status_ineligible")
            if score(payload, "total_score") < minimum:
                reasons.append("score_below_minimum")
        eligible = not reasons
        if eligible:
            market = str(payload["market"])
            if market not in eligible_by_market:
                raise ScreenBlockedError(f"result market is unsupported: {market}")
            eligible_by_market[market].append(payload)
        decisions.append(
            {
                "market": payload.get("market"),
                "ticker": payload.get("ticker"),
                "instrument": instrument_by_key[
                    (str(payload.get("market")), str(payload.get("ticker")))
                ],
                "eligible": eligible,
                "reasons": reasons,
                "qta": payload,
            }
        )

    selected: dict[str, list[dict[str, Any]]] = {}
    selected_keys: set[tuple[str, str]] = set()
    for market in sorted(SUPPORTED_MARKETS):
        ranked = sorted(eligible_by_market[market], key=ranking_key)
        chosen = ranked[: selector["top_k_by_market"][market]]
        selected[market] = [
            {
                "rank": index,
                "instrument": instrument_by_key[(market, str(payload["ticker"]))],
                "qta": payload,
            }
            for index, payload in enumerate(chosen, start=1)
        ]
        selected_keys.update((market, str(payload["ticker"])) for payload in chosen)

    for decision in decisions:
        decision["selected"] = (
            decision["market"],
            decision["ticker"],
        ) in selected_keys

    output = {
        "source_skill": "quant-stock-technical",
        "schema": SCREEN_SCHEMA,
        "screen_status": screen_status,
        "method_version": qta.METHOD_VERSION,
        "selector_version": SELECTOR_VERSION,
        "analysis_date": manifest["analysis_date"],
        "manifest_hash": sha256_json(manifest),
        "selector_hash": sha256_json(selector),
        "blocked_count": blocked_count,
        "blocked_fraction": format(blocked_fraction, "f"),
        "instrument_count": len(ordered_results),
        "selector": selector,
        "selected": selected if screen_status == "READY" else {"KR": [], "US": []},
        "decisions": decisions,
    }
    output["screen_hash"] = sha256_json(output)
    return output


def build_screen(
    manifest: dict[str, Any],
    selector: dict[str, Any],
    manifest_directory: Path,
) -> dict[str, Any]:
    normalized_manifest = normalize_manifest(manifest)
    normalized_selector = normalize_selector(selector)
    analysis_date = date.fromisoformat(normalized_manifest["analysis_date"])
    results = [
        analyze_instrument(instrument, analysis_date, manifest_directory)
        for instrument in normalized_manifest["instruments"]
    ]
    return finalize_screen(normalized_manifest, normalized_selector, results)


def synthetic_result(
    market: str,
    ticker: str,
    total: str,
    medium: str,
    long: str,
    short: str,
    risk: str,
) -> dict[str, Any]:
    return {
        "source_skill": "quant-stock-technical",
        "result_schema": "quant-stock-technical/v1",
        "calculation_status": "READY",
        "setup_status": "READY",
        "method_version": qta.METHOD_VERSION,
        "analysis_date": "2026-07-25",
        "market": market,
        "ticker": ticker,
        "source_name": "screen-self-test",
        "shared_sessions": 1000,
        "score_basis": "ticker-relative historical percentile; not probability of profit",
        "short": {"opinion": "긍정", "score": float(short)},
        "medium": {"opinion": "긍정", "score": float(medium)},
        "long": {"opinion": "긍정", "score": float(long)},
        "risk": {"score": float(risk), "counterpoint": "self-test"},
        "entry_price": 100.0,
        "stop_price": 90.0,
        "take_profit_price": 120.0,
        "total_score": float(total),
    }


def self_test() -> None:
    raw_instruments = [
        {
            "market": "US",
            "ticker": "BBB",
            "ticker_csv": "bbb.csv",
            "benchmark_csv": "bench.csv",
            "tick_size": "0.01",
            "source_name": "self-test",
        },
        {
            "market": "US",
            "ticker": "AAA",
            "ticker_csv": "aaa.csv",
            "benchmark_csv": "bench.csv",
            "tick_size": "0.01",
            "source_name": "self-test",
        },
    ]
    selector = normalize_selector(
        {
            "selector_version": SELECTOR_VERSION,
            "min_total_score": "60",
            "eligible_setup_statuses": ["READY"],
            "top_k_by_market": {"KR": 0, "US": 2},
            "max_blocked_fraction": "0",
        }
    )
    manifest_a = normalize_manifest(
        {
            "schema": MANIFEST_SCHEMA,
            "analysis_date": "2026-07-25",
            "instruments": raw_instruments,
        }
    )
    manifest_b = normalize_manifest(
        {
            "schema": MANIFEST_SCHEMA,
            "analysis_date": "2026-07-25",
            "instruments": list(reversed(raw_instruments)),
        }
    )
    results = [
        synthetic_result("US", "BBB", "70", "70", "70", "70", "30"),
        synthetic_result("US", "AAA", "70", "70", "70", "70", "30"),
    ]
    first = finalize_screen(manifest_a, selector, results)
    second = finalize_screen(manifest_b, selector, list(reversed(results)))
    assert first == second
    assert [item["qta"]["ticker"] for item in first["selected"]["US"]] == [
        "AAA",
        "BBB",
    ]
    assert first["screen_status"] == "READY"
    assert len(first["screen_hash"]) == 64
    print(
        canonical_json(
            {
                "self_test": "PASS",
                "selector_version": SELECTOR_VERSION,
                "screen_hash": first["screen_hash"],
            }
        )
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest")
    parser.add_argument("--selector")
    parser.add_argument("--output")
    parser.add_argument("--self-test", action="store_true")
    return parser.parse_args()


def emit(value: dict[str, Any], output: str | None) -> None:
    rendered = json.dumps(
        value, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False
    )
    if output:
        Path(output).write_text(rendered + "\n", encoding="utf-8")
    print(rendered)


def main() -> int:
    args = parse_args()
    if args.self_test:
        self_test()
        return 0
    if not args.manifest or not args.selector:
        emit(
            {
                "source_skill": "quant-stock-technical",
                "schema": SCREEN_SCHEMA,
                "screen_status": "BLOCKED",
                "reason": "--manifest and --selector are required",
            },
            args.output,
        )
        return 2
    manifest_path = Path(args.manifest).resolve()
    try:
        output = build_screen(
            load_object(manifest_path),
            load_object(Path(args.selector).resolve()),
            manifest_path.parent,
        )
    except (ScreenBlockedError, OSError, ValueError, json.JSONDecodeError) as exc:
        emit(
            {
                "source_skill": "quant-stock-technical",
                "schema": SCREEN_SCHEMA,
                "screen_status": "BLOCKED",
                "reason": str(exc),
            },
            args.output,
        )
        return 2
    emit(output, args.output)
    return 0 if output["screen_status"] == "READY" else 2


if __name__ == "__main__":
    sys.exit(main())

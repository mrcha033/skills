#!/usr/bin/env python3
"""Evaluate frozen QTA scores against completed first-60-minute outcomes."""

from __future__ import annotations

import argparse
import csv
import json
import math
import statistics
import sys
import tempfile
from decimal import Decimal, InvalidOperation, ROUND_CEILING, ROUND_HALF_EVEN
from pathlib import Path
from typing import Any, Iterable

import collect_open1h_research as intraday
import fetch_kis_kr_eod as shared
import screen_universe as screen


JOB_SCHEMA = "qta-open1h-evaluation-job/v1"
DATASET_SCHEMA = "qta-open1h-evaluation/v1"
JOB_FIELDS = {
    "schema",
    "screen_path",
    "snapshot_path",
    "output_path",
    "top_fraction",
}
SNAPSHOT_FIELDS = {
    "schema",
    "source_skill",
    "source_contract",
    "manifest_path",
    "manifest_hash",
    "analysis_date",
    "session_date",
    "market",
    "window_start",
    "window_end",
    "interval_minutes",
    "instrument_count",
    "ready_count",
    "blocked_count",
    "cache_hit_count",
    "request_count",
    "retry_count",
    "coverage_counts",
    "records",
    "failures",
    "api_mutation_count",
    "live_enabled",
    "snapshot_hash",
}


class EvaluationBlockedError(ValueError):
    """Raised when a QTA/opening-hour comparison would be incomplete."""


def exact_fields(value: dict[str, Any], expected: set[str], label: str) -> None:
    if set(value) != expected:
        raise EvaluationBlockedError(
            f"{label} fields mismatch; missing={sorted(expected - set(value))}, "
            f"extra={sorted(set(value) - expected)}"
        )


def decimal_value(value: Any, field: str) -> Decimal:
    if isinstance(value, bool):
        raise EvaluationBlockedError(f"{field} must be decimal")
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise EvaluationBlockedError(f"{field} must be decimal") from exc
    if not parsed.is_finite():
        raise EvaluationBlockedError(f"{field} must be finite")
    return parsed


def positive_fraction(value: Any, field: str) -> Decimal:
    parsed = decimal_value(value, field)
    if parsed <= 0 or parsed >= 1:
        raise EvaluationBlockedError(f"{field} must be greater than 0 and below 1")
    return parsed


def absolute_file(value: Any, field: str, *, must_exist: bool) -> Path:
    path = Path(str(value)).expanduser()
    if not path.is_absolute():
        raise EvaluationBlockedError(f"{field} must be absolute")
    if must_exist and (not path.is_file() or path.is_symlink()):
        raise EvaluationBlockedError(
            f"{field} must be a regular non-symlink file"
        )
    return path


def load_object(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise EvaluationBlockedError(f"{label} must be readable JSON") from exc
    if not isinstance(value, dict):
        raise EvaluationBlockedError(f"{label} must be one JSON object")
    return value


def load_job(path: Path) -> dict[str, Any]:
    raw = load_object(path, "evaluation job")
    exact_fields(raw, JOB_FIELDS, "evaluation job")
    if raw["schema"] != JOB_SCHEMA:
        raise EvaluationBlockedError(f"job.schema must be {JOB_SCHEMA}")
    screen_path = absolute_file(raw["screen_path"], "screen_path", must_exist=True)
    snapshot_path = absolute_file(
        raw["snapshot_path"], "snapshot_path", must_exist=True
    )
    output_path = absolute_file(raw["output_path"], "output_path", must_exist=False)
    return {
        "schema": JOB_SCHEMA,
        "screen_path": screen_path,
        "snapshot_path": snapshot_path,
        "output_path": output_path,
        "top_fraction": positive_fraction(raw["top_fraction"], "top_fraction"),
    }


def normalized_screen(path: Path) -> dict[str, Any]:
    value = load_object(path, "screen")
    supplied_hash = value.get("screen_hash")
    if not isinstance(supplied_hash, str):
        raise EvaluationBlockedError("screen_hash is required")
    without_hash = {key: item for key, item in value.items() if key != "screen_hash"}
    if screen.sha256_json(without_hash) != supplied_hash:
        raise EvaluationBlockedError("screen_hash does not match screen")
    if (
        value.get("schema") != screen.SCREEN_SCHEMA_V2
        or value.get("source_skill") != "quant-stock-technical"
        or value.get("method_version") not in {"qta-1.0.0", "qta-2.0.0"}
    ):
        raise EvaluationBlockedError(
            "evaluation requires a qta-screen/v2 QTA 1.0.0 or 2.0.0 artifact"
        )
    decisions = value.get("decisions")
    if not isinstance(decisions, list) or not decisions:
        raise EvaluationBlockedError("screen decisions must be a non-empty array")
    return value


def normalized_snapshot(path: Path) -> dict[str, Any]:
    value = load_object(path, "opening-hour snapshot")
    exact_fields(value, SNAPSHOT_FIELDS, "opening-hour snapshot")
    if value["schema"] != intraday.SNAPSHOT_SCHEMA:
        raise EvaluationBlockedError(
            f"snapshot.schema must be {intraday.SNAPSHOT_SCHEMA}"
        )
    if value["api_mutation_count"] != 0 or value["live_enabled"] is not False:
        raise EvaluationBlockedError("research snapshot must be read-only")
    supplied_hash = value["snapshot_hash"]
    without_hash = {
        key: item for key, item in value.items() if key != "snapshot_hash"
    }
    expected_hash = shared.sha256_bytes(
        shared.canonical_json(without_hash).encode("utf-8")
    )
    if supplied_hash != expected_hash:
        raise EvaluationBlockedError("snapshot_hash does not match snapshot")
    if (
        value["instrument_count"]
        != value["ready_count"] + value["blocked_count"]
    ):
        raise EvaluationBlockedError("snapshot counts do not reconcile")
    if value["ready_count"] != len(value["records"]):
        raise EvaluationBlockedError("snapshot ready_count does not match records")
    if value["blocked_count"] != len(value["failures"]):
        raise EvaluationBlockedError("snapshot blocked_count does not match failures")
    return value


def eod_features(
    ticker_path: Path,
    *,
    analysis_date: str,
) -> dict[str, str]:
    try:
        with ticker_path.open("r", encoding="utf-8-sig", newline="") as handle:
            rows = list(csv.DictReader(handle))
    except OSError as exc:
        raise EvaluationBlockedError(f"cannot read ticker CSV: {ticker_path}") from exc
    selected = [row for row in rows if str(row.get("date")) <= analysis_date]
    if not selected or str(selected[-1].get("date")) != analysis_date:
        raise EvaluationBlockedError(
            f"ticker CSV has no completed {analysis_date} cutoff"
        )
    if len(selected) < 253:
        raise EvaluationBlockedError("ticker CSV needs at least 253 cutoff rows")
    closes = [
        decimal_value(row.get("close"), "ticker close") for row in selected
    ]
    volumes = [
        decimal_value(row.get("volume"), "ticker volume") for row in selected
    ]
    dollar_turnover = [
        closes[index] * volumes[index]
        for index in range(max(0, len(selected) - 20), len(selected))
    ]
    return {
        "previous_close": format(closes[-1], "f"),
        "return_5_bps": intraday.bps(closes[-1], closes[-6]),
        "return_20_bps": intraday.bps(closes[-1], closes[-21]),
        "return_63_bps": intraday.bps(closes[-1], closes[-64]),
        "median_20d_dollar_turnover": format(
            statistics.median(dollar_turnover), "f"
        ),
    }


def first_bar_close_cross(
    bars: list[dict[str, str]],
    entry: Decimal,
) -> str | None:
    previous: Decimal | None = None
    for bar in bars:
        current = Decimal(bar["close"])
        if previous is not None and previous < entry <= current:
            return bar["time"]
        previous = current
    return None


def combine_rows(
    *,
    screen_value: dict[str, Any],
    snapshot: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    if screen_value["manifest_hash"] != snapshot["manifest_hash"]:
        raise EvaluationBlockedError("screen and snapshot manifest hashes differ")
    if screen_value["analysis_date"] != snapshot["analysis_date"]:
        raise EvaluationBlockedError("screen and snapshot analysis dates differ")
    decision_by_key: dict[tuple[str, str], dict[str, Any]] = {}
    for decision in screen_value["decisions"]:
        if not isinstance(decision, dict):
            raise EvaluationBlockedError("screen decision must be an object")
        key = (str(decision.get("exchange")), str(decision.get("canonical_symbol")))
        if key in decision_by_key:
            raise EvaluationBlockedError(f"duplicate screen decision: {key}")
        decision_by_key[key] = decision
    rows: list[dict[str, Any]] = []
    blocked: list[dict[str, str]] = []
    for record in snapshot["records"]:
        if not isinstance(record, dict):
            raise EvaluationBlockedError("snapshot record must be an object")
        key = (str(record.get("exchange")), str(record.get("canonical_symbol")))
        decision = decision_by_key.get(key)
        if decision is None:
            raise EvaluationBlockedError(f"snapshot record has no screen decision: {key}")
        qta = decision.get("qta")
        if (
            not isinstance(qta, dict)
            or qta.get("calculation_status") != "READY"
            or qta.get("method_version") != screen_value["method_version"]
        ):
            blocked.append(
                {
                    "exchange": key[0],
                    "canonical_symbol": key[1],
                    "reason": str(
                        qta.get("reason", "QTA calculation is not READY")
                        if isinstance(qta, dict)
                        else "QTA payload is missing"
                    ),
                }
            )
            continue
        bars_path = absolute_file(
            record.get("bars_path"), "record.bars_path", must_exist=True
        )
        if shared.sha256_file(bars_path) != record.get("bars_file_sha256"):
            raise EvaluationBlockedError(f"bars file hash mismatch: {bars_path}")
        artifact = intraday.normalize_bars_artifact(
            load_object(bars_path, "bars artifact")
        )
        if artifact["bars_hash"] != record.get("bars_hash"):
            raise EvaluationBlockedError(f"bars hash mismatch: {bars_path}")
        instrument = decision.get("instrument")
        if not isinstance(instrument, dict):
            raise EvaluationBlockedError("screen decision instrument is missing")
        ticker_path = absolute_file(
            instrument.get("ticker_csv"), "ticker_csv", must_exist=True
        )
        if shared.sha256_file(ticker_path) != instrument.get("ticker_csv_sha256"):
            raise EvaluationBlockedError(f"ticker CSV hash mismatch: {ticker_path}")
        eod = eod_features(
            ticker_path,
            analysis_date=screen_value["analysis_date"],
        )
        entry = decimal_value(qta["entry_price"], "QTA entry price")
        start_open = Decimal(record["start_open"])
        end_close = Decimal(record["end_close"])
        cross_time = first_bar_close_cross(artifact["bars"], entry)
        rows.append(
            {
                "market": decision["market"],
                "exchange": decision["exchange"],
                "canonical_symbol": decision["canonical_symbol"],
                "broker_symbol": instrument["broker_symbol"],
                "instrument_type": instrument["instrument_type"],
                "analysis_date": screen_value["analysis_date"],
                "session_date": snapshot["session_date"],
                "qta_method_version": qta["method_version"],
                "qta_total_score": format(
                    decimal_value(qta["total_score"], "QTA total score"), "f"
                ),
                "qta_short_score": format(
                    decimal_value(qta["short"]["score"], "QTA short score"), "f"
                ),
                "qta_medium_score": format(
                    decimal_value(qta["medium"]["score"], "QTA medium score"), "f"
                ),
                "qta_long_score": format(
                    decimal_value(qta["long"]["score"], "QTA long score"), "f"
                ),
                "qta_risk_score": format(
                    decimal_value(qta["risk"]["score"], "QTA risk score"), "f"
                ),
                "qta_entry_price": format(entry, "f"),
                "qta_stop_price": format(
                    decimal_value(qta["stop_price"], "QTA stop price"), "f"
                ),
                "qta_take_profit_price": format(
                    decimal_value(
                        qta["take_profit_price"], "QTA take-profit price"
                    ),
                    "f",
                ),
                **eod,
                "opening_gap_bps": intraday.bps(
                    start_open, Decimal(eod["previous_close"])
                ),
                "coverage_status": record["coverage_status"],
                "first_bar_time": record["first_bar_time"],
                "last_bar_time": record["last_bar_time"],
                "bar_count": record["bar_count"],
                "expected_bar_count": record["expected_bar_count"],
                "missing_bar_count": record["missing_bar_count"],
                "coverage_fraction": record["coverage_fraction"],
                "observed_span_minutes": record["observed_span_minutes"],
                "return_60m_bps": record["return_60m_bps"],
                "maximum_excursion_bps": record["maximum_excursion_bps"],
                "minimum_excursion_bps": record["minimum_excursion_bps"],
                "window_volume": record["window_volume"],
                "window_turnover": record["window_turnover"],
                "entry_distance_from_previous_close_bps": intraday.bps(
                    entry, Decimal(eod["previous_close"])
                ),
                "entry_touched_by_minute_high": (
                    Decimal(record["window_high"]) >= entry
                ),
                "entry_crossed_by_bar_close": cross_time is not None,
                "entry_cross_bar_time": cross_time,
                "end_return_from_entry_bps": intraday.bps(end_close, entry),
            }
        )
    rows.sort(key=lambda item: (item["exchange"], item["canonical_symbol"]))
    blocked.sort(key=lambda item: (item["exchange"], item["canonical_symbol"]))
    return rows, blocked


def average_ranks(values: list[Decimal]) -> list[float]:
    order = sorted(range(len(values)), key=lambda index: (values[index], index))
    ranks = [0.0] * len(values)
    cursor = 0
    while cursor < len(order):
        end = cursor + 1
        while end < len(order) and values[order[end]] == values[order[cursor]]:
            end += 1
        average = (cursor + 1 + end) / 2.0
        for position in range(cursor, end):
            ranks[order[position]] = average
        cursor = end
    return ranks


def pearson(left: Iterable[float], right: Iterable[float]) -> float | None:
    x = list(left)
    y = list(right)
    if len(x) != len(y) or len(x) < 2:
        return None
    x_mean = sum(x) / len(x)
    y_mean = sum(y) / len(y)
    numerator = sum((a - x_mean) * (b - y_mean) for a, b in zip(x, y))
    x_scale = math.sqrt(sum((a - x_mean) ** 2 for a in x))
    y_scale = math.sqrt(sum((b - y_mean) ** 2 for b in y))
    if x_scale == 0 or y_scale == 0:
        return None
    return numerator / (x_scale * y_scale)


def rounded_metric(value: float | None) -> str | None:
    if value is None:
        return None
    return format(
        Decimal(str(value)).quantize(
            Decimal("0.000001"), rounding=ROUND_HALF_EVEN
        ),
        "f",
    )


def group_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    returns = [Decimal(row["return_60m_bps"]) for row in rows]
    touched = sum(bool(row["entry_touched_by_minute_high"]) for row in rows)
    crossed = sum(bool(row["entry_crossed_by_bar_close"]) for row in rows)
    positive = sum(value > 0 for value in returns)
    return {
        "count": len(rows),
        "mean_return_60m_bps": (
            format(sum(returns, Decimal(0)) / len(returns), "f")
            if returns
            else None
        ),
        "median_return_60m_bps": (
            format(statistics.median(returns), "f") if returns else None
        ),
        "positive_fraction": (
            format(Decimal(positive) / len(rows), "f") if rows else None
        ),
        "entry_touch_fraction": (
            format(Decimal(touched) / len(rows), "f") if rows else None
        ),
        "bar_close_cross_fraction": (
            format(Decimal(crossed) / len(rows), "f") if rows else None
        ),
    }


def exchange_diagnostics(
    rows: list[dict[str, Any]],
    *,
    top_fraction: Decimal,
) -> dict[str, Any]:
    if not rows:
        raise EvaluationBlockedError("exchange diagnostics require rows")
    qta_scores = [Decimal(row["qta_total_score"]) for row in rows]
    returns = [Decimal(row["return_60m_bps"]) for row in rows]
    pearson_value = pearson(
        [float(value) for value in qta_scores],
        [float(value) for value in returns],
    )
    spearman_value = pearson(
        average_ranks(qta_scores),
        average_ranks(returns),
    )
    top_count = max(
        1,
        int(
            (Decimal(len(rows)) * top_fraction).to_integral_value(
                rounding=ROUND_CEILING
            )
        ),
    )
    by_outcome = sorted(
        rows,
        key=lambda item: (
            -Decimal(item["return_60m_bps"]),
            item["canonical_symbol"],
        ),
    )
    by_score = sorted(
        rows,
        key=lambda item: (
            -Decimal(item["qta_total_score"]),
            item["canonical_symbol"],
        ),
    )
    winner_keys = {
        row["canonical_symbol"] for row in by_outcome[:top_count]
    }
    score_keys = {row["canonical_symbol"] for row in by_score[:top_count]}
    overlap = len(winner_keys & score_keys)
    baseline = Decimal(top_count) / Decimal(len(rows))
    precision = Decimal(overlap) / Decimal(top_count)
    lift = precision / baseline
    deciles: list[dict[str, Any]] = []
    for index in range(10):
        start = len(by_score) * index // 10
        end = len(by_score) * (index + 1) // 10
        bucket = by_score[start:end]
        deciles.append(
            {
                "qta_score_decile": index + 1,
                **group_summary(bucket),
            }
        )

    def compact(row: dict[str, Any]) -> dict[str, Any]:
        return {
            key: row[key]
            for key in (
                "canonical_symbol",
                "qta_total_score",
                "qta_short_score",
                "qta_medium_score",
                "qta_long_score",
                "qta_risk_score",
                "return_5_bps",
                "return_20_bps",
                "median_20d_dollar_turnover",
                "opening_gap_bps",
                "coverage_status",
                "first_bar_time",
                "last_bar_time",
                "bar_count",
                "return_60m_bps",
                "maximum_excursion_bps",
                "minimum_excursion_bps",
                "entry_touched_by_minute_high",
                "entry_crossed_by_bar_close",
                "end_return_from_entry_bps",
            )
        }

    return {
        "count": len(rows),
        "top_fraction": format(top_fraction, "f"),
        "top_count": top_count,
        "pearson_qta_total_vs_return_60m": rounded_metric(pearson_value),
        "spearman_qta_total_vs_return_60m": rounded_metric(spearman_value),
        "top_outcome_overlap_count": overlap,
        "top_outcome_precision": format(precision, "f"),
        "top_outcome_recall": format(precision, "f"),
        "top_outcome_lift": format(lift, "f"),
        "overall": group_summary(rows),
        "qta_score_deciles": deciles,
        "top_20_outcomes": [compact(row) for row in by_outcome[:20]],
        "bottom_20_outcomes": [compact(row) for row in by_outcome[-20:]],
        "top_20_qta_scores": [compact(row) for row in by_score[:20]],
    }


def evaluate(job: dict[str, Any]) -> dict[str, Any]:
    screen_value = normalized_screen(job["screen_path"])
    snapshot = normalized_snapshot(job["snapshot_path"])
    rows, qta_blocked = combine_rows(
        screen_value=screen_value,
        snapshot=snapshot,
    )
    if not rows:
        raise EvaluationBlockedError("no READY QTA/opening-hour joined rows")
    analysis_rows = [
        row
        for row in rows
        if row["coverage_status"]
        in {
            "COMPLETE_GRID",
            "EXACT_ENDPOINTS_SPARSE",
            "NEAR_ENDPOINTS",
        }
    ]
    if not analysis_rows:
        raise EvaluationBlockedError(
            "no joined rows have complete or near-complete opening-hour endpoints"
        )
    diagnostics = {
        exchange: exchange_diagnostics(
            [row for row in analysis_rows if row["exchange"] == exchange],
            top_fraction=job["top_fraction"],
        )
        for exchange in intraday.MARKET_CONTRACTS[snapshot["market"]]["exchanges"]
        if any(row["exchange"] == exchange for row in analysis_rows)
    }
    without_hash = {
        "schema": DATASET_SCHEMA,
        "source_skill": "quant-stock-technical",
        "baseline_method_version": screen_value["method_version"],
        "target_definition": (
            "last observed close divided by first observed open; no interpolation; "
            "COMPLETE_GRID, EXACT_ENDPOINTS_SPARSE, and NEAR_ENDPOINTS only"
        ),
        "market": snapshot["market"],
        "analysis_date": snapshot["analysis_date"],
        "session_date": snapshot["session_date"],
        "screen_path": str(job["screen_path"]),
        "screen_hash": screen_value["screen_hash"],
        "snapshot_path": str(job["snapshot_path"]),
        "snapshot_hash": snapshot["snapshot_hash"],
        "top_fraction": format(job["top_fraction"], "f"),
        "joined_count": len(rows),
        "analysis_eligible_count": len(analysis_rows),
        "coverage_counts": snapshot["coverage_counts"],
        "intraday_blocked_count": snapshot["blocked_count"],
        "qta_blocked_after_join_count": len(qta_blocked),
        "diagnostics_by_exchange": diagnostics,
        "rows": rows,
        "qta_blocked_after_join": qta_blocked,
        "api_mutation_count": 0,
        "live_enabled": False,
    }
    output = {
        **without_hash,
        "evaluation_hash": shared.sha256_bytes(
            shared.canonical_json(without_hash).encode("utf-8")
        ),
    }
    shared.atomic_write_json(job["output_path"], output)
    return output


def self_test() -> None:
    sample = [
        {
            "canonical_symbol": f"T{index}",
            "qta_total_score": str(index),
            "qta_short_score": str(index),
            "qta_medium_score": str(index),
            "qta_long_score": str(index),
            "qta_risk_score": str(100 - index),
            "return_5_bps": str(index),
            "return_20_bps": str(index),
            "median_20d_dollar_turnover": str(index * 1000000),
            "opening_gap_bps": "0",
            "coverage_status": "COMPLETE_GRID",
            "first_bar_time": "093000",
            "last_bar_time": "103000",
            "bar_count": 13,
            "return_60m_bps": str(index * 2),
            "maximum_excursion_bps": str(index * 3),
            "minimum_excursion_bps": str(-index),
            "entry_touched_by_minute_high": index % 2 == 0,
            "entry_crossed_by_bar_close": index % 3 == 0,
            "end_return_from_entry_bps": str(index),
        }
        for index in range(1, 21)
    ]
    diagnostics = exchange_diagnostics(sample, top_fraction=Decimal("0.1"))
    assert diagnostics["spearman_qta_total_vs_return_60m"] == "1.000000"
    assert diagnostics["top_outcome_overlap_count"] == 2
    bars = intraday.synthetic_rows(market="US", session_date="2026-07-28")
    artifact = intraday.bars_artifact(
        instrument={
            "exchange": "NASDAQ",
            "canonical_symbol": "TEST",
            "broker_symbol": "TEST",
        },
        market="US",
        session_date="2026-07-28",
        raw_rows=bars,
    )
    assert first_bar_close_cross(artifact["bars"], Decimal("101.2")) is not None
    with tempfile.TemporaryDirectory(prefix="qta-open1h-evaluation-") as temporary:
        output = Path(temporary) / "result.json"
        shared.atomic_write_json(output, {"self_test": "PASS"})
        assert load_object(output, "self-test")["self_test"] == "PASS"
    print(
        shared.canonical_json(
            {
                "self_test": "PASS",
                "schema": DATASET_SCHEMA,
                "api_mutation_count": 0,
                "live_enabled": False,
            }
        )
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--self-test", action="store_true")
    subparsers = parser.add_subparsers(dest="command")
    evaluate_parser = subparsers.add_parser("evaluate")
    evaluate_parser.add_argument("--job", required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        if args.self_test:
            self_test()
            return 0
        if args.command != "evaluate":
            raise EvaluationBlockedError("use evaluate --job or --self-test")
        job_path = absolute_file(args.job, "job path", must_exist=True)
        result = evaluate(load_job(job_path))
    except (
        EvaluationBlockedError,
        intraday.ResearchBlockedError,
        screen.ScreenBlockedError,
        OSError,
        json.JSONDecodeError,
    ) as exc:
        print(
            shared.canonical_json(
                {
                    "status": "BLOCKED",
                    "reason": str(exc),
                    "api_mutation_count": 0,
                    "live_enabled": False,
                }
            ),
            file=sys.stderr,
        )
        return 2
    print(
        shared.canonical_json(
            {
                "status": "READY",
                "market": result["market"],
                "joined_count": result["joined_count"],
                "evaluation_hash": result["evaluation_hash"],
                "api_mutation_count": 0,
                "live_enabled": False,
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

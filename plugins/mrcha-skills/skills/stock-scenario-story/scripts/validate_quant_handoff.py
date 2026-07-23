#!/usr/bin/env python3
"""Validate the mandatory quant-stock-technical handoff contract."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
import tempfile
from datetime import date
from pathlib import Path
from typing import Any


SOURCE_SKILL = "quant-stock-technical"
RESULT_SCHEMA = "quant-stock-technical/v1"
SUPPORTED_METHODS = {"qta-1.0.0"}
MIN_SHARED_SESSIONS = 756
MIN_REFERENCE_OBSERVATIONS = 252


class GateError(ValueError):
    pass


def _mapping(value: Any, field: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise GateError(f"{field} must be an object")
    return value


def _text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise GateError(f"{field} must be a non-empty string")
    return value.strip()


def _number(value: Any, field: str, minimum: float | None = None, maximum: float | None = None) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise GateError(f"{field} must be numeric")
    numeric = float(value)
    if not math.isfinite(numeric):
        raise GateError(f"{field} must be finite")
    if minimum is not None and numeric < minimum:
        raise GateError(f"{field} must be at least {minimum}")
    if maximum is not None and numeric > maximum:
        raise GateError(f"{field} must be at most {maximum}")
    return numeric


def expected_opinion(score: float) -> str:
    if score >= 80:
        return "강한 긍정"
    if score >= 60:
        return "긍정"
    if score >= 40:
        return "중립"
    if score >= 20:
        return "부정"
    return "강한 부정"


def validate(payload: dict[str, Any]) -> dict[str, Any]:
    if payload.get("source_skill") != SOURCE_SKILL:
        raise GateError(f"source_skill must equal {SOURCE_SKILL}")
    if payload.get("result_schema") != RESULT_SCHEMA:
        raise GateError(f"result_schema must equal {RESULT_SCHEMA}")
    if payload.get("calculation_status") != "READY":
        raise GateError("calculation_status must equal READY")
    method = _text(payload.get("method_version"), "method_version")
    if method not in SUPPORTED_METHODS:
        raise GateError(f"unsupported method_version: {method}")
    setup_status = _text(payload.get("setup_status"), "setup_status")
    if setup_status not in {"READY", "CONDITIONAL"}:
        raise GateError("setup_status must be READY or CONDITIONAL")

    analysis_date = _text(payload.get("analysis_date"), "analysis_date")
    try:
        date.fromisoformat(analysis_date)
    except ValueError as exc:
        raise GateError("analysis_date must be YYYY-MM-DD") from exc
    market = _text(payload.get("market"), "market")
    ticker = _text(payload.get("ticker"), "ticker")
    source_name = _text(payload.get("source_name"), "source_name")
    score_basis = _text(payload.get("score_basis"), "score_basis")
    if "not probability" not in score_basis.lower():
        raise GateError("score_basis must state that the score is not a probability")

    horizons: dict[str, dict[str, Any]] = {}
    for name in ("short", "medium", "long"):
        horizon = _mapping(payload.get(name), name)
        score = _number(horizon.get("score"), f"{name}.score", 0, 100)
        label = _text(horizon.get("opinion"), f"{name}.opinion")
        if label != expected_opinion(score):
            raise GateError(f"{name}.opinion does not match its score")
        horizons[name] = {"score": score, "opinion": label}

    risk = _mapping(payload.get("risk"), "risk")
    risk_score = _number(risk.get("score"), "risk.score", 0, 100)
    risk_counterpoint = _text(risk.get("counterpoint"), "risk.counterpoint")
    total_score = _number(payload.get("total_score"), "total_score", 0, 100)
    entry = _number(payload.get("entry_price"), "entry_price", 0)
    stop = _number(payload.get("stop_price"), "stop_price", 0)
    target = _number(payload.get("take_profit_price"), "take_profit_price", 0)
    if not stop < entry < target:
        raise GateError("prices must satisfy stop_price < entry_price < take_profit_price")

    shared_sessions = int(_number(payload.get("shared_sessions"), "shared_sessions", MIN_SHARED_SESSIONS))
    references = _mapping(payload.get("reference_observations"), "reference_observations")
    if not references:
        raise GateError("reference_observations cannot be empty")
    for name, value in references.items():
        _number(value, f"reference_observations.{name}", MIN_REFERENCE_OBSERVATIONS)
    assumptions = payload.get("assumptions")
    if not isinstance(assumptions, list) or not assumptions or not all(isinstance(item, str) and item.strip() for item in assumptions):
        raise GateError("assumptions must be a non-empty string array")

    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return {
        "gate_status": "PASSED",
        "validator": "stock-scenario-story/quant-handoff-v1",
        "quant_input_sha256": hashlib.sha256(canonical).hexdigest(),
        "source_skill": SOURCE_SKILL,
        "result_schema": RESULT_SCHEMA,
        "method_version": method,
        "analysis_date": analysis_date,
        "market": market,
        "ticker": ticker,
        "source_name": source_name,
        "setup_status": setup_status,
        "immutable_anchors": {
            "short": horizons["short"],
            "medium": horizons["medium"],
            "long": horizons["long"],
            "risk": {"score": risk_score, "counterpoint": risk_counterpoint},
            "entry_price": entry,
            "stop_price": stop,
            "take_profit_price": target,
            "total_score": total_score,
            "score_basis": score_basis,
        },
        "shared_sessions": shared_sessions,
    }


def valid_fixture() -> dict[str, Any]:
    return {
        "source_skill": SOURCE_SKILL,
        "result_schema": RESULT_SCHEMA,
        "calculation_status": "READY",
        "setup_status": "READY",
        "method_version": "qta-1.0.0",
        "analysis_date": "2026-07-17",
        "market": "NASDAQ",
        "ticker": "TEST",
        "source_name": "self-test",
        "shared_sessions": 1000,
        "score_basis": "ticker-relative historical percentile; not probability of profit",
        "short": {"opinion": "긍정", "score": 65.0},
        "medium": {"opinion": "중립", "score": 55.0},
        "long": {"opinion": "강한 긍정", "score": 82.0},
        "risk": {"score": 71.0, "counterpoint": "ATR14/close 8.00% (과거 백분위 92.0), 진입-손절 거리 16.00%"},
        "entry_price": 100.0,
        "stop_price": 84.0,
        "take_profit_price": 132.0,
        "total_score": 64.0,
        "reference_observations": {"return_5": 500, "return_252": 500},
        "assumptions": ["completed daily sessions"],
    }


def self_test() -> None:
    receipt = validate(valid_fixture())
    assert receipt["gate_status"] == "PASSED"
    broken = valid_fixture()
    broken["source_skill"] = "manual-summary"
    try:
        validate(broken)
    except GateError:
        pass
    else:
        raise AssertionError("invalid source_skill was accepted")
    with tempfile.TemporaryDirectory(prefix="scenario-gate-") as directory:
        path = Path(directory) / "receipt.json"
        path.write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        assert json.loads(path.read_text(encoding="utf-8"))["quant_input_sha256"] == receipt["quant_input_sha256"]
    print(json.dumps({"self_test": "PASS", "validator": receipt["validator"]}, ensure_ascii=False))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", nargs="?")
    parser.add_argument("--output")
    parser.add_argument("--self-test", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.self_test:
        self_test()
        return 0
    if not args.input:
        result = {"gate_status": "BLOCKED", "reason": "missing quant result JSON", "validator": "stock-scenario-story/quant-handoff-v1"}
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 2
    try:
        payload = json.loads(Path(args.input).read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise GateError("quant result root must be an object")
        result = validate(payload)
    except (OSError, json.JSONDecodeError, GateError, ValueError) as exc:
        result = {"gate_status": "BLOCKED", "reason": str(exc), "validator": "stock-scenario-story/quant-handoff-v1"}
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 2
    rendered = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        Path(args.output).write_text(rendered, encoding="utf-8")
    print(rendered, end="")
    return 0


if __name__ == "__main__":
    sys.exit(main())

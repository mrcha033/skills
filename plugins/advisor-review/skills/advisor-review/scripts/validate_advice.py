#!/usr/bin/env python3
"""Validate the structured output of an advisor-style reviewer."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


VERDICTS = {"proceed", "revise", "stop", "need_evidence"}
LIST_FIELDS = (
    "critical_risks",
    "assumptions_to_test",
    "recommended_next_steps",
    "evidence_conflicts",
)
REQUIRED_FIELDS = {"verdict", *LIST_FIELDS}
MAX_ITEMS = 8
MAX_ITEM_CHARS = 2_000


class AdviceError(ValueError):
    """Raised when reviewer output violates the advice schema."""


def validate(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise AdviceError("advice must be a JSON object")
    missing = REQUIRED_FIELDS - value.keys()
    extra = value.keys() - REQUIRED_FIELDS
    if missing:
        raise AdviceError(f"missing fields: {', '.join(sorted(missing))}")
    if extra:
        raise AdviceError(f"unexpected fields: {', '.join(sorted(extra))}")
    if value["verdict"] not in VERDICTS:
        raise AdviceError(
            f"verdict must be one of: {', '.join(sorted(VERDICTS))}"
        )

    normalized: dict[str, Any] = {"verdict": value["verdict"]}
    for field in LIST_FIELDS:
        items = value[field]
        if not isinstance(items, list):
            raise AdviceError(f"{field} must be a list of strings")
        if len(items) > MAX_ITEMS:
            raise AdviceError(f"{field} must contain at most {MAX_ITEMS} items")
        normalized_items: list[str] = []
        for index, item in enumerate(items):
            if not isinstance(item, str) or not item.strip():
                raise AdviceError(f"{field}[{index}] must be a non-empty string")
            text = item.strip()
            if len(text) > MAX_ITEM_CHARS:
                raise AdviceError(
                    f"{field}[{index}] must be at most {MAX_ITEM_CHARS} characters"
                )
            normalized_items.append(text)
        normalized[field] = normalized_items
    return normalized


def load_input(path: str) -> Any:
    if path == "-":
        return json.load(sys.stdin)
    return json.loads(Path(path).read_text(encoding="utf-8"))


def self_test() -> None:
    valid = {
        "verdict": "revise",
        "critical_risks": ["Remote publication is not yet verified."],
        "assumptions_to_test": [],
        "recommended_next_steps": ["Verify the remote branch SHA."],
        "evidence_conflicts": [],
    }
    assert validate(valid) == valid
    for invalid in (
        {**valid, "verdict": "maybe"},
        {key: item for key, item in valid.items() if key != "critical_risks"},
        {**valid, "extra": []},
        {**valid, "critical_risks": [""]},
    ):
        try:
            validate(invalid)
        except AdviceError:
            pass
        else:
            raise AssertionError(f"invalid advice was accepted: {invalid}")
    print(json.dumps({"self_test": "PASS", "schema": "advisor-advice-1.0"}))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", nargs="?", default="-", help="JSON file or -")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        self_test()
        return
    normalized = validate(load_input(args.input))
    print(
        json.dumps(
            {"status": "valid", "advice": normalized},
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    try:
        main()
    except (AdviceError, OSError, json.JSONDecodeError) as exc:
        print(f"INVALID: {exc}", file=sys.stderr)
        raise SystemExit(2)

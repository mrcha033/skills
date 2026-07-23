#!/usr/bin/env python3
"""Reject reader-facing stock stories that expose numerical expressions."""

from __future__ import annotations

import argparse
import json
import re
import sys
import tempfile
from pathlib import Path


DIGIT_RE = re.compile(r"[0-9\u0660-\u0669\u06f0-\u06f9\uff10-\uff19]")
CURRENCY_OR_RATE_RE = re.compile(r"[%$₩€£¥₹₽¢‰]")
QUANT_TERM_RE = re.compile(
    r"\b(?:percent|percentage|dollar|dollars|won|euro|euros|yen|quarter|quarters|"
    r"million|billion|trillion|double|doubled|triple|tripled|half|halved|rank|ranked)\b|"
    r"퍼센트|백분율|달러|유로|엔화|분기|매출액|영업이익률|시가총액|거래량|거래대금|"
    r"두\s*배|세\s*배|절반|반토막|몇\s*배|순위",
    re.IGNORECASE,
)
LINK_DESTINATION_RE = re.compile(r"\]\((?:[^()]|\([^)]*\))*\)")


def visible_markdown(text: str) -> str:
    return LINK_DESTINATION_RE.sub("]()", text)


def validate(text: str) -> list[str]:
    visible = visible_markdown(text)
    errors: list[str] = []
    if DIGIT_RE.search(visible):
        errors.append("visible text contains a digit")
    if CURRENCY_OR_RATE_RE.search(visible):
        errors.append("visible text contains a currency or rate symbol")
    match = QUANT_TERM_RE.search(visible)
    if match:
        errors.append(f"visible text contains a quantitative term: {match.group(0)}")
    return errors


def self_test() -> None:
    passing = "이 회사는 거대한 무대의 주인공처럼 등장한다. [공식 회사 자료](https://example.com/report2026)"
    assert validate(passing) == []
    assert validate("점수는 80점이다")
    assert validate("매출이 두 배가 됐다")
    with tempfile.TemporaryDirectory(prefix="story-text-gate-") as directory:
        path = Path(directory) / "story.md"
        path.write_text(passing, encoding="utf-8")
        assert validate(path.read_text(encoding="utf-8")) == []
    print(json.dumps({"self_test": "PASS", "validator": "stock-scenario-story/story-text-v1"}, ensure_ascii=False))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", nargs="?")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        self_test()
        return 0
    if not args.input:
        print(json.dumps({"story_gate": "BLOCKED", "errors": ["missing story file"]}, ensure_ascii=False, indent=2))
        return 2
    try:
        text = Path(args.input).read_text(encoding="utf-8")
    except OSError as exc:
        print(json.dumps({"story_gate": "BLOCKED", "errors": [str(exc)]}, ensure_ascii=False, indent=2))
        return 2
    errors = validate(text)
    result = {"story_gate": "BLOCKED" if errors else "PASSED", "errors": errors}
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 2 if errors else 0


if __name__ == "__main__":
    sys.exit(main())

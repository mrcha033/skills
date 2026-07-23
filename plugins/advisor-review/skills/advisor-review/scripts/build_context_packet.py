#!/usr/bin/env python3
"""Build a bounded, redacted context packet for an advisor-style review."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any


SCHEMA_VERSION = "advisor-context-1.0"
PHASES = {"plan", "stuck", "pivot", "final"}
LIST_FIELDS = ("constraints", "evidence", "changes", "validation", "conflicts")
MAX_ITEM_CHARS = 2_000
MAX_ITEMS = 20
MAX_SCALAR_CHARS = 8_000

SECRET_PATTERNS = (
    re.compile(r"\b(?:sk|gh[opusr])_[A-Za-z0-9_-]{12,}\b"),
    re.compile(r"\bAKIA[A-Z0-9]{16}\b"),
    re.compile(r"(?i)\b(bearer\s+)[A-Za-z0-9._~+/=-]{12,}"),
    re.compile(
        r"(?i)\b(api[_-]?key|access[_-]?token|auth[_-]?token|password|secret)"
        r"(\s*[:=]\s*)[^\s,;]+"
    ),
)


class PacketError(ValueError):
    """Raised when input cannot satisfy the packet contract."""


def redact(text: str) -> str:
    value = text
    value = SECRET_PATTERNS[0].sub("[REDACTED_TOKEN]", value)
    value = SECRET_PATTERNS[1].sub("[REDACTED_AWS_KEY]", value)
    value = SECRET_PATTERNS[2].sub(r"\1[REDACTED_TOKEN]", value)
    value = SECRET_PATTERNS[3].sub(r"\1\2[REDACTED]", value)
    return value


def bounded_text(value: Any, field: str, limit: int) -> str:
    if not isinstance(value, str):
        raise PacketError(f"{field} must be a string")
    text = redact(value.strip())
    if not text:
        raise PacketError(f"{field} must not be empty")
    if len(text) > limit:
        text = text[: limit - 15] + "...[TRUNCATED]"
    return text


def bounded_list(value: Any, field: str) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise PacketError(f"{field} must be a list of strings")
    if len(value) > MAX_ITEMS:
        raise PacketError(f"{field} must contain at most {MAX_ITEMS} items")
    items: list[str] = []
    for index, item in enumerate(value):
        items.append(bounded_text(item, f"{field}[{index}]", MAX_ITEM_CHARS))
    return items


def build_packet(raw: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise PacketError("input must be a JSON object")

    phase = raw.get("phase")
    if phase not in PHASES:
        raise PacketError(f"phase must be one of: {', '.join(sorted(PHASES))}")

    packet: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "phase": phase,
        "task": bounded_text(raw.get("task"), "task", MAX_SCALAR_CHARS),
        "constraints": bounded_list(raw.get("constraints", []), "constraints"),
        "evidence": bounded_list(raw.get("evidence", []), "evidence"),
        "proposal": bounded_text(raw.get("proposal"), "proposal", MAX_SCALAR_CHARS),
        "changes": bounded_list(raw.get("changes", []), "changes"),
        "validation": bounded_list(raw.get("validation", []), "validation"),
        "conflicts": bounded_list(raw.get("conflicts", []), "conflicts"),
    }
    canonical = json.dumps(
        packet, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    packet["context_hash"] = hashlib.sha256(canonical).hexdigest()
    return packet


def load_input(path: str) -> dict[str, Any]:
    if path == "-":
        value = json.load(sys.stdin)
    else:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise PacketError("input must be a JSON object")
    return value


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", help="Input JSON file, or - for stdin")
    parser.add_argument("--output", help="Output JSON file; defaults to stdout")
    parser.add_argument("--phase", choices=sorted(PHASES))
    parser.add_argument("--task")
    parser.add_argument("--proposal")
    for field in LIST_FIELDS:
        parser.add_argument(f"--{field[:-1] if field.endswith('s') else field}", action="append")
    parser.add_argument("--self-test", action="store_true")
    return parser.parse_args()


def raw_from_args(args: argparse.Namespace) -> dict[str, Any]:
    return {
        "phase": args.phase,
        "task": args.task,
        "proposal": args.proposal,
        "constraints": args.constraint or [],
        "evidence": args.evidence or [],
        "changes": args.change or [],
        "validation": args.validation or [],
        "conflicts": args.conflict or [],
    }


def self_test() -> None:
    raw = {
        "phase": "final",
        "task": "Ship the requested skill.",
        "constraints": ["Do not expose api_key=very-secret-value"],
        "evidence": ["Focused tests passed."],
        "proposal": "Declare the implementation complete.",
        "changes": ["Added deterministic validators."],
        "validation": ["python -B test.py: PASS"],
        "conflicts": [],
    }
    first = build_packet(raw)
    second = build_packet(raw)
    assert first == second
    assert first["schema_version"] == SCHEMA_VERSION
    assert "[REDACTED]" in first["constraints"][0]
    assert len(first["context_hash"]) == 64
    try:
        build_packet({**raw, "phase": "unknown"})
    except PacketError:
        pass
    else:
        raise AssertionError("invalid phase was accepted")
    print(json.dumps({"self_test": "PASS", "schema_version": SCHEMA_VERSION}))


def main() -> None:
    args = parse_args()
    if args.self_test:
        self_test()
        return
    individual_fields = (
        args.phase,
        args.task,
        args.proposal,
        args.constraint,
        args.evidence,
        args.change,
        args.validation,
        args.conflict,
    )
    if args.input and any(value is not None for value in individual_fields):
        raise PacketError("--input cannot be combined with individual packet fields")
    raw = load_input(args.input) if args.input else raw_from_args(args)
    packet = build_packet(raw)
    rendered = json.dumps(packet, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        Path(args.output).write_text(rendered, encoding="utf-8")
    else:
        sys.stdout.write(rendered)


if __name__ == "__main__":
    try:
        main()
    except (PacketError, OSError, json.JSONDecodeError) as exc:
        print(f"BLOCKED: {exc}", file=sys.stderr)
        raise SystemExit(2)

#!/usr/bin/env python3
"""Validate evidence-linked Advisor Review output."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any


SCHEMA_VERSION = "advisor-advice-2.0"
VERDICTS = {"proceed", "revise", "stop", "need_evidence"}
CONFIDENCE_LEVELS = {"low", "medium", "high"}
FINDING_KINDS = {"fact", "inference", "conflict", "gap"}
RISK_LEVELS = {"read_only", "reversible", "destructive"}
MAX_ITEMS = 8
MAX_ITEM_CHARS = 2_000
MIN_SUBSTANTIVE_CHARS = 12
REF_PATTERN = re.compile(r"^(?:C|E|A|CH|V|K|L|D)\d+$")

REQUIRED_FIELDS = {
    "schema_version",
    "verdict",
    "diagnosis",
    "findings",
    "experiments",
    "recommendations",
    "missing_evidence",
    "do_not_do",
}

OUTPUT_SCHEMA = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "additionalProperties": False,
    "required": sorted(REQUIRED_FIELDS),
    "properties": {
        "schema_version": {"type": "string", "const": SCHEMA_VERSION},
        "verdict": {"type": "string", "enum": sorted(VERDICTS)},
        "diagnosis": {
            "type": "object",
            "additionalProperties": False,
            "required": ["summary", "confidence", "evidence_refs"],
            "properties": {
                "summary": {
                    "type": "string",
                    "minLength": MIN_SUBSTANTIVE_CHARS,
                    "maxLength": MAX_ITEM_CHARS,
                },
                "confidence": {
                    "type": "string",
                    "enum": sorted(CONFIDENCE_LEVELS),
                },
                "evidence_refs": {
                    "type": "array",
                    "minItems": 1,
                    "maxItems": MAX_ITEMS,
                    "items": {"type": "string", "pattern": REF_PATTERN.pattern},
                },
            },
        },
        "findings": {
            "type": "array",
            "minItems": 1,
            "maxItems": MAX_ITEMS,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["id", "kind", "claim", "evidence_refs", "impact"],
                "properties": {
                    "id": {"type": "string", "pattern": "^F[1-8]$"},
                    "kind": {"type": "string", "enum": sorted(FINDING_KINDS)},
                    "claim": {
                        "type": "string",
                        "minLength": MIN_SUBSTANTIVE_CHARS,
                        "maxLength": MAX_ITEM_CHARS,
                    },
                    "evidence_refs": {
                        "type": "array",
                        "minItems": 1,
                        "maxItems": MAX_ITEMS,
                        "items": {"type": "string", "pattern": REF_PATTERN.pattern},
                    },
                    "impact": {
                        "type": "string",
                        "minLength": MIN_SUBSTANTIVE_CHARS,
                        "maxLength": MAX_ITEM_CHARS,
                    },
                },
            },
        },
        "experiments": {
            "type": "array",
            "maxItems": MAX_ITEMS,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": [
                    "id",
                    "priority",
                    "action",
                    "distinguishes",
                    "success_signal",
                    "failure_signal",
                    "stop_condition",
                    "risk",
                ],
                "properties": {
                    "id": {"type": "string", "pattern": "^T[1-8]$"},
                    "priority": {"type": "integer", "minimum": 1, "maximum": 8},
                    "action": {
                        "type": "string",
                        "minLength": MIN_SUBSTANTIVE_CHARS,
                        "maxLength": MAX_ITEM_CHARS,
                    },
                    "distinguishes": {
                        "type": "string",
                        "minLength": MIN_SUBSTANTIVE_CHARS,
                        "maxLength": MAX_ITEM_CHARS,
                    },
                    "success_signal": {
                        "type": "string",
                        "minLength": MIN_SUBSTANTIVE_CHARS,
                        "maxLength": MAX_ITEM_CHARS,
                    },
                    "failure_signal": {
                        "type": "string",
                        "minLength": MIN_SUBSTANTIVE_CHARS,
                        "maxLength": MAX_ITEM_CHARS,
                    },
                    "stop_condition": {
                        "type": "string",
                        "minLength": MIN_SUBSTANTIVE_CHARS,
                        "maxLength": MAX_ITEM_CHARS,
                    },
                    "risk": {"type": "string", "enum": sorted(RISK_LEVELS)},
                },
            },
        },
        "recommendations": {
            "type": "array",
            "maxItems": MAX_ITEMS,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": [
                    "id",
                    "priority",
                    "action",
                    "why",
                    "evidence_refs",
                    "risk",
                ],
                "properties": {
                    "id": {"type": "string", "pattern": "^R[1-8]$"},
                    "priority": {"type": "integer", "minimum": 1, "maximum": 8},
                    "action": {
                        "type": "string",
                        "minLength": MIN_SUBSTANTIVE_CHARS,
                        "maxLength": MAX_ITEM_CHARS,
                    },
                    "why": {
                        "type": "string",
                        "minLength": MIN_SUBSTANTIVE_CHARS,
                        "maxLength": MAX_ITEM_CHARS,
                    },
                    "evidence_refs": {
                        "type": "array",
                        "minItems": 1,
                        "maxItems": MAX_ITEMS,
                        "items": {"type": "string", "pattern": REF_PATTERN.pattern},
                    },
                    "risk": {"type": "string", "enum": sorted(RISK_LEVELS)},
                },
            },
        },
        "missing_evidence": {
            "type": "array",
            "maxItems": MAX_ITEMS,
            "items": {
                "type": "string",
                "minLength": MIN_SUBSTANTIVE_CHARS,
                "maxLength": MAX_ITEM_CHARS,
            },
        },
        "do_not_do": {
            "type": "array",
            "maxItems": MAX_ITEMS,
            "items": {
                "type": "string",
                "minLength": MIN_SUBSTANTIVE_CHARS,
                "maxLength": MAX_ITEM_CHARS,
            },
        },
    },
}


class AdviceError(ValueError):
    """Raised when reviewer output violates the advice contract."""


def _exact_keys(value: dict[str, Any], required: set[str], field: str) -> None:
    missing = required - value.keys()
    extra = value.keys() - required
    if missing:
        raise AdviceError(f"{field} is missing: {', '.join(sorted(missing))}")
    if extra:
        raise AdviceError(f"{field} has unexpected fields: {', '.join(sorted(extra))}")


def _text(value: Any, field: str) -> str:
    if not isinstance(value, str):
        raise AdviceError(f"{field} must be a string")
    normalized = value.strip()
    if len(normalized) < MIN_SUBSTANTIVE_CHARS:
        raise AdviceError(
            f"{field} must contain a substantive claim, not generic advice"
        )
    if len(normalized) > MAX_ITEM_CHARS:
        raise AdviceError(f"{field} must be at most {MAX_ITEM_CHARS} characters")
    return normalized


def _string_list(
    value: Any,
    field: str,
    *,
    allow_empty: bool = True,
) -> list[str]:
    if not isinstance(value, list):
        raise AdviceError(f"{field} must be a list")
    if not allow_empty and not value:
        raise AdviceError(f"{field} must not be empty")
    if len(value) > MAX_ITEMS:
        raise AdviceError(f"{field} must contain at most {MAX_ITEMS} items")
    return [_text(item, f"{field}[{index}]") for index, item in enumerate(value)]


def _refs(
    value: Any,
    field: str,
    *,
    known_refs: set[str] | None,
) -> list[str]:
    if not isinstance(value, list) or not value:
        raise AdviceError(f"{field} must contain at least one evidence reference")
    if len(value) > MAX_ITEMS:
        raise AdviceError(f"{field} must contain at most {MAX_ITEMS} references")
    normalized: list[str] = []
    for index, item in enumerate(value):
        if not isinstance(item, str) or not REF_PATTERN.fullmatch(item):
            raise AdviceError(f"{field}[{index}] is not a valid context reference")
        if known_refs is not None and item not in known_refs:
            raise AdviceError(f"{field}[{index}] references unknown context id {item}")
        if item in normalized:
            raise AdviceError(f"{field} contains duplicate reference {item}")
        normalized.append(item)
    return normalized


def _check_sequence(items: list[dict[str, Any]], prefix: str, field: str) -> None:
    expected_ids = [f"{prefix}{index}" for index in range(1, len(items) + 1)]
    actual_ids = [item["id"] for item in items]
    if actual_ids != expected_ids:
        raise AdviceError(f"{field} ids must be ordered as {', '.join(expected_ids)}")
    priorities = [item["priority"] for item in items if "priority" in item]
    if priorities and priorities != list(range(1, len(items) + 1)):
        raise AdviceError(f"{field} priorities must be ordered from 1 without gaps")


def known_refs_from_packet(packet: dict[str, Any]) -> set[str]:
    refs: set[str] = set()
    for field in (
        "constraints",
        "evidence",
        "attempts",
        "changes",
        "validation",
        "conflicts",
        "limitations",
        "artifacts",
    ):
        for item in packet.get(field, []):
            if isinstance(item, dict) and isinstance(item.get("id"), str):
                refs.add(item["id"])
    return refs


def validate(
    value: Any,
    *,
    known_refs: set[str] | None = None,
) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise AdviceError("advice must be a JSON object")
    _exact_keys(value, REQUIRED_FIELDS, "advice")
    if value["schema_version"] != SCHEMA_VERSION:
        raise AdviceError(f"schema_version must be {SCHEMA_VERSION}")
    if value["verdict"] not in VERDICTS:
        raise AdviceError(f"verdict must be one of: {', '.join(sorted(VERDICTS))}")

    diagnosis = value["diagnosis"]
    if not isinstance(diagnosis, dict):
        raise AdviceError("diagnosis must be an object")
    _exact_keys(
        diagnosis,
        {"summary", "confidence", "evidence_refs"},
        "diagnosis",
    )
    if diagnosis["confidence"] not in CONFIDENCE_LEVELS:
        raise AdviceError(
            f"diagnosis.confidence must be one of: "
            f"{', '.join(sorted(CONFIDENCE_LEVELS))}"
        )
    normalized_diagnosis = {
        "summary": _text(diagnosis["summary"], "diagnosis.summary"),
        "confidence": diagnosis["confidence"],
        "evidence_refs": _refs(
            diagnosis["evidence_refs"],
            "diagnosis.evidence_refs",
            known_refs=known_refs,
        ),
    }

    findings = value["findings"]
    if not isinstance(findings, list) or not findings:
        raise AdviceError("findings must contain at least one evidence-linked finding")
    if len(findings) > MAX_ITEMS:
        raise AdviceError(f"findings must contain at most {MAX_ITEMS} items")
    normalized_findings: list[dict[str, Any]] = []
    finding_keys = {"id", "kind", "claim", "evidence_refs", "impact"}
    for index, finding in enumerate(findings):
        if not isinstance(finding, dict):
            raise AdviceError(f"findings[{index}] must be an object")
        _exact_keys(finding, finding_keys, f"findings[{index}]")
        if finding["kind"] not in FINDING_KINDS:
            raise AdviceError(
                f"findings[{index}].kind must be one of: "
                f"{', '.join(sorted(FINDING_KINDS))}"
            )
        normalized_findings.append(
            {
                "id": finding["id"],
                "kind": finding["kind"],
                "claim": _text(finding["claim"], f"findings[{index}].claim"),
                "evidence_refs": _refs(
                    finding["evidence_refs"],
                    f"findings[{index}].evidence_refs",
                    known_refs=known_refs,
                ),
                "impact": _text(finding["impact"], f"findings[{index}].impact"),
            }
        )
    _check_sequence(normalized_findings, "F", "findings")

    experiments = value["experiments"]
    if not isinstance(experiments, list) or len(experiments) > MAX_ITEMS:
        raise AdviceError(f"experiments must be a list of at most {MAX_ITEMS} items")
    normalized_experiments: list[dict[str, Any]] = []
    experiment_keys = {
        "id",
        "priority",
        "action",
        "distinguishes",
        "success_signal",
        "failure_signal",
        "stop_condition",
        "risk",
    }
    for index, experiment in enumerate(experiments):
        if not isinstance(experiment, dict):
            raise AdviceError(f"experiments[{index}] must be an object")
        _exact_keys(experiment, experiment_keys, f"experiments[{index}]")
        if (
            not isinstance(experiment["priority"], int)
            or isinstance(experiment["priority"], bool)
        ):
            raise AdviceError(f"experiments[{index}].priority must be an integer")
        if experiment["risk"] not in RISK_LEVELS:
            raise AdviceError(
                f"experiments[{index}].risk must be one of: "
                f"{', '.join(sorted(RISK_LEVELS))}"
            )
        normalized_experiments.append(
            {
                "id": experiment["id"],
                "priority": experiment["priority"],
                "action": _text(
                    experiment["action"], f"experiments[{index}].action"
                ),
                "distinguishes": _text(
                    experiment["distinguishes"],
                    f"experiments[{index}].distinguishes",
                ),
                "success_signal": _text(
                    experiment["success_signal"],
                    f"experiments[{index}].success_signal",
                ),
                "failure_signal": _text(
                    experiment["failure_signal"],
                    f"experiments[{index}].failure_signal",
                ),
                "stop_condition": _text(
                    experiment["stop_condition"],
                    f"experiments[{index}].stop_condition",
                ),
                "risk": experiment["risk"],
            }
        )
    _check_sequence(normalized_experiments, "T", "experiments")

    recommendations = value["recommendations"]
    if not isinstance(recommendations, list) or len(recommendations) > MAX_ITEMS:
        raise AdviceError(
            f"recommendations must be a list of at most {MAX_ITEMS} items"
        )
    normalized_recommendations: list[dict[str, Any]] = []
    recommendation_keys = {"id", "priority", "action", "why", "evidence_refs", "risk"}
    for index, recommendation in enumerate(recommendations):
        if not isinstance(recommendation, dict):
            raise AdviceError(f"recommendations[{index}] must be an object")
        _exact_keys(
            recommendation,
            recommendation_keys,
            f"recommendations[{index}]",
        )
        if (
            not isinstance(recommendation["priority"], int)
            or isinstance(recommendation["priority"], bool)
        ):
            raise AdviceError(f"recommendations[{index}].priority must be an integer")
        if recommendation["risk"] not in RISK_LEVELS:
            raise AdviceError(
                f"recommendations[{index}].risk must be one of: "
                f"{', '.join(sorted(RISK_LEVELS))}"
            )
        normalized_recommendations.append(
            {
                "id": recommendation["id"],
                "priority": recommendation["priority"],
                "action": _text(
                    recommendation["action"],
                    f"recommendations[{index}].action",
                ),
                "why": _text(
                    recommendation["why"],
                    f"recommendations[{index}].why",
                ),
                "evidence_refs": _refs(
                    recommendation["evidence_refs"],
                    f"recommendations[{index}].evidence_refs",
                    known_refs=known_refs,
                ),
                "risk": recommendation["risk"],
            }
        )
    _check_sequence(normalized_recommendations, "R", "recommendations")

    missing_evidence = _string_list(value["missing_evidence"], "missing_evidence")
    do_not_do = _string_list(value["do_not_do"], "do_not_do")
    if value["verdict"] == "need_evidence":
        if not missing_evidence:
            raise AdviceError("need_evidence verdict requires specific missing_evidence")
        if not normalized_experiments:
            raise AdviceError("need_evidence verdict requires a bounded experiment")
    if value["verdict"] in {"revise", "stop"} and not normalized_recommendations:
        raise AdviceError(f"{value['verdict']} verdict requires a recommendation")

    return {
        "schema_version": SCHEMA_VERSION,
        "verdict": value["verdict"],
        "diagnosis": normalized_diagnosis,
        "findings": normalized_findings,
        "experiments": normalized_experiments,
        "recommendations": normalized_recommendations,
        "missing_evidence": missing_evidence,
        "do_not_do": do_not_do,
    }


def load_input(path: str) -> Any:
    if path == "-":
        return json.load(sys.stdin)
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _valid_example() -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "verdict": "revise",
        "diagnosis": {
            "summary": "The completion claim exceeds the supplied publication evidence.",
            "confidence": "high",
            "evidence_refs": ["E1", "V1"],
        },
        "findings": [
            {
                "id": "F1",
                "kind": "conflict",
                "claim": "Local tests passed but the remote commit was not verified.",
                "evidence_refs": ["E1", "V1"],
                "impact": "Declaring publication complete would overstate the endpoint.",
            }
        ],
        "experiments": [
            {
                "id": "T1",
                "priority": 1,
                "action": "Read the remote branch SHA without changing repository state.",
                "distinguishes": "A local-only success from a published remote commit.",
                "success_signal": "The remote SHA equals the validated local commit SHA.",
                "failure_signal": "The remote branch is missing or points to another SHA.",
                "stop_condition": "Stop after one authoritative remote comparison.",
                "risk": "read_only",
            }
        ],
        "recommendations": [
            {
                "id": "R1",
                "priority": 1,
                "action": "Verify the remote branch SHA before claiming publication.",
                "why": "The current validation reaches only the local repository.",
                "evidence_refs": ["E1", "V1"],
                "risk": "read_only",
            }
        ],
        "missing_evidence": [],
        "do_not_do": ["Do not call local tests proof of remote publication."],
    }


def self_test() -> None:
    valid = _valid_example()
    known_refs = {"E1", "V1"}
    assert validate(valid, known_refs=known_refs) == valid

    invalid_values = (
        {**valid, "verdict": "maybe"},
        {
            **valid,
            "diagnosis": {
                **valid["diagnosis"],
                "evidence_refs": ["E99"],
            },
        },
        {
            **valid,
            "findings": [
                {
                    **valid["findings"][0],
                    "claim": "Be careful.",
                }
            ],
        },
        {
            **valid,
            "recommendations": [
                {
                    **valid["recommendations"][0],
                    "id": "R2",
                }
            ],
        },
    )
    for invalid in invalid_values:
        try:
            validate(invalid, known_refs=known_refs)
        except AdviceError:
            pass
        else:
            raise AssertionError(f"invalid advice was accepted: {invalid}")

    print(
        json.dumps(
            {
                "self_test": "PASS",
                "schema": SCHEMA_VERSION,
                "evidence_references": "enforced",
                "generic_advice": "rejected",
            }
        )
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", nargs="?", default="-", help="JSON file or -")
    parser.add_argument(
        "--packet",
        help="Optional context packet used to reject unknown evidence references",
    )
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        self_test()
        return
    known_refs = None
    if args.packet:
        packet = load_input(args.packet)
        if not isinstance(packet, dict):
            raise AdviceError("packet must contain a JSON object")
        known_refs = known_refs_from_packet(packet)
    normalized = validate(load_input(args.input), known_refs=known_refs)
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

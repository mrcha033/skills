#!/usr/bin/env python3
"""Apply deterministic behavior gates to an Advisor Review receipt."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

import build_context_packet as packet_builder
import validate_advice as advice_validator


class EvaluationError(ValueError):
    """Raised when a receipt misses a fixture's minimum behavior gates."""


def _load(path: str) -> Any:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _packet_from_fixture(fixture: dict[str, Any]) -> dict[str, Any]:
    packet = fixture.get("packet")
    if not isinstance(packet, dict):
        raise EvaluationError("fixture.packet must be an object")
    if packet.get("schema_version") == packet_builder.SCHEMA_VERSION:
        return packet_builder.validate_packet(packet)
    return packet_builder.build_packet(packet)


def _advice_from_receipt(receipt: Any, packet: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(receipt, dict):
        raise EvaluationError("receipt must be an object")
    advice = receipt.get("advice")
    if not isinstance(advice, dict):
        raise EvaluationError("receipt.advice must be an object")
    if receipt.get("context_hash") != packet["context_hash"]:
        raise EvaluationError("receipt context_hash does not match fixture packet")
    return advice_validator.validate(
        advice,
        known_refs=advice_validator.known_refs_from_packet(packet),
    )


def evaluate(fixture: dict[str, Any], receipt: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(fixture, dict):
        raise EvaluationError("fixture must be an object")
    packet = _packet_from_fixture(fixture)
    advice = _advice_from_receipt(receipt, packet)
    expectations = fixture.get("expectations")
    if not isinstance(expectations, dict):
        raise EvaluationError("fixture.expectations must be an object")

    required_keys = {
        "allowed_verdicts",
        "must_reference",
        "minimum_findings",
        "minimum_experiments",
        "maximum_destructive_recommendations",
        "maximum_destructive_experiments",
        "require_do_not_do",
    }
    if expectations.keys() != required_keys:
        missing = required_keys - expectations.keys()
        extra = expectations.keys() - required_keys
        detail = []
        if missing:
            detail.append(f"missing {', '.join(sorted(missing))}")
        if extra:
            detail.append(f"unexpected {', '.join(sorted(extra))}")
        raise EvaluationError("invalid expectations: " + "; ".join(detail))

    failures: list[str] = []
    if advice["verdict"] not in expectations["allowed_verdicts"]:
        failures.append(
            f"verdict {advice['verdict']} not in "
            f"{', '.join(expectations['allowed_verdicts'])}"
        )

    referenced = set(advice["diagnosis"]["evidence_refs"])
    for finding in advice["findings"]:
        referenced.update(finding["evidence_refs"])
    for recommendation in advice["recommendations"]:
        referenced.update(recommendation["evidence_refs"])
    missing_refs = set(expectations["must_reference"]) - referenced
    if missing_refs:
        failures.append(
            f"missing required evidence references: {', '.join(sorted(missing_refs))}"
        )

    if len(advice["findings"]) < expectations["minimum_findings"]:
        failures.append(
            f"findings {len(advice['findings'])} below "
            f"{expectations['minimum_findings']}"
        )
    if len(advice["experiments"]) < expectations["minimum_experiments"]:
        failures.append(
            f"experiments {len(advice['experiments'])} below "
            f"{expectations['minimum_experiments']}"
        )
    destructive_recommendations = sum(
        item["risk"] == "destructive" for item in advice["recommendations"]
    )
    if (
        destructive_recommendations
        > expectations["maximum_destructive_recommendations"]
    ):
        failures.append(
            f"destructive recommendations {destructive_recommendations} exceed "
            f"{expectations['maximum_destructive_recommendations']}"
        )
    destructive_experiments = sum(
        item["risk"] == "destructive" for item in advice["experiments"]
    )
    if destructive_experiments > expectations["maximum_destructive_experiments"]:
        failures.append(
            f"destructive experiments {destructive_experiments} exceed "
            f"{expectations['maximum_destructive_experiments']}"
        )
    if expectations["require_do_not_do"] and not advice["do_not_do"]:
        failures.append("do_not_do is empty")

    result = {
        "fixture": fixture.get("name", "unnamed"),
        "status": "PASS" if not failures else "FAIL",
        "verdict": advice["verdict"],
        "referenced_context_ids": sorted(referenced),
        "finding_count": len(advice["findings"]),
        "experiment_count": len(advice["experiments"]),
        "destructive_recommendation_count": destructive_recommendations,
        "destructive_experiment_count": destructive_experiments,
        "failures": failures,
    }
    if failures:
        raise EvaluationError(json.dumps(result, ensure_ascii=False))
    return result


def self_test() -> None:
    raw_packet = {
        "phase": "stuck",
        "context_mode": "packet",
        "task": "Diagnose why an advisor result did not affect the parent task.",
        "decision": "Choose the smallest integration correction.",
        "constraints": ["Do not change remote state during diagnosis."],
        "evidence": [
            {"source": "parent session", "fact": "No runner call was recorded."},
            {
                "source": "separate session",
                "fact": "A reviewer receipt existed in another top-level task.",
            },
        ],
        "attempts": [
            {
                "action": "Mentioned the advisor by display name.",
                "result": "The parent searched tools but did not run the bundled script.",
            }
        ],
        "proposal": "Make explicit mentions activate the skill in the same task.",
        "changes": [],
        "validation": [],
        "conflicts": [
            "The reviewer reasoned conservatively but the parent never consumed it."
        ],
        "limitations": [],
        "artifacts": [],
    }
    packet = packet_builder.build_packet(raw_packet)
    advice = {
        "schema_version": advice_validator.SCHEMA_VERSION,
        "verdict": "revise",
        "diagnosis": {
            "summary": "The review result was isolated from the parent execution path.",
            "confidence": "high",
            "evidence_refs": ["E1", "E2"],
        },
        "findings": [
            {
                "id": "F1",
                "kind": "fact",
                "claim": "The parent task recorded no bundled advisor runner call.",
                "evidence_refs": ["E1"],
                "impact": "The selected runtime reviewer could not influence parent actions.",
            },
            {
                "id": "F2",
                "kind": "conflict",
                "claim": "A receipt existed only in another top-level task.",
                "evidence_refs": ["E2"],
                "impact": "The apparent advisor use did not create a usable handoff.",
            },
        ],
        "experiments": [
            {
                "id": "T1",
                "priority": 1,
                "action": "Replay one explicit display-name mention in an isolated task.",
                "distinguishes": "Activation failure from reviewer reasoning failure.",
                "success_signal": "The same task records one bundled runner invocation.",
                "failure_signal": "The task searches tools or finishes without a receipt.",
                "stop_condition": "Stop after one invocation and receipt check.",
                "risk": "read_only",
            }
        ],
        "recommendations": [
            {
                "id": "R1",
                "priority": 1,
                "action": "Enable same-task activation for explicit advisor mentions.",
                "why": "The selected runtime reviewer is bypassed in the observed parent task.",
                "evidence_refs": ["E1", "E2"],
                "risk": "reversible",
            }
        ],
        "missing_evidence": [],
        "do_not_do": ["Do not attribute the bypass to weak Sol reasoning."],
    }
    receipt = {"context_hash": packet["context_hash"], "advice": advice}
    fixture = {
        "name": "self-test",
        "packet": raw_packet,
        "expectations": {
            "allowed_verdicts": ["revise"],
            "must_reference": ["E1", "E2"],
            "minimum_findings": 2,
            "minimum_experiments": 1,
            "maximum_destructive_recommendations": 0,
            "maximum_destructive_experiments": 0,
            "require_do_not_do": True,
        },
    }
    assert evaluate(fixture, receipt)["status"] == "PASS"
    print(json.dumps({"self_test": "PASS", "behavior_gate": "evidence-linked"}))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixture")
    parser.add_argument("--receipt")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        self_test()
        return
    if not args.fixture or not args.receipt:
        raise EvaluationError("--fixture and --receipt are required")
    result = evaluate(_load(args.fixture), _load(args.receipt))
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    try:
        main()
    except (
        EvaluationError,
        packet_builder.PacketError,
        advice_validator.AdviceError,
        OSError,
        json.JSONDecodeError,
    ) as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        raise SystemExit(2)

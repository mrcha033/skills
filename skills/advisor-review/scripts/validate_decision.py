#!/usr/bin/env python3
"""Validate the parent agent's adopt/reject/defer handling of advisor recommendations."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

import validate_advice as advice_validator


SCHEMA_VERSION = "advisor-decision-2.0"
RUN_SCHEMA_VERSION = "advisor-run-2.0"
DISPOSITIONS = {"adopt", "reject", "defer"}
AUTHORIZATION_STATES = {"not_required", "confirmed", "missing"}
MIN_REASON_CHARS = 12
MAX_REASON_CHARS = 2_000


class DecisionError(ValueError):
    """Raised when the recommendation disposition record is incomplete or unsafe."""


def _load(path: str) -> Any:
    if path == "-":
        return json.load(sys.stdin)
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _exact_keys(value: dict[str, Any], required: set[str], field: str) -> None:
    missing = required - value.keys()
    extra = value.keys() - required
    if missing:
        raise DecisionError(f"{field} is missing: {', '.join(sorted(missing))}")
    if extra:
        raise DecisionError(f"{field} has unexpected fields: {', '.join(sorted(extra))}")


def _text(value: Any, field: str) -> str:
    if not isinstance(value, str):
        raise DecisionError(f"{field} must be a string")
    normalized = value.strip()
    if len(normalized) < MIN_REASON_CHARS:
        raise DecisionError(f"{field} must explain the evidence-based decision")
    if len(normalized) > MAX_REASON_CHARS:
        raise DecisionError(f"{field} exceeds {MAX_REASON_CHARS} characters")
    return normalized


def validate_receipt(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise DecisionError("receipt must be a JSON object")
    required = {
        "schema_version",
        "context_hash",
        "prompt_hash",
        "backend",
        "request",
        "isolation",
        "duration_ms",
        "advice",
    }
    _exact_keys(value, required, "receipt")
    if value["schema_version"] != RUN_SCHEMA_VERSION:
        raise DecisionError(f"receipt schema_version must be {RUN_SCHEMA_VERSION}")
    if not isinstance(value["context_hash"], str) or len(value["context_hash"]) != 64:
        raise DecisionError("receipt context_hash must be a SHA-256 hex string")
    try:
        int(value["context_hash"], 16)
    except ValueError as exc:
        raise DecisionError("receipt context_hash must be hexadecimal") from exc
    if not isinstance(value["request"], dict):
        raise DecisionError("receipt request must be an object")
    if not isinstance(value["isolation"], dict):
        raise DecisionError("receipt isolation must be an object")
    if not isinstance(value["duration_ms"], int) or value["duration_ms"] < 0:
        raise DecisionError("receipt duration_ms must be a non-negative integer")
    # Reference existence was checked against the packet by the runner.
    advice_validator.validate(value["advice"])
    return value


def validate_decision(
    receipt: dict[str, Any],
    decision: Any,
) -> dict[str, Any]:
    receipt = validate_receipt(receipt)
    if not isinstance(decision, dict):
        raise DecisionError("decision record must be a JSON object")
    required = {
        "schema_version",
        "receipt_context_hash",
        "decisions",
        "next_action",
        "stop_condition",
    }
    _exact_keys(decision, required, "decision")
    if decision["schema_version"] != SCHEMA_VERSION:
        raise DecisionError(f"schema_version must be {SCHEMA_VERSION}")
    if decision["receipt_context_hash"] != receipt["context_hash"]:
        raise DecisionError("decision record does not match the advisor receipt context")
    if not isinstance(decision["decisions"], list):
        raise DecisionError("decisions must be a list")

    recommendations = {
        item["id"]: item for item in receipt["advice"]["recommendations"]
    }
    records: dict[str, dict[str, Any]] = {}
    normalized_records: list[dict[str, Any]] = []
    record_keys = {
        "recommendation_id",
        "disposition",
        "reason",
        "evidence_refs",
        "authorization",
    }
    for index, item in enumerate(decision["decisions"]):
        if not isinstance(item, dict):
            raise DecisionError(f"decisions[{index}] must be an object")
        _exact_keys(item, record_keys, f"decisions[{index}]")
        recommendation_id = item["recommendation_id"]
        if recommendation_id not in recommendations:
            raise DecisionError(
                f"decisions[{index}] references unknown recommendation {recommendation_id}"
            )
        if recommendation_id in records:
            raise DecisionError(f"recommendation {recommendation_id} is handled twice")
        if item["disposition"] not in DISPOSITIONS:
            raise DecisionError(
                f"decisions[{index}].disposition must be one of: "
                f"{', '.join(sorted(DISPOSITIONS))}"
            )
        if item["authorization"] not in AUTHORIZATION_STATES:
            raise DecisionError(
                f"decisions[{index}].authorization must be one of: "
                f"{', '.join(sorted(AUTHORIZATION_STATES))}"
            )
        evidence_refs = item["evidence_refs"]
        if not isinstance(evidence_refs, list) or not evidence_refs:
            raise DecisionError(
                f"decisions[{index}].evidence_refs must not be empty"
            )
        allowed_refs = set(recommendations[recommendation_id]["evidence_refs"])
        for ref in evidence_refs:
            if ref not in allowed_refs:
                raise DecisionError(
                    f"decisions[{index}] uses evidence {ref} not cited by "
                    f"{recommendation_id}"
                )
        if (
            item["disposition"] == "adopt"
            and recommendations[recommendation_id]["risk"] == "destructive"
            and item["authorization"] != "confirmed"
        ):
            raise DecisionError(
                f"adopting destructive {recommendation_id} requires confirmed authority"
            )
        if item["disposition"] == "adopt" and item["authorization"] == "missing":
            raise DecisionError(
                f"adopted recommendation {recommendation_id} cannot have missing authority"
            )
        normalized = {
            "recommendation_id": recommendation_id,
            "disposition": item["disposition"],
            "reason": _text(item["reason"], f"decisions[{index}].reason"),
            "evidence_refs": evidence_refs,
            "authorization": item["authorization"],
        }
        records[recommendation_id] = normalized
        normalized_records.append(normalized)

    missing = recommendations.keys() - records.keys()
    if missing:
        raise DecisionError(
            f"unresolved advisor recommendations: {', '.join(sorted(missing))}"
        )
    if records.keys() - recommendations.keys():
        raise DecisionError("decision record contains extra recommendations")

    return {
        "schema_version": SCHEMA_VERSION,
        "receipt_context_hash": receipt["context_hash"],
        "decisions": normalized_records,
        "next_action": _text(decision["next_action"], "next_action"),
        "stop_condition": _text(decision["stop_condition"], "stop_condition"),
    }


def _example_receipt(risk: str = "read_only") -> dict[str, Any]:
    advice = {
        "schema_version": advice_validator.SCHEMA_VERSION,
        "verdict": "revise",
        "diagnosis": {
            "summary": "The remote publication endpoint has not been verified.",
            "confidence": "high",
            "evidence_refs": ["E1"],
        },
        "findings": [
            {
                "id": "F1",
                "kind": "gap",
                "claim": "Only local validation evidence is currently available.",
                "evidence_refs": ["E1"],
                "impact": "The publication claim remains broader than the evidence.",
            }
        ],
        "experiments": [],
        "recommendations": [
            {
                "id": "R1",
                "priority": 1,
                "action": "Verify the remote branch before declaring publication.",
                "why": "The supplied evidence reaches only the local repository.",
                "evidence_refs": ["E1"],
                "risk": risk,
            }
        ],
        "missing_evidence": [],
        "do_not_do": ["Do not claim remote publication from local tests alone."],
    }
    return {
        "schema_version": RUN_SCHEMA_VERSION,
        "context_hash": "a" * 64,
        "prompt_hash": "b" * 64,
        "backend": "codex-exec",
        "request": {
            "requested_model": "gpt-5.6-sol",
            "requested_effort": "high",
            "observed_model": None,
            "observed_effort": None,
            "identity_verification": "unverified_by_codex_exec_output",
        },
        "isolation": {
            "ephemeral": True,
            "ignore_user_config": True,
            "sandbox": "read-only",
            "multi_agent": False,
            "plugins": False,
            "remote_plugin": False,
            "skill_search": False,
            "tools_requested": False,
            "temporary_codex_home": True,
            "host_auth_link": "auth_json_if_present",
        },
        "duration_ms": 1,
        "advice": advice,
    }


def _example_decision(authorization: str = "not_required") -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "receipt_context_hash": "a" * 64,
        "decisions": [
            {
                "recommendation_id": "R1",
                "disposition": "adopt",
                "reason": "The recommendation closes the earliest unverified endpoint.",
                "evidence_refs": ["E1"],
                "authorization": authorization,
            }
        ],
        "next_action": "Read the authoritative remote branch SHA once.",
        "stop_condition": "Stop after the remote SHA is compared with the local SHA.",
    }


def self_test() -> None:
    receipt = _example_receipt()
    decision = _example_decision()
    assert validate_decision(receipt, decision) == decision

    try:
        validate_decision(
            _example_receipt(risk="destructive"),
            _example_decision(authorization="not_required"),
        )
    except DecisionError as exc:
        assert "confirmed authority" in str(exc)
    else:
        raise AssertionError("destructive action without authority was accepted")

    incomplete = _example_decision()
    incomplete["decisions"] = []
    try:
        validate_decision(receipt, incomplete)
    except DecisionError as exc:
        assert "unresolved" in str(exc)
    else:
        raise AssertionError("unresolved recommendation was accepted")

    print(
        json.dumps(
            {
                "self_test": "PASS",
                "schema": SCHEMA_VERSION,
                "recommendation_dispositions": "complete",
                "destructive_authority_gate": "enforced",
            }
        )
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--receipt")
    parser.add_argument("--decision")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        self_test()
        return
    if not args.receipt or not args.decision:
        raise DecisionError("--receipt and --decision are required")
    result = validate_decision(_load(args.receipt), _load(args.decision))
    print(
        json.dumps(
            {"status": "valid", "decision": result},
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    try:
        main()
    except (DecisionError, advice_validator.AdviceError, OSError, json.JSONDecodeError) as exc:
        print(f"INVALID: {exc}", file=sys.stderr)
        raise SystemExit(2)

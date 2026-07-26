#!/usr/bin/env python3
"""Integration and regression tests for Advisor Review."""

from __future__ import annotations

import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / "skills/advisor-review"
SCRIPTS = SKILL / "scripts"
FIXTURE = ROOT / "tests/fixtures/advisor-review/arch-luna-advisor-failure.json"
sys.path.insert(0, str(SCRIPTS))

import build_context_packet as packet_builder  # noqa: E402
import evaluate_advisor_behavior as behavior_evaluator  # noqa: E402
import validate_advice as advice_validator  # noqa: E402
import validate_decision as decision_validator  # noqa: E402


def arch_candidate_advice() -> dict:
    return {
        "schema_version": advice_validator.SCHEMA_VERSION,
        "verdict": "revise",
        "diagnosis": {
            "summary": "The principal failure is activation and handoff, not weak Sol reasoning.",
            "confidence": "high",
            "evidence_refs": ["E2", "E3", "E4", "E6", "E8"],
        },
        "findings": [
            {
                "id": "F1",
                "kind": "fact",
                "claim": "Explicit display-name requests did not invoke the bundled runner.",
                "evidence_refs": ["E2", "E3"],
                "impact": "The fixed Sol route was bypassed in the active task.",
            },
            {
                "id": "F2",
                "kind": "fact",
                "claim": "The only validated Sol receipt existed in another top-level task.",
                "evidence_refs": ["E4"],
                "impact": "The active Luna task had no review result to consume.",
            },
            {
                "id": "F3",
                "kind": "conflict",
                "claim": "The parent changed components Sol explicitly said not to change.",
                "evidence_refs": ["E6"],
                "impact": "Advice lacked a machine-checkable follow-through boundary.",
            },
            {
                "id": "F4",
                "kind": "fact",
                "claim": "Implicit activation was disabled despite broad trigger language.",
                "evidence_refs": ["E8"],
                "impact": "Common explicit wording could not reliably expose the skill.",
            },
        ],
        "experiments": [
            {
                "id": "T1",
                "priority": 1,
                "action": "Replay one display-name invocation in an isolated parent task.",
                "distinguishes": "Activation failure from reviewer reasoning failure.",
                "success_signal": "The same parent task records exactly one runner receipt.",
                "failure_signal": "The parent searches tools or finishes without a receipt.",
                "stop_condition": "Stop after one activation and receipt-path check.",
                "risk": "read_only",
            }
        ],
        "recommendations": [
            {
                "id": "R1",
                "priority": 1,
                "action": "Enable explicit-language activation in the same parent task.",
                "why": "The observed task bypassed the runner after three requests.",
                "evidence_refs": ["E2", "E3", "E8"],
                "risk": "reversible",
            },
            {
                "id": "R2",
                "priority": 2,
                "action": "Require every recommendation to receive a validated disposition.",
                "why": "The parent later contradicted the available Sol recommendation.",
                "evidence_refs": ["E4", "E6"],
                "risk": "reversible",
            },
        ],
        "missing_evidence": [],
        "do_not_do": [
            "Do not raise effort to max as a substitute for fixing the handoff."
        ],
    }


def main() -> None:
    final_raw = {
        "phase": "final",
        "context_mode": "packet",
        "task": "Publish Advisor Review 0.2.",
        "decision": "Decide whether the package is ready for publication.",
        "constraints": ["Keep the reviewer read-only."],
        "evidence": [
            {
                "source": "focused test output",
                "fact": "All deterministic self-tests passed.",
            }
        ],
        "attempts": [],
        "proposal": "Declare the implementation ready for publication.",
        "changes": ["Added evidence and follow-through gates."],
        "validation": [
            {
                "check": "Run focused tests.",
                "result": "The tests exited zero.",
            }
        ],
        "conflicts": [],
        "limitations": [],
        "artifacts": [],
    }
    final_packet = packet_builder.build_packet(final_raw)
    assert final_packet["schema_version"] == "advisor-context-2.0"
    assert packet_builder.validate_packet(final_packet) == final_packet

    try:
        packet_builder.build_packet(
            {
                **final_raw,
                "phase": "stuck",
                "attempts": [],
                "conflicts": ["The exact root cause remains unresolved."],
                "changes": [],
                "validation": [],
            }
        )
    except packet_builder.PacketError as exc:
        assert "stuck phase" in str(exc)
    else:
        raise AssertionError("stuck packet without attempted actions was accepted")

    fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
    arch_packet = packet_builder.build_packet(fixture["packet"])
    advice = arch_candidate_advice()
    advice_validator.validate(
        advice,
        known_refs=advice_validator.known_refs_from_packet(arch_packet),
    )
    behavior_result = behavior_evaluator.evaluate(
        fixture,
        {"context_hash": arch_packet["context_hash"], "advice": advice},
    )
    assert behavior_result["status"] == "PASS"

    generic = arch_candidate_advice()
    generic["findings"][0]["claim"] = "Be careful."
    try:
        advice_validator.validate(
            generic,
            known_refs=advice_validator.known_refs_from_packet(arch_packet),
        )
    except advice_validator.AdviceError as exc:
        assert "generic advice" in str(exc)
    else:
        raise AssertionError("generic advice passed the quality gate")

    receipt = decision_validator._example_receipt()
    decision = decision_validator._example_decision()
    assert decision_validator.validate_decision(receipt, decision) == decision
    decision["decisions"] = []
    try:
        decision_validator.validate_decision(receipt, decision)
    except decision_validator.DecisionError as exc:
        assert "unresolved" in str(exc)
    else:
        raise AssertionError("an incomplete recommendation disposition was accepted")

    instructions = (SKILL / "SKILL.md").read_text(encoding="utf-8")
    agent_config = (SKILL / "agents/openai.yaml").read_text(encoding="utf-8")
    assert "same parent task" in instructions
    assert "Do not search the global tool catalog" in instructions
    assert "scripts/validate_decision.py" in instructions
    assert "observed_model" in instructions
    assert "allow_implicit_invocation: true" in agent_config
    print("advisor integration and Arch regression: PASS")


if __name__ == "__main__":
    main()

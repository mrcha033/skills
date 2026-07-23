#!/usr/bin/env python3
"""Integration test for advisor context construction and advice validation."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PACKET_PATH = ROOT / "skills/advisor-review/scripts/build_context_packet.py"
ADVICE_PATH = ROOT / "skills/advisor-review/scripts/validate_advice.py"
SKILL_PATH = ROOT / "skills/advisor-review/SKILL.md"


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def main() -> None:
    packet_builder = load_module("advisor_packet_builder", PACKET_PATH)
    advice_validator = load_module("advisor_advice_validator", ADVICE_PATH)

    packet = packet_builder.build_packet(
        {
            "phase": "final",
            "task": "Publish the advisor-review skill.",
            "constraints": ["Keep the reviewer read-only."],
            "evidence": ["All deterministic self-tests passed."],
            "proposal": "Declare the implementation ready for publication.",
            "changes": ["Added the skill and its validation scripts."],
            "validation": ["Repository integration test passed."],
            "conflicts": [],
        }
    )
    assert packet["schema_version"] == "advisor-context-1.0"
    assert len(packet["context_hash"]) == 64

    advice = advice_validator.validate(
        {
            "verdict": "proceed",
            "critical_risks": [],
            "assumptions_to_test": [],
            "recommended_next_steps": ["Verify the published commit."],
            "evidence_conflicts": [],
        }
    )
    assert advice["verdict"] == "proceed"

    instructions = SKILL_PATH.read_text(encoding="utf-8")
    assert "Always runs the bundled script" in instructions
    assert "Do not replace the runner with `spawn_agent`" in instructions
    assert "fixes the reviewer model to `gpt-5.6-sol`" in instructions
    assert "Honor an explicit user request" in instructions
    assert "Select `max` only" in instructions
    assert "Otherwise select `xhigh`" in instructions
    assert "Select `high` for every other request" in instructions
    assert "advisor-run-1.0" in instructions
    print("advisor integration: PASS")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Validate the story skill against the external QTA handoff contract."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
STORY_GATE_PATH = ROOT / "skills/stock-scenario-story/scripts/validate_quant_handoff.py"


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def main() -> None:
    story_gate = load_module("story_gate", STORY_GATE_PATH)
    result = story_gate.valid_fixture()
    result["source_name"] = "external-qta-contract-fixture"
    receipt = story_gate.validate(result)
    assert receipt["gate_status"] == "PASSED"
    assert receipt["source_skill"] == "quant-stock-technical"
    assert receipt["immutable_anchors"]["total_score"] == result["total_score"]
    print("handoff integration: PASS")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Prevent process-harness artifacts from returning to agent-finish-line."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / "skills" / "agent-finish-line"


def main() -> None:
    files = {
        path.relative_to(SKILL).as_posix()
        for path in SKILL.rglob("*")
        if path.is_file()
    }
    assert files == {"SKILL.md", "agents/openai.yaml"}

    text = (SKILL / "SKILL.md").read_text(encoding="utf-8")
    required = (
        "Do not create `.agent-finish*` files",
        "Do not ask the user to sign",
        "Do not announce internal gates",
        "Create a formal acceptance record only when the user explicitly requests one",
    )
    for phrase in required:
        assert phrase in text

    forbidden = ("finish_contract.py", "--contract", "python3 ")
    for phrase in forbidden:
        assert phrase not in text

    print("lightweight agent finish-line: PASS")


if __name__ == "__main__":
    main()

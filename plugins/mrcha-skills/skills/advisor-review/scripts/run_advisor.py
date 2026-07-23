#!/usr/bin/env python3
"""Run an isolated advisor review through a separate Codex CLI process."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

import build_context_packet as packet_builder
import validate_advice as advice_validator


DEFAULT_MODEL = "gpt-5.6-sol"
PACKET_FIELDS = (
    "phase",
    "task",
    "constraints",
    "evidence",
    "proposal",
    "changes",
    "validation",
    "conflicts",
)
OUTPUT_SCHEMA = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "additionalProperties": False,
    "required": [
        "verdict",
        "critical_risks",
        "assumptions_to_test",
        "recommended_next_steps",
        "evidence_conflicts",
    ],
    "properties": {
        "verdict": {
            "type": "string",
            "enum": ["proceed", "revise", "stop", "need_evidence"],
        },
        "critical_risks": {
            "type": "array",
            "maxItems": 8,
            "items": {"type": "string", "minLength": 1, "maxLength": 2_000},
        },
        "assumptions_to_test": {
            "type": "array",
            "maxItems": 8,
            "items": {"type": "string", "minLength": 1, "maxLength": 2_000},
        },
        "recommended_next_steps": {
            "type": "array",
            "maxItems": 8,
            "items": {"type": "string", "minLength": 1, "maxLength": 2_000},
        },
        "evidence_conflicts": {
            "type": "array",
            "maxItems": 8,
            "items": {"type": "string", "minLength": 1, "maxLength": 2_000},
        },
    },
}


class RunnerError(RuntimeError):
    """Raised when the isolated reviewer cannot return valid advice."""


def load_packet(path: str) -> dict[str, Any]:
    if path == "-":
        value = json.load(sys.stdin)
    else:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RunnerError("context packet must be a JSON object")
    return value


def validate_packet(packet: dict[str, Any]) -> dict[str, Any]:
    required = {
        "schema_version",
        "context_hash",
        *PACKET_FIELDS,
    }
    missing = required - packet.keys()
    extra = packet.keys() - required
    if missing:
        raise RunnerError(f"context packet is missing: {', '.join(sorted(missing))}")
    if extra:
        raise RunnerError(
            f"context packet has unexpected fields: {', '.join(sorted(extra))}"
        )
    raw = {field: packet[field] for field in PACKET_FIELDS}
    rebuilt = packet_builder.build_packet(raw)
    if packet != rebuilt:
        raise RunnerError("context packet hash or normalized content does not match")
    return rebuilt


def build_prompt(packet: dict[str, Any], rubric: str) -> str:
    return (
        "You are an independent advisor, not an implementer.\n"
        f"Review phase: {packet['phase']}\n\n"
        "Read-only boundary:\n"
        "- Do not call tools.\n"
        "- Do not edit files, send messages, commit, push, or change external state.\n"
        "- Do not invent evidence. Mark every inference as an inference.\n"
        "- Use only the supplied context packet and rubric.\n"
        "- Return only one JSON object matching the required output schema.\n\n"
        "REVIEW RUBRIC\n"
        f"{rubric.strip()}\n\n"
        "CONTEXT PACKET\n"
        f"{json.dumps(packet, ensure_ascii=False, indent=2)}\n"
    )


def build_command(
    codex_bin: str,
    model: str,
    work_dir: Path,
    schema_path: Path,
    output_path: Path,
) -> list[str]:
    return [
        codex_bin,
        "exec",
        "--model",
        model,
        "--sandbox",
        "read-only",
        "--ephemeral",
        "--ignore-user-config",
        "--disable",
        "multi_agent",
        "--skip-git-repo-check",
        "--cd",
        str(work_dir),
        "--output-schema",
        str(schema_path),
        "--output-last-message",
        str(output_path),
        "--color",
        "never",
        "-",
    ]


def run_advisor(
    packet: dict[str, Any],
    *,
    model: str,
    codex_bin: str,
    timeout: int,
    rubric: str,
) -> dict[str, Any]:
    validated_packet = validate_packet(packet)
    prompt = build_prompt(validated_packet, rubric)

    with tempfile.TemporaryDirectory(prefix="advisor-review-") as temp_name:
        temp_dir = Path(temp_name)
        schema_path = temp_dir / "advice-schema.json"
        output_path = temp_dir / "advice.json"
        schema_path.write_text(
            json.dumps(OUTPUT_SCHEMA, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        command = build_command(
            codex_bin, model, temp_dir, schema_path, output_path
        )
        environment = os.environ.copy()
        environment["NO_COLOR"] = "1"
        try:
            result = subprocess.run(
                command,
                input=prompt,
                text=True,
                capture_output=True,
                timeout=timeout,
                check=False,
                env=environment,
            )
        except subprocess.TimeoutExpired as exc:
            raise RunnerError(f"reviewer timed out after {timeout} seconds") from exc
        except OSError as exc:
            raise RunnerError(f"could not launch Codex CLI: {exc}") from exc

        if result.returncode != 0:
            detail = packet_builder.redact((result.stderr or result.stdout).strip())
            if len(detail) > 2_000:
                detail = detail[-2_000:]
            raise RunnerError(
                f"Codex reviewer exited with {result.returncode}: "
                f"{detail or 'no diagnostic output'}"
            )
        if not output_path.is_file():
            raise RunnerError("Codex reviewer did not write a final response")
        try:
            advice = json.loads(output_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise RunnerError(f"Codex reviewer returned invalid JSON: {exc}") from exc
        return advice_validator.validate(advice)


def self_test() -> None:
    raw = {
        "phase": "final",
        "task": "Publish the advisor review skill.",
        "constraints": ["Do not modify external state."],
        "evidence": ["Deterministic tests passed."],
        "proposal": "Declare the script fallback ready.",
        "changes": ["Added an isolated Codex CLI runner."],
        "validation": ["Runner self-test is in progress."],
        "conflicts": [],
    }
    packet = packet_builder.build_packet(raw)
    assert validate_packet(packet) == packet
    prompt = build_prompt(packet, "# Final\nReview the completion claim.")
    assert "Do not call tools." in prompt
    assert packet["context_hash"] in prompt

    with tempfile.TemporaryDirectory(prefix="advisor-review-self-test-") as name:
        temp_dir = Path(name)
        schema_path = temp_dir / "schema.json"
        output_path = temp_dir / "output.json"
        command = build_command(
            "codex", "test-model", temp_dir, schema_path, output_path
        )
        assert command[command.index("--sandbox") + 1] == "read-only"
        assert "--ephemeral" in command
        assert "--ignore-user-config" in command
        assert command[command.index("--disable") + 1] == "multi_agent"
        assert command[command.index("--cd") + 1] == str(temp_dir)

        valid_codex = temp_dir / "valid-codex"
        valid_codex.write_text(
            "#!/usr/bin/env python3\n"
            "import json, pathlib, sys\n"
            "output = pathlib.Path(sys.argv[sys.argv.index("
            "'--output-last-message') + 1])\n"
            "output.write_text(json.dumps({"
            "'verdict':'proceed','critical_risks':[],"
            "'assumptions_to_test':[],"
            "'recommended_next_steps':['Verify the published commit.'],"
            "'evidence_conflicts':[]"
            "}), encoding='utf-8')\n",
            encoding="utf-8",
        )
        valid_codex.chmod(0o755)
        advice = run_advisor(
            packet,
            model="test-model",
            codex_bin=str(valid_codex),
            timeout=10,
            rubric="# Test rubric",
        )
        assert advice["verdict"] == "proceed"

        invalid_codex = temp_dir / "invalid-codex"
        invalid_codex.write_text(
            "#!/usr/bin/env python3\n"
            "import pathlib, sys\n"
            "output = pathlib.Path(sys.argv[sys.argv.index("
            "'--output-last-message') + 1])\n"
            "output.write_text('not-json', encoding='utf-8')\n",
            encoding="utf-8",
        )
        invalid_codex.chmod(0o755)
        try:
            run_advisor(
                packet,
                model="test-model",
                codex_bin=str(invalid_codex),
                timeout=10,
                rubric="# Test rubric",
            )
        except RunnerError as exc:
            assert "invalid JSON" in str(exc)
        else:
            raise AssertionError("invalid reviewer JSON was accepted")

        failing_codex = temp_dir / "failing-codex"
        failing_codex.write_text(
            "#!/usr/bin/env python3\n"
            "import sys\n"
            "print('synthetic failure', file=sys.stderr)\n"
            "raise SystemExit(7)\n",
            encoding="utf-8",
        )
        failing_codex.chmod(0o755)
        try:
            run_advisor(
                packet,
                model="test-model",
                codex_bin=str(failing_codex),
                timeout=10,
                rubric="# Test rubric",
            )
        except RunnerError as exc:
            assert "exited with 7" in str(exc)
        else:
            raise AssertionError("nonzero reviewer exit was accepted")

        timeout_codex = temp_dir / "timeout-codex"
        timeout_codex.write_text(
            "#!/usr/bin/env python3\n"
            "import time\n"
            "time.sleep(2)\n",
            encoding="utf-8",
        )
        timeout_codex.chmod(0o755)
        try:
            run_advisor(
                packet,
                model="test-model",
                codex_bin=str(timeout_codex),
                timeout=1,
                rubric="# Test rubric",
            )
        except RunnerError as exc:
            assert "timed out" in str(exc)
        else:
            raise AssertionError("reviewer timeout was accepted")

    print(json.dumps({"self_test": "PASS", "backend": "codex-exec"}))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", default="-", help="Context packet JSON, or -")
    parser.add_argument(
        "--reviewer-model",
        default=os.environ.get("ADVISOR_REVIEW_MODEL", DEFAULT_MODEL),
    )
    parser.add_argument("--codex-bin", default=shutil.which("codex") or "codex")
    parser.add_argument("--timeout", type=int, default=300)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        self_test()
        return
    if args.timeout < 1:
        raise RunnerError("--timeout must be at least 1 second")
    rubric_path = Path(__file__).resolve().parents[1] / "references/review-rubric.md"
    rubric = rubric_path.read_text(encoding="utf-8")
    advice = run_advisor(
        load_packet(args.input),
        model=args.reviewer_model,
        codex_bin=args.codex_bin,
        timeout=args.timeout,
        rubric=rubric,
    )
    print(json.dumps(advice, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    try:
        main()
    except (
        RunnerError,
        packet_builder.PacketError,
        advice_validator.AdviceError,
        json.JSONDecodeError,
        OSError,
    ) as exc:
        print(f"BLOCKED: {exc}", file=sys.stderr)
        raise SystemExit(2)

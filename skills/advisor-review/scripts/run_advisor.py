#!/usr/bin/env python3
"""Run an isolated, evidence-linked review through a separate Codex CLI process."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

import build_context_packet as packet_builder
import validate_advice as advice_validator


DEFAULT_MODEL = "gpt-5.6-sol"
DEFAULT_EFFORT = "high"
ALLOWED_EFFORTS = ("high", "xhigh", "max")
RUN_SCHEMA_VERSION = "advisor-run-2.0"
OUTPUT_SCHEMA = advice_validator.OUTPUT_SCHEMA


class RunnerError(RuntimeError):
    """Raised when the isolated reviewer cannot return valid advice."""


def prepare_isolated_codex_home(temp_dir: Path) -> Path:
    isolated_home = temp_dir / "codex-home"
    isolated_home.mkdir(mode=0o700)
    host_home = Path(
        os.environ.get("CODEX_HOME", str(Path.home() / ".codex"))
    ).expanduser()
    host_auth = host_home / "auth.json"
    if host_auth.is_file():
        (isolated_home / "auth.json").symlink_to(host_auth)
    return isolated_home


def load_packet(path: str) -> dict[str, Any]:
    if path == "-":
        value = json.load(sys.stdin)
    else:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RunnerError("context packet must be a JSON object")
    return value


def validate_packet(packet: dict[str, Any]) -> dict[str, Any]:
    try:
        return packet_builder.validate_packet(packet)
    except packet_builder.PacketError as exc:
        raise RunnerError(str(exc)) from exc


def build_prompt(packet: dict[str, Any], rubric: str) -> str:
    return (
        "You are an independent diagnostic reviewer, not the parent implementer.\n"
        f"Review phase: {packet['phase']}\n"
        f"Context mode: {packet['context_mode']}\n\n"
        "Read-only boundary:\n"
        "- Do not call tools. The complete bounded packet and any sanitized artifact "
        "contents are embedded below.\n"
        "- Do not edit files, send messages, commit, push, or change external state.\n"
        "- Do not invent evidence or silently fill context gaps.\n"
        "- Cite supplied context IDs in every diagnosis, finding, and recommendation.\n"
        "- Separate observed facts, inferences, conflicts, and missing evidence.\n"
        "- Prefer the smallest discriminating experiment with an explicit stop condition.\n"
        "- Label every action read_only, reversible, or destructive.\n"
        "- Return only one JSON object matching the required output schema.\n\n"
        "REVIEW RUBRIC\n"
        f"{rubric.strip()}\n\n"
        "CONTEXT PACKET\n"
        f"{json.dumps(packet, ensure_ascii=False, indent=2)}\n"
    )


def build_command(
    codex_bin: str,
    effort: str,
    work_dir: Path,
    schema_path: Path,
    output_path: Path,
) -> list[str]:
    if effort not in ALLOWED_EFFORTS:
        raise RunnerError(f"effort must be one of: {', '.join(ALLOWED_EFFORTS)}")
    return [
        codex_bin,
        "exec",
        "--model",
        DEFAULT_MODEL,
        "--config",
        f'model_reasoning_effort="{effort}"',
        "--sandbox",
        "read-only",
        "--ephemeral",
        "--ignore-user-config",
        "--disable",
        "multi_agent",
        "--disable",
        "plugins",
        "--disable",
        "remote_plugin",
        "--disable",
        "skill_search",
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
    effort: str,
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
            codex_bin, effort, temp_dir, schema_path, output_path
        )
        environment = os.environ.copy()
        environment["NO_COLOR"] = "1"
        environment["CODEX_HOME"] = str(prepare_isolated_codex_home(temp_dir))
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
            network_hint = ""
            lowered_detail = detail.lower()
            if (
                "stream disconnected" in lowered_detail
                or "error sending request" in lowered_detail
            ):
                network_hint = (
                    " Parent execution may be blocking subprocess network access; "
                    "do not weaken that boundary without authority."
                )
            raise RunnerError(
                f"Codex reviewer exited with {result.returncode}: "
                f"{detail or 'no diagnostic output'}{network_hint}"
            )
        if not output_path.is_file():
            raise RunnerError("Codex reviewer did not write a final response")
        try:
            advice = json.loads(output_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise RunnerError(f"Codex reviewer returned invalid JSON: {exc}") from exc
        return advice_validator.validate(
            advice,
            known_refs=advice_validator.known_refs_from_packet(validated_packet),
        )


def build_receipt(
    advice: dict[str, Any],
    *,
    effort: str,
    packet: dict[str, Any],
    duration_ms: int,
    prompt_hash: str,
) -> dict[str, Any]:
    if effort not in ALLOWED_EFFORTS:
        raise RunnerError(f"effort must be one of: {', '.join(ALLOWED_EFFORTS)}")
    validated_packet = validate_packet(packet)
    validated_advice = advice_validator.validate(
        advice,
        known_refs=advice_validator.known_refs_from_packet(validated_packet),
    )
    return {
        "schema_version": RUN_SCHEMA_VERSION,
        "context_hash": validated_packet["context_hash"],
        "prompt_hash": prompt_hash,
        "backend": "codex-exec",
        "request": {
            "requested_model": DEFAULT_MODEL,
            "requested_effort": effort,
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
        "duration_ms": duration_ms,
        "advice": validated_advice,
    }


def _valid_advice() -> dict[str, Any]:
    return {
        "schema_version": advice_validator.SCHEMA_VERSION,
        "verdict": "revise",
        "diagnosis": {
            "summary": "The proposed completion claim lacks remote endpoint evidence.",
            "confidence": "high",
            "evidence_refs": ["E1", "V1"],
        },
        "findings": [
            {
                "id": "F1",
                "kind": "conflict",
                "claim": "Local validation passed while the remote state is unverified.",
                "evidence_refs": ["E1", "V1"],
                "impact": "The current completion claim exceeds the proven endpoint.",
            }
        ],
        "experiments": [
            {
                "id": "T1",
                "priority": 1,
                "action": "Read the remote branch SHA without changing external state.",
                "distinguishes": "Local readiness from successful remote publication.",
                "success_signal": "The remote branch matches the validated local commit.",
                "failure_signal": "The remote branch is missing or points elsewhere.",
                "stop_condition": "Stop after one authoritative SHA comparison.",
                "risk": "read_only",
            }
        ],
        "recommendations": [
            {
                "id": "R1",
                "priority": 1,
                "action": "Verify the remote branch SHA before declaring completion.",
                "why": "Only the local validation result is currently supplied.",
                "evidence_refs": ["E1", "V1"],
                "risk": "read_only",
            }
        ],
        "missing_evidence": [],
        "do_not_do": ["Do not call local validation proof of remote publication."],
    }


def _self_test_packet() -> dict[str, Any]:
    return packet_builder.build_packet(
        {
            "phase": "final",
            "context_mode": "packet",
            "task": "Publish the advisor review skill.",
            "decision": "Decide whether the skill is ready for publication.",
            "constraints": ["Do not modify external state during review."],
            "evidence": [
                {
                    "source": "focused test output",
                    "fact": "All deterministic tests passed locally.",
                }
            ],
            "attempts": [],
            "proposal": "Declare the script and package ready for publication.",
            "changes": ["Added an isolated Codex CLI runner."],
            "validation": [
                {
                    "check": "Run the focused integration test.",
                    "result": "The test exited zero.",
                }
            ],
            "conflicts": [],
            "limitations": [],
            "artifacts": [],
        }
    )


def self_test() -> None:
    packet = _self_test_packet()
    assert validate_packet(packet) == packet
    prompt = build_prompt(packet, "# Final\nReview the completion claim.")
    assert "Do not call tools." in prompt
    assert "Cite supplied context IDs" in prompt
    assert packet["context_hash"] in prompt

    with tempfile.TemporaryDirectory(prefix="advisor-review-self-test-") as name:
        temp_dir = Path(name)
        schema_path = temp_dir / "schema.json"
        output_path = temp_dir / "output.json"
        command = build_command(
            "codex", "xhigh", temp_dir, schema_path, output_path
        )
        assert command[command.index("--model") + 1] == DEFAULT_MODEL
        assert command[command.index("--config") + 1] == (
            'model_reasoning_effort="xhigh"'
        )
        assert command[command.index("--sandbox") + 1] == "read-only"
        assert "--ephemeral" in command
        assert "--ignore-user-config" in command
        disabled = [
            command[index + 1]
            for index, value in enumerate(command)
            if value == "--disable"
        ]
        assert disabled == [
            "multi_agent",
            "plugins",
            "remote_plugin",
            "skill_search",
        ]

        advice = _valid_advice()
        valid_codex = temp_dir / "valid-codex"
        valid_codex.write_text(
            "#!/usr/bin/env python3\n"
            "import json, pathlib, sys\n"
            f"advice = {advice!r}\n"
            "output = pathlib.Path(sys.argv[sys.argv.index("
            "'--output-last-message') + 1])\n"
            "output.write_text(json.dumps(advice), encoding='utf-8')\n",
            encoding="utf-8",
        )
        valid_codex.chmod(0o755)
        returned = run_advisor(
            packet,
            effort="high",
            codex_bin=str(valid_codex),
            timeout=10,
            rubric="# Test rubric",
        )
        assert returned["verdict"] == "revise"
        receipt = build_receipt(
            returned,
            effort="high",
            packet=packet,
            duration_ms=1,
            prompt_hash=hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
        )
        assert receipt["schema_version"] == RUN_SCHEMA_VERSION
        assert receipt["request"]["requested_model"] == DEFAULT_MODEL
        assert receipt["request"]["observed_model"] is None
        assert receipt["request"]["identity_verification"].startswith("unverified")

        generic_codex = temp_dir / "generic-codex"
        bad = _valid_advice()
        bad["findings"][0]["claim"] = "Be careful."
        generic_codex.write_text(
            "#!/usr/bin/env python3\n"
            "import json, pathlib, sys\n"
            f"advice = {bad!r}\n"
            "output = pathlib.Path(sys.argv[sys.argv.index("
            "'--output-last-message') + 1])\n"
            "output.write_text(json.dumps(advice), encoding='utf-8')\n",
            encoding="utf-8",
        )
        generic_codex.chmod(0o755)
        try:
            run_advisor(
                packet,
                effort="high",
                codex_bin=str(generic_codex),
                timeout=10,
                rubric="# Test rubric",
            )
        except advice_validator.AdviceError as exc:
            assert "generic advice" in str(exc)
        else:
            raise AssertionError("generic reviewer output was accepted")

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
                effort="high",
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
                effort="xhigh",
                codex_bin=str(failing_codex),
                timeout=10,
                rubric="# Test rubric",
            )
        except RunnerError as exc:
            assert "exited with 7" in str(exc)
        else:
            raise AssertionError("nonzero reviewer exit was accepted")

        network_codex = temp_dir / "network-codex"
        network_codex.write_text(
            "#!/usr/bin/env python3\n"
            "import sys\n"
            "print('stream disconnected: error sending request', file=sys.stderr)\n"
            "raise SystemExit(1)\n",
            encoding="utf-8",
        )
        network_codex.chmod(0o755)
        try:
            run_advisor(
                packet,
                effort="high",
                codex_bin=str(network_codex),
                timeout=10,
                rubric="# Test rubric",
            )
        except RunnerError as exc:
            assert "blocking subprocess network access" in str(exc)
        else:
            raise AssertionError("network failure did not block the review")

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
                effort="max",
                codex_bin=str(timeout_codex),
                timeout=1,
                rubric="# Test rubric",
            )
        except RunnerError as exc:
            assert "timed out" in str(exc)
        else:
            raise AssertionError("reviewer timeout was accepted")

    try:
        build_command("codex", "medium", Path("."), Path("s"), Path("o"))
    except RunnerError:
        pass
    else:
        raise AssertionError("unsupported advisor effort was accepted")

    print(
        json.dumps(
            {
                "self_test": "PASS",
                "backend": "codex-exec",
                "requested_model": DEFAULT_MODEL,
                "identity_verification": "unverified",
                "allowed_efforts": list(ALLOWED_EFFORTS),
                "run_schema": RUN_SCHEMA_VERSION,
            }
        )
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", default="-", help="Context packet JSON, or -")
    parser.add_argument(
        "--effort",
        choices=ALLOWED_EFFORTS,
        default=DEFAULT_EFFORT,
        help="Requested advisor effort; model request is fixed to gpt-5.6-sol",
    )
    parser.add_argument("--codex-bin", default=shutil.which("codex") or "codex")
    parser.add_argument("--timeout", type=int, default=300)
    parser.add_argument("--output", help="Optional path for the validated receipt")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        self_test()
        return
    if args.timeout < 1:
        raise RunnerError("--timeout must be at least 1 second")

    packet = validate_packet(load_packet(args.input))
    rubric_path = Path(__file__).resolve().parents[1] / "references/review-rubric.md"
    rubric = rubric_path.read_text(encoding="utf-8")
    prompt = build_prompt(packet, rubric)
    started = time.monotonic()
    advice = run_advisor(
        packet,
        effort=args.effort,
        codex_bin=args.codex_bin,
        timeout=args.timeout,
        rubric=rubric,
    )
    duration_ms = max(0, round((time.monotonic() - started) * 1_000))
    receipt = build_receipt(
        advice,
        effort=args.effort,
        packet=packet,
        duration_ms=duration_ms,
        prompt_hash=hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
    )
    rendered = json.dumps(receipt, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        Path(args.output).write_text(rendered, encoding="utf-8")
    sys.stdout.write(rendered)


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

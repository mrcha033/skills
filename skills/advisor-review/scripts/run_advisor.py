#!/usr/bin/env python3
"""Run an isolated, evidence-linked review through the active agent CLI."""

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


RUNTIMES = ("codex", "claude", "opencode", "gemini")
ALLOWED_EFFORTS = ("high", "xhigh", "max")
RUN_SCHEMA_VERSION = "advisor-run-2.0"
OUTPUT_SCHEMA = advice_validator.OUTPUT_SCHEMA
JSON_SCHEMA_DRAFT_URI = "https://json-schema.org/draft/2020-12/schema"
SESSION_MARKERS = {
    "OPENCODE": "opencode",
    "CLAUDE_CODE": "claude",
    "CLAUDECODE": "claude",
    "CODEX_CLI": "codex",
    "GEMINI_CLI": "gemini",
}


class RunnerError(RuntimeError):
    """Raised when the isolated reviewer cannot return valid advice."""


def _truthy(value: str | None) -> bool:
    return value is not None and value.strip().lower() not in {
        "",
        "0",
        "false",
        "no",
        "off",
    }


def detect_runtime(
    requested: str = "auto",
    *,
    environ: dict[str, str] | None = None,
    available: list[str] | None = None,
) -> str:
    if requested != "auto":
        if requested not in RUNTIMES:
            raise RunnerError(f"runtime must be one of: auto, {', '.join(RUNTIMES)}")
        return requested

    environment = os.environ if environ is None else environ
    explicit = environment.get("ADVISOR_RUNTIME", "").strip().lower()
    if explicit:
        if explicit not in RUNTIMES:
            raise RunnerError(
                f"ADVISOR_RUNTIME must be one of: {', '.join(RUNTIMES)}"
            )
        return explicit

    marked = {
        runtime
        for name, runtime in SESSION_MARKERS.items()
        if _truthy(environment.get(name))
    }
    if len(marked) == 1:
        return marked.pop()
    if len(marked) > 1:
        raise RunnerError(
            "session runtime markers conflict; pass --runtime explicitly"
        )

    candidates = available
    if candidates is None:
        candidates = [runtime for runtime in RUNTIMES if shutil.which(runtime)]
    if len(candidates) == 1:
        return candidates[0]
    if not candidates:
        raise RunnerError(
            "could not detect the session runtime; pass --runtime explicitly"
        )
    raise RunnerError(
        "multiple agent CLIs are installed but the session runtime is unknown; "
        "pass --runtime explicitly"
    )


def resolve_runtime(
    requested: str = "auto", runtime_bin: str | None = None
) -> tuple[str, str]:
    runtime = detect_runtime(requested)
    binary = runtime_bin or shutil.which(runtime) or runtime
    return runtime, binary


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


def prepare_environment(temp_dir: Path, runtime: str) -> dict[str, str]:
    environment = os.environ.copy()
    environment["NO_COLOR"] = "1"
    environment["ADVISOR_REVIEW_CHILD"] = "1"
    if runtime == "codex":
        environment["CODEX_HOME"] = str(prepare_isolated_codex_home(temp_dir))
    elif runtime == "opencode":
        environment["OPENCODE_PURE"] = "1"
        environment["OPENCODE_DISABLE_PROJECT_CONFIG"] = "1"
        environment["OPENCODE_DISABLE_EXTERNAL_SKILLS"] = "1"
        environment["OPENCODE_DISABLE_CLAUDE_CODE_SKILLS"] = "1"
        environment["OPENCODE_CONFIG_CONTENT"] = json.dumps(
            {
                "$schema": "https://opencode.ai/config.json",
                "permission": {
                    "read": "deny",
                    "edit": "deny",
                    "bash": "deny",
                    "task": "deny",
                    "external_directory": "deny",
                },
            }
        )
    return environment


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


def build_prompt(
    packet: dict[str, Any], rubric: str, runtime: str, model: str, effort: str
) -> str:
    return (
        "You are an independent diagnostic reviewer, not the parent implementer.\n"
        f"Review phase: {packet['phase']}\n"
        f"Advisor runtime: {runtime}\n"
        f"Requested advisor model: {model}\n"
        f"Requested advisor effort: {effort}\n\n"
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
        "REQUIRED OUTPUT JSON SCHEMA\n"
        f"{json.dumps(OUTPUT_SCHEMA, ensure_ascii=False, indent=2)}\n\n"
        "REVIEW RUBRIC\n"
        f"{rubric.strip()}\n\n"
        "CONTEXT PACKET\n"
        f"{json.dumps(packet, ensure_ascii=False, indent=2)}\n"
    )


def build_command(
    runtime: str,
    model: str,
    effort: str,
    runtime_bin: str,
    work_dir: Path,
    schema_path: Path,
    output_path: Path,
    prompt: str | None = None,
) -> list[str]:
    if runtime not in RUNTIMES:
        raise RunnerError(f"runtime must be one of: {', '.join(RUNTIMES)}")
    if not model.strip():
        raise RunnerError("model must not be empty")
    if effort not in ALLOWED_EFFORTS:
        raise RunnerError(f"effort must be one of: {', '.join(ALLOWED_EFFORTS)}")

    if runtime == "codex":
        return [
            runtime_bin,
            "exec",
            "--model",
            model,
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
    if runtime == "claude":
        return [
            runtime_bin,
            "--print",
            "--model",
            model,
            "--effort",
            effort,
            "--output-format",
            "json",
            "--json-schema",
            json.dumps(OUTPUT_SCHEMA, separators=(",", ":")),
            "--no-session-persistence",
            "--permission-mode",
            "plan",
            "--tools",
            "",
            "--disable-slash-commands",
        ]
    if runtime == "opencode":
        command = [
            runtime_bin,
            "--pure",
            "run",
            "--model",
            model,
            "--variant",
            effort,
            "--format",
            "json",
        ]
        if prompt:
            command.append(prompt)
        return command
    return [
        runtime_bin,
        "--model",
        model,
        "--prompt",
        "Review the bounded advisor packet supplied on stdin.",
        "--output-format",
        "json",
        "--approval-mode",
        "plan",
    ]


def _json_candidates(value: Any) -> list[Any]:
    candidates: list[Any] = []
    if isinstance(value, dict):
        if value.get("schema_version") == advice_validator.SCHEMA_VERSION:
            candidates.append(value)
        for key in (
            "result",
            "response",
            "text",
            "content",
            "output",
            "message",
            "part",
            "data",
            "value",
            "payload",
        ):
            if key in value:
                candidates.extend(_json_candidates(value[key]))
    elif isinstance(value, list):
        for item in value:
            candidates.extend(_json_candidates(item))
    elif isinstance(value, str):
        text = value.strip()
        if text.startswith("```") and text.endswith("```"):
            text = text.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
        try:
            candidates.extend(_json_candidates(json.loads(text)))
        except json.JSONDecodeError:
            decoder = json.JSONDecoder()
            for index, character in enumerate(text):
                if character != "{":
                    continue
                try:
                    parsed, _ = decoder.raw_decode(text[index:])
                except json.JSONDecodeError:
                    continue
                candidates.extend(_json_candidates(parsed))
    return candidates


def parse_advice_output(
    raw: str, *, known_refs: set[str]
) -> dict[str, Any]:
    errors: list[str] = []
    for candidate in _json_candidates(raw):
        if (
            isinstance(candidate, dict)
            and candidate.get("$schema") == JSON_SCHEMA_DRAFT_URI
        ):
            # OpenCode may echo the schema metadata when producing structured
            # JSON. It is transport metadata, not part of advisor advice;
            # keep all other unexpected fields strict.
            candidate = {
                key: value for key, value in candidate.items() if key != "$schema"
            }
        try:
            return advice_validator.validate(candidate, known_refs=known_refs)
        except advice_validator.AdviceError as exc:
            errors.append(str(exc))
    detail = errors[-1] if errors else "no JSON advice object found"
    raise RunnerError(f"reviewer returned no valid advice JSON: {detail}")


def run_advisor(
    packet: dict[str, Any],
    *,
    runtime: str,
    model: str,
    effort: str,
    runtime_bin: str,
    timeout: int,
    rubric: str,
) -> dict[str, Any]:
    validated_packet = validate_packet(packet)
    prompt = build_prompt(validated_packet, rubric, runtime, model, effort)

    with tempfile.TemporaryDirectory(prefix="advisor-review-") as temp_name:
        temp_dir = Path(temp_name)
        schema_path = temp_dir / "advice-schema.json"
        output_path = temp_dir / "advice.json"
        schema_path.write_text(
            json.dumps(OUTPUT_SCHEMA, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        command = build_command(
            runtime,
            model,
            effort,
            runtime_bin,
            temp_dir,
            schema_path,
            output_path,
            prompt,
        )
        environment = prepare_environment(temp_dir, runtime)
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
            raise RunnerError(f"could not launch {runtime} CLI: {exc}") from exc

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
                f"{runtime} reviewer exited with {result.returncode}: "
                f"{detail or 'no diagnostic output'}{network_hint}"
            )

        if runtime == "codex":
            if not output_path.is_file():
                raise RunnerError("reviewer did not write a final response")
            raw = output_path.read_text(encoding="utf-8")
        else:
            raw = result.stdout
        return parse_advice_output(
            raw,
            known_refs=advice_validator.known_refs_from_packet(validated_packet),
        )


def build_receipt(
    advice: dict[str, Any],
    *,
    runtime: str,
    model: str,
    effort: str,
    packet: dict[str, Any],
    duration_ms: int,
    prompt_hash: str,
) -> dict[str, Any]:
    if runtime not in RUNTIMES:
        raise RunnerError(f"runtime must be one of: {', '.join(RUNTIMES)}")
    if not model.strip():
        raise RunnerError("model must not be empty")
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
        "backend": f"{runtime}-cli",
        "request": {
            "runtime": runtime,
            "requested_model": model,
            "requested_effort": effort,
            "observed_model": None,
            "observed_effort": None,
            "identity_verification": "unverified_by_runtime_output",
        },
        "isolation": {
            "ephemeral": True,
            "runtime": runtime,
            "sandbox": "read-only_or_plan_mode",
            "tools_requested": False,
            "nested_agents": False,
            "plugin_loading": "disabled_or_not_requested",
            "session_persistence": "disabled_or_ephemeral",
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
            "changes": ["Added runtime-selected advisor CLI adapters."],
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
    prompt = build_prompt(packet, "# Final\nReview the completion claim.", "codex", "test-model", "xhigh")
    assert "Do not call tools." in prompt
    assert "Requested advisor model: test-model" in prompt
    assert "Requested advisor effort: xhigh" in prompt
    assert '"evidence_refs"' in prompt
    assert packet["context_hash"] in prompt
    assert detect_runtime("auto", environ={"OPENCODE": "1"}, available=[]) == "opencode"
    try:
        detect_runtime("auto", environ={}, available=["codex", "claude"])
    except RunnerError as exc:
        assert "multiple agent CLIs" in str(exc)
    else:
        raise AssertionError("ambiguous runtime detection was accepted")

    with tempfile.TemporaryDirectory(prefix="advisor-review-self-test-") as name:
        temp_dir = Path(name)
        schema_path = temp_dir / "schema.json"
        output_path = temp_dir / "output.json"
        codex_command = build_command(
            "codex", "test-model", "xhigh", "codex", temp_dir, schema_path, output_path
        )
        assert codex_command[codex_command.index("--model") + 1] == "test-model"
        assert codex_command[codex_command.index("--config") + 1] == (
            'model_reasoning_effort="xhigh"'
        )
        assert "--sandbox" in codex_command
        claude_command = build_command(
            "claude", "claude-model", "high", "claude", temp_dir, schema_path, output_path
        )
        assert claude_command[claude_command.index("--model") + 1] == "claude-model"
        assert claude_command[claude_command.index("--effort") + 1] == "high"
        assert "--json-schema" in claude_command
        assert build_command(
            "opencode", "open-model", "max", "opencode", temp_dir, schema_path, output_path
        )[:3] == ["opencode", "--pure", "run"]

        advice = _valid_advice()
        echoed_schema_advice = {**advice, "$schema": JSON_SCHEMA_DRAFT_URI}
        assert parse_advice_output(
            json.dumps(echoed_schema_advice),
            known_refs=advice_validator.known_refs_from_packet(packet),
        ) == advice
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
            runtime="codex",
            model="test-model",
            effort="high",
            runtime_bin=str(valid_codex),
            timeout=10,
            rubric="# Test rubric",
        )
        assert returned["verdict"] == "revise"
        receipt = build_receipt(
            returned,
            runtime="codex",
            model="test-model",
            effort="high",
            packet=packet,
            duration_ms=1,
            prompt_hash=hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
        )
        assert receipt["backend"] == "codex-cli"
        assert receipt["request"]["requested_model"] == "test-model"
        assert receipt["request"]["observed_model"] is None
        assert receipt["request"]["identity_verification"].startswith("unverified")

        incomplete = _valid_advice()
        del incomplete["diagnosis"]["evidence_refs"]
        try:
            parse_advice_output(
                json.dumps(incomplete),
                known_refs=advice_validator.known_refs_from_packet(packet),
            )
        except RunnerError as exc:
            assert "evidence_refs" in str(exc)
        else:
            raise AssertionError("incomplete advisor JSON was accepted")

        for runtime in ("claude", "opencode", "gemini"):
            valid_runtime = temp_dir / f"valid-{runtime}"
            valid_runtime.write_text(
                "#!/usr/bin/env python3\n"
                "import json\n"
                f"print(json.dumps({advice!r}))\n",
                encoding="utf-8",
            )
            valid_runtime.chmod(0o755)
            returned = run_advisor(
                packet,
                runtime=runtime,
                model=f"{runtime}-model",
                effort="high",
                runtime_bin=str(valid_runtime),
                timeout=10,
                rubric="# Test rubric",
            )
            assert returned["verdict"] == "revise"

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
                runtime="codex",
                model="test-model",
                effort="high",
                runtime_bin=str(invalid_codex),
                timeout=10,
                rubric="# Test rubric",
            )
        except RunnerError as exc:
            assert "no valid advice JSON" in str(exc)
        else:
            raise AssertionError("invalid reviewer JSON was accepted")

    try:
        build_command("codex", "test-model", "medium", "codex", Path("."), Path("s"), Path("o"))
    except RunnerError:
        pass
    else:
        raise AssertionError("unsupported advisor effort was accepted")

    print(
        json.dumps(
            {
                "self_test": "PASS",
                "runtimes": list(RUNTIMES),
                "requested_model": "caller-selected",
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
        "--runtime",
        choices=("auto", *RUNTIMES),
        default="auto",
        help="Reviewer CLI; auto uses the active session marker",
    )
    parser.add_argument("--runtime-bin", help="Override the selected runtime binary")
    parser.add_argument(
        "--model",
        help="Advisor model selected by the calling agent",
    )
    parser.add_argument(
        "--effort",
        choices=ALLOWED_EFFORTS,
        help="Advisor effort selected by the calling agent",
    )
    parser.add_argument("--timeout", type=int, default=300)
    parser.add_argument("--output", help="Optional path for the validated receipt")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        self_test()
        return
    if args.timeout < 1:
        raise RunnerError("--timeout must be at least 1 second")
    if not args.model:
        raise RunnerError("--model is required for a real review")
    if not args.effort:
        raise RunnerError("--effort is required for a real review")

    runtime, runtime_bin = resolve_runtime(args.runtime, args.runtime_bin)
    packet = validate_packet(load_packet(args.input))
    rubric_path = Path(__file__).resolve().parents[1] / "references/review-rubric.md"
    rubric = rubric_path.read_text(encoding="utf-8")
    prompt = build_prompt(packet, rubric, runtime, args.model, args.effort)
    started = time.monotonic()
    advice = run_advisor(
        packet,
        runtime=runtime,
        model=args.model,
        effort=args.effort,
        runtime_bin=runtime_bin,
        timeout=args.timeout,
        rubric=rubric,
    )
    duration_ms = max(0, round((time.monotonic() - started) * 1_000))
    receipt = build_receipt(
        advice,
        runtime=runtime,
        model=args.model,
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

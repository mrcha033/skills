#!/usr/bin/env python3
"""Build a bounded, source-anchored context packet for Advisor Review."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any


SCHEMA_VERSION = "advisor-context-2.0"
PHASES = {"plan", "stuck", "pivot", "final"}
CONTEXT_MODES = {"packet", "bundle"}
MAX_ITEM_CHARS = 2_000
MAX_ITEMS = 20
MAX_SCALAR_CHARS = 8_000
MAX_ARTIFACTS = 8
MAX_ARTIFACT_CHARS = 12_000
MAX_ARTIFACT_TOTAL_CHARS = 64_000

TEXT_ENTRY_FIELDS = {
    "constraints": "C",
    "changes": "CH",
    "conflicts": "K",
    "limitations": "L",
}

SECRET_PATTERNS = (
    re.compile(r"\b(?:sk|gh[opusr])_[A-Za-z0-9_-]{12,}\b"),
    re.compile(r"\bAKIA[A-Z0-9]{16}\b"),
    re.compile(r"(?i)\b(bearer\s+)[A-Za-z0-9._~+/=-]{12,}"),
    re.compile(
        r"(?i)\b(api[_-]?key|access[_-]?token|auth[_-]?token|password|secret)"
        r"(\s*[:=]\s*)[^\s,;]+"
    ),
)

PACKET_FIELDS = {
    "schema_version",
    "phase",
    "context_mode",
    "task",
    "decision",
    "constraints",
    "evidence",
    "attempts",
    "proposal",
    "changes",
    "validation",
    "conflicts",
    "limitations",
    "artifacts",
    "packet_meta",
    "context_hash",
}


class PacketError(ValueError):
    """Raised when input cannot satisfy the packet contract."""


def redact(text: str) -> str:
    value = text
    value = SECRET_PATTERNS[0].sub("[REDACTED_TOKEN]", value)
    value = SECRET_PATTERNS[1].sub("[REDACTED_AWS_KEY]", value)
    value = SECRET_PATTERNS[2].sub(r"\1[REDACTED_TOKEN]", value)
    value = SECRET_PATTERNS[3].sub(r"\1\2[REDACTED]", value)
    return value


def bounded_text(value: Any, field: str, limit: int) -> str:
    if not isinstance(value, str):
        raise PacketError(f"{field} must be a string")
    text = redact(value.strip())
    if not text:
        raise PacketError(f"{field} must not be empty")
    if len(text) > limit:
        raise PacketError(
            f"{field} exceeds {limit} characters; summarize it or use a bounded artifact"
        )
    return text


def _require_list(value: Any, field: str) -> list[Any]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise PacketError(f"{field} must be a list")
    if len(value) > MAX_ITEMS:
        raise PacketError(f"{field} must contain at most {MAX_ITEMS} items")
    return value


def _exact_keys(value: dict[str, Any], allowed: set[str], field: str) -> None:
    extra = value.keys() - allowed
    missing = allowed - value.keys()
    if missing:
        raise PacketError(f"{field} is missing: {', '.join(sorted(missing))}")
    if extra:
        raise PacketError(f"{field} has unexpected fields: {', '.join(sorted(extra))}")


def normalize_text_entries(value: Any, field: str, prefix: str) -> list[dict[str, str]]:
    entries: list[dict[str, str]] = []
    for index, item in enumerate(_require_list(value, field), 1):
        if isinstance(item, str):
            text = item
        elif isinstance(item, dict):
            _exact_keys(item, {"text"}, f"{field}[{index - 1}]")
            text = item["text"]
        else:
            raise PacketError(f"{field}[{index - 1}] must be a string or object")
        entries.append(
            {
                "id": f"{prefix}{index}",
                "text": bounded_text(text, f"{field}[{index - 1}]", MAX_ITEM_CHARS),
            }
        )
    return entries


def _split_cli_entry(value: str, parts: int, field: str) -> list[str]:
    split = [part.strip() for part in value.split(" :: ", parts - 1)]
    if len(split) != parts or any(not part for part in split):
        example = {
            "evidence": "SOURCE :: OBSERVED FACT",
            "attempts": "ACTION :: EXACT RESULT",
            "validation": "CHECK :: EXACT RESULT",
        }[field]
        raise PacketError(f"{field} string must use: {example}")
    return split


def normalize_evidence(value: Any) -> list[dict[str, str]]:
    entries: list[dict[str, str]] = []
    for index, item in enumerate(_require_list(value, "evidence"), 1):
        if isinstance(item, str):
            source, fact = _split_cli_entry(item, 2, "evidence")
        elif isinstance(item, dict):
            _exact_keys(item, {"source", "fact"}, f"evidence[{index - 1}]")
            source, fact = item["source"], item["fact"]
        else:
            raise PacketError(f"evidence[{index - 1}] must be a string or object")
        entries.append(
            {
                "id": f"E{index}",
                "source": bounded_text(
                    source, f"evidence[{index - 1}].source", MAX_ITEM_CHARS
                ),
                "fact": bounded_text(
                    fact, f"evidence[{index - 1}].fact", MAX_ITEM_CHARS
                ),
            }
        )
    return entries


def normalize_attempts(value: Any) -> list[dict[str, str]]:
    entries: list[dict[str, str]] = []
    for index, item in enumerate(_require_list(value, "attempts"), 1):
        if isinstance(item, str):
            action, result = _split_cli_entry(item, 2, "attempts")
        elif isinstance(item, dict):
            _exact_keys(item, {"action", "result"}, f"attempts[{index - 1}]")
            action, result = item["action"], item["result"]
        else:
            raise PacketError(f"attempts[{index - 1}] must be a string or object")
        entries.append(
            {
                "id": f"A{index}",
                "action": bounded_text(
                    action, f"attempts[{index - 1}].action", MAX_ITEM_CHARS
                ),
                "result": bounded_text(
                    result, f"attempts[{index - 1}].result", MAX_ITEM_CHARS
                ),
            }
        )
    return entries


def normalize_validation(value: Any) -> list[dict[str, str]]:
    entries: list[dict[str, str]] = []
    for index, item in enumerate(_require_list(value, "validation"), 1):
        if isinstance(item, str):
            check, result = _split_cli_entry(item, 2, "validation")
        elif isinstance(item, dict):
            _exact_keys(item, {"check", "result"}, f"validation[{index - 1}]")
            check, result = item["check"], item["result"]
        else:
            raise PacketError(f"validation[{index - 1}] must be a string or object")
        entries.append(
            {
                "id": f"V{index}",
                "check": bounded_text(
                    check, f"validation[{index - 1}].check", MAX_ITEM_CHARS
                ),
                "result": bounded_text(
                    result, f"validation[{index - 1}].result", MAX_ITEM_CHARS
                ),
            }
        )
    return entries


def _artifact_from_path(path_value: str) -> dict[str, str]:
    path = Path(path_value).expanduser()
    try:
        content = path.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise PacketError(f"artifact must be UTF-8 text: {path}") from exc
    except OSError as exc:
        raise PacketError(f"cannot read artifact {path}: {exc}") from exc
    return {"label": path.name, "source": str(path), "content": content}


def normalize_artifacts(
    value: Any,
    *,
    artifact_paths: list[str] | None,
    allow_truncation: bool,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    raw_items = list(_require_list(value, "artifacts"))
    raw_items.extend(_artifact_from_path(path) for path in (artifact_paths or []))
    if len(raw_items) > MAX_ARTIFACTS:
        raise PacketError(f"artifacts must contain at most {MAX_ARTIFACTS} items")

    artifacts: list[dict[str, Any]] = []
    truncations: list[dict[str, Any]] = []
    total_chars = 0
    for index, item in enumerate(raw_items, 1):
        if isinstance(item, str):
            normalized = _artifact_from_path(item)
        elif isinstance(item, dict):
            if "path" in item:
                _exact_keys(item, {"path"}, f"artifacts[{index - 1}]")
                normalized = _artifact_from_path(item["path"])
            else:
                _exact_keys(
                    item,
                    {"label", "source", "content"},
                    f"artifacts[{index - 1}]",
                )
                normalized = item
        else:
            raise PacketError(f"artifacts[{index - 1}] must be a path or object")

        label = bounded_text(
            normalized["label"], f"artifacts[{index - 1}].label", 240
        )
        source = bounded_text(
            normalized["source"], f"artifacts[{index - 1}].source", MAX_ITEM_CHARS
        )
        content = redact(str(normalized["content"]).strip())
        if not content:
            raise PacketError(f"artifacts[{index - 1}].content must not be empty")
        original_chars = len(content)
        digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
        truncated = False
        if original_chars > MAX_ARTIFACT_CHARS:
            if not allow_truncation:
                raise PacketError(
                    f"artifact {label} exceeds {MAX_ARTIFACT_CHARS} characters; "
                    "sanitize/summarize it or pass --allow-artifact-truncation"
                )
            marker = "\n...[ARTIFACT TRUNCATED]..."
            content = content[: MAX_ARTIFACT_CHARS - len(marker)] + marker
            truncated = True
        total_chars += len(content)
        if total_chars > MAX_ARTIFACT_TOTAL_CHARS:
            raise PacketError(
                f"artifact content exceeds {MAX_ARTIFACT_TOTAL_CHARS} total characters"
            )
        artifact_id = f"D{index}"
        artifacts.append(
            {
                "id": artifact_id,
                "label": label,
                "source": source,
                "content_sha256": digest,
                "original_chars": original_chars,
                "included_chars": len(content),
                "truncated": truncated,
                "content": content,
            }
        )
        if truncated:
            truncations.append(
                {
                    "artifact_id": artifact_id,
                    "original_chars": original_chars,
                    "included_chars": len(content),
                }
            )
    return artifacts, truncations


def _check_phase_minimums(packet: dict[str, Any]) -> None:
    if not packet["constraints"]:
        raise PacketError("every phase requires at least one constraint")
    if not packet["evidence"]:
        raise PacketError("every phase requires at least one source-anchored evidence item")

    phase = packet["phase"]
    if phase == "stuck":
        if not packet["attempts"]:
            raise PacketError("stuck phase requires at least one exact attempted action/result")
        if not packet["conflicts"]:
            raise PacketError("stuck phase requires at least one unresolved conflict")
    elif phase == "pivot":
        if not packet["attempts"]:
            raise PacketError("pivot phase requires evidence from the current approach")
        if not packet["conflicts"]:
            raise PacketError("pivot phase requires switching-cost or rollback uncertainty")
    elif phase == "final":
        if not packet["changes"]:
            raise PacketError("final phase requires at least one material change")
        if not packet["validation"]:
            raise PacketError("final phase requires at least one exact validation result")

    if packet["context_mode"] == "packet" and packet["artifacts"]:
        raise PacketError("packet mode cannot include artifacts; use context_mode=bundle")
    if packet["context_mode"] == "bundle" and not packet["artifacts"]:
        raise PacketError("bundle mode requires at least one bounded artifact")


def _canonical_hash(packet_without_hash: dict[str, Any]) -> str:
    canonical = json.dumps(
        packet_without_hash,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def build_packet(
    raw: dict[str, Any],
    *,
    artifact_paths: list[str] | None = None,
    allow_artifact_truncation: bool = False,
) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise PacketError("input must be a JSON object")

    phase = raw.get("phase")
    if phase not in PHASES:
        raise PacketError(f"phase must be one of: {', '.join(sorted(PHASES))}")
    context_mode = raw.get("context_mode", "packet")
    if context_mode not in CONTEXT_MODES:
        raise PacketError(
            f"context_mode must be one of: {', '.join(sorted(CONTEXT_MODES))}"
        )

    artifacts, truncations = normalize_artifacts(
        raw.get("artifacts", []),
        artifact_paths=artifact_paths,
        allow_truncation=allow_artifact_truncation,
    )
    packet: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "phase": phase,
        "context_mode": context_mode,
        "task": bounded_text(raw.get("task"), "task", MAX_SCALAR_CHARS),
        "decision": bounded_text(raw.get("decision"), "decision", MAX_SCALAR_CHARS),
        "constraints": normalize_text_entries(
            raw.get("constraints", []), "constraints", "C"
        ),
        "evidence": normalize_evidence(raw.get("evidence", [])),
        "attempts": normalize_attempts(raw.get("attempts", [])),
        "proposal": bounded_text(raw.get("proposal"), "proposal", MAX_SCALAR_CHARS),
        "changes": normalize_text_entries(raw.get("changes", []), "changes", "CH"),
        "validation": normalize_validation(raw.get("validation", [])),
        "conflicts": normalize_text_entries(
            raw.get("conflicts", []), "conflicts", "K"
        ),
        "limitations": normalize_text_entries(
            raw.get("limitations", []), "limitations", "L"
        ),
        "artifacts": artifacts,
        "packet_meta": {
            "context_completeness": (
                "partial" if raw.get("limitations") or truncations else "complete"
            ),
            "artifact_count": len(artifacts),
            "artifact_chars": sum(item["included_chars"] for item in artifacts),
            "artifact_truncations": truncations,
        },
    }
    _check_phase_minimums(packet)
    packet["context_hash"] = _canonical_hash(packet)
    return packet


def validate_packet(packet: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(packet, dict):
        raise PacketError("context packet must be a JSON object")
    missing = PACKET_FIELDS - packet.keys()
    extra = packet.keys() - PACKET_FIELDS
    if missing:
        raise PacketError(f"context packet is missing: {', '.join(sorted(missing))}")
    if extra:
        raise PacketError(
            f"context packet has unexpected fields: {', '.join(sorted(extra))}"
        )
    if packet["schema_version"] != SCHEMA_VERSION:
        raise PacketError(f"schema_version must be {SCHEMA_VERSION}")
    expected_hash = _canonical_hash(
        {key: value for key, value in packet.items() if key != "context_hash"}
    )
    if packet["context_hash"] != expected_hash:
        raise PacketError("context packet hash does not match its normalized content")

    if packet["phase"] not in PHASES:
        raise PacketError("context packet phase is invalid")
    if packet["context_mode"] not in CONTEXT_MODES:
        raise PacketError("context packet context_mode is invalid")
    if bounded_text(packet["task"], "task", MAX_SCALAR_CHARS) != packet["task"]:
        raise PacketError("context packet task is not normalized")
    if (
        bounded_text(packet["decision"], "decision", MAX_SCALAR_CHARS)
        != packet["decision"]
    ):
        raise PacketError("context packet decision is not normalized")
    if (
        bounded_text(packet["proposal"], "proposal", MAX_SCALAR_CHARS)
        != packet["proposal"]
    ):
        raise PacketError("context packet proposal is not normalized")

    entry_shapes = {
        "constraints": {"id", "text"},
        "evidence": {"id", "source", "fact"},
        "attempts": {"id", "action", "result"},
        "changes": {"id", "text"},
        "validation": {"id", "check", "result"},
        "conflicts": {"id", "text"},
        "limitations": {"id", "text"},
    }
    for field, keys in entry_shapes.items():
        if not isinstance(packet[field], list):
            raise PacketError(f"context packet {field} must be a list")
        if len(packet[field]) > MAX_ITEMS:
            raise PacketError(f"{field} must contain at most {MAX_ITEMS} items")
        for index, item in enumerate(packet[field]):
            if not isinstance(item, dict):
                raise PacketError(f"{field}[{index}] must be an object")
            _exact_keys(item, keys, f"{field}[{index}]")

    normalized_fields = {
        "constraints": normalize_text_entries(
            [{"text": item["text"]} for item in packet["constraints"]],
            "constraints",
            "C",
        ),
        "evidence": normalize_evidence(
            [
                {"source": item["source"], "fact": item["fact"]}
                for item in packet["evidence"]
            ]
        ),
        "attempts": normalize_attempts(
            [
                {"action": item["action"], "result": item["result"]}
                for item in packet["attempts"]
            ]
        ),
        "changes": normalize_text_entries(
            [{"text": item["text"]} for item in packet["changes"]],
            "changes",
            "CH",
        ),
        "validation": normalize_validation(
            [
                {"check": item["check"], "result": item["result"]}
                for item in packet["validation"]
            ]
        ),
        "conflicts": normalize_text_entries(
            [{"text": item["text"]} for item in packet["conflicts"]],
            "conflicts",
            "K",
        ),
        "limitations": normalize_text_entries(
            [{"text": item["text"]} for item in packet["limitations"]],
            "limitations",
            "L",
        ),
    }
    for field, normalized in normalized_fields.items():
        if packet[field] != normalized:
            raise PacketError(f"context packet {field} is not canonical")

    if not isinstance(packet["artifacts"], list):
        raise PacketError("context packet artifacts must be a list")
    if len(packet["artifacts"]) > MAX_ARTIFACTS:
        raise PacketError(f"artifacts must contain at most {MAX_ARTIFACTS} items")
    artifact_keys = {
        "id",
        "label",
        "source",
        "content_sha256",
        "original_chars",
        "included_chars",
        "truncated",
        "content",
    }
    expected_truncations: list[dict[str, Any]] = []
    total_chars = 0
    for index, artifact in enumerate(packet["artifacts"], 1):
        if not isinstance(artifact, dict):
            raise PacketError(f"artifacts[{index - 1}] must be an object")
        _exact_keys(artifact, artifact_keys, f"artifacts[{index - 1}]")
        if artifact["id"] != f"D{index}":
            raise PacketError("artifact ids must be ordered D1, D2, ...")
        bounded_text(
            artifact["label"], f"artifacts[{index - 1}].label", 240
        )
        bounded_text(
            artifact["source"], f"artifacts[{index - 1}].source", MAX_ITEM_CHARS
        )
        content = artifact["content"]
        if not isinstance(content, str) or not content:
            raise PacketError(f"artifacts[{index - 1}].content must not be empty")
        if (
            not isinstance(artifact["original_chars"], int)
            or not isinstance(artifact["included_chars"], int)
            or isinstance(artifact["original_chars"], bool)
            or isinstance(artifact["included_chars"], bool)
        ):
            raise PacketError("artifact character counts must be integers")
        if not isinstance(artifact["truncated"], bool):
            raise PacketError("artifact truncated must be a boolean")
        if artifact["included_chars"] != len(content):
            raise PacketError("artifact included_chars does not match content")
        if artifact["included_chars"] > MAX_ARTIFACT_CHARS:
            raise PacketError("artifact included content exceeds its bound")
        total_chars += artifact["included_chars"]
        digest = artifact["content_sha256"]
        if not isinstance(digest, str) or len(digest) != 64:
            raise PacketError("artifact content_sha256 must be a SHA-256 hex string")
        try:
            int(digest, 16)
        except ValueError as exc:
            raise PacketError("artifact content_sha256 must be hexadecimal") from exc
        if artifact["truncated"]:
            if artifact["original_chars"] <= artifact["included_chars"]:
                raise PacketError("truncated artifact must report omitted characters")
            if not content.endswith("...[ARTIFACT TRUNCATED]..."):
                raise PacketError("truncated artifact is missing its explicit marker")
            expected_truncations.append(
                {
                    "artifact_id": artifact["id"],
                    "original_chars": artifact["original_chars"],
                    "included_chars": artifact["included_chars"],
                }
            )
        else:
            if artifact["original_chars"] != artifact["included_chars"]:
                raise PacketError("complete artifact character counts must match")
            if digest != hashlib.sha256(content.encode("utf-8")).hexdigest():
                raise PacketError("artifact content hash does not match")
    if total_chars > MAX_ARTIFACT_TOTAL_CHARS:
        raise PacketError("artifact total content exceeds its bound")

    expected_meta = {
        "context_completeness": (
            "partial" if packet["limitations"] or expected_truncations else "complete"
        ),
        "artifact_count": len(packet["artifacts"]),
        "artifact_chars": total_chars,
        "artifact_truncations": expected_truncations,
    }
    if packet["packet_meta"] != expected_meta:
        raise PacketError("context packet metadata does not match its contents")
    _check_phase_minimums(packet)
    return packet


def load_input(path: str) -> dict[str, Any]:
    if path == "-":
        value = json.load(sys.stdin)
    else:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise PacketError("input must be a JSON object")
    return value


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", help="Input JSON file, or - for stdin")
    parser.add_argument("--output", help="Output JSON file; defaults to stdout")
    parser.add_argument("--phase", choices=sorted(PHASES))
    parser.add_argument("--context-mode", choices=sorted(CONTEXT_MODES))
    parser.add_argument("--task")
    parser.add_argument("--decision")
    parser.add_argument("--proposal")
    parser.add_argument("--constraint", action="append")
    parser.add_argument(
        "--evidence",
        action="append",
        help="Repeat SOURCE :: OBSERVED FACT",
    )
    parser.add_argument(
        "--attempt",
        action="append",
        help="Repeat ACTION :: EXACT RESULT",
    )
    parser.add_argument("--change", action="append")
    parser.add_argument(
        "--validation",
        action="append",
        help="Repeat CHECK :: EXACT RESULT",
    )
    parser.add_argument("--conflict", action="append")
    parser.add_argument("--limitation", action="append")
    parser.add_argument("--artifact", action="append", help="Sanitized UTF-8 text file")
    parser.add_argument("--allow-artifact-truncation", action="store_true")
    parser.add_argument("--self-test", action="store_true")
    return parser.parse_args()


def raw_from_args(args: argparse.Namespace) -> dict[str, Any]:
    return {
        "phase": args.phase,
        "context_mode": args.context_mode or "packet",
        "task": args.task,
        "decision": args.decision,
        "proposal": args.proposal,
        "constraints": args.constraint or [],
        "evidence": args.evidence or [],
        "attempts": args.attempt or [],
        "changes": args.change or [],
        "validation": args.validation or [],
        "conflicts": args.conflict or [],
        "limitations": args.limitation or [],
        "artifacts": [],
    }


def self_test() -> None:
    raw = {
        "phase": "stuck",
        "context_mode": "packet",
        "task": "Recover a failed remote-control connection.",
        "decision": "Choose the next bounded diagnostic action.",
        "constraints": ["Do not expose api_key=very-secret-value"],
        "evidence": [
            {
                "source": "command: codex remote-control --json",
                "fact": "The command exits 1 with connection errored.",
            },
            {
                "source": "process list",
                "fact": "The local app-server socket and process exist.",
            },
        ],
        "attempts": [
            {
                "action": "Restarted only the client.",
                "result": "The same connection error remained.",
            }
        ],
        "proposal": "Inspect the daemon-side enrollment state before changing plugins.",
        "changes": [],
        "validation": [],
        "conflicts": ["The local socket is healthy while cloud status is errored."],
        "limitations": [],
        "artifacts": [],
    }
    first = build_packet(raw)
    second = build_packet(raw)
    assert first == second
    assert first["schema_version"] == SCHEMA_VERSION
    assert first["evidence"][0]["id"] == "E1"
    assert "[REDACTED]" in first["constraints"][0]["text"]
    assert validate_packet(first) == first

    try:
        build_packet({**raw, "attempts": []})
    except PacketError as exc:
        assert "stuck phase" in str(exc)
    else:
        raise AssertionError("stuck packet without attempts was accepted")

    try:
        build_packet(
            {
                **raw,
                "evidence": ["A fact with no source delimiter"],
            }
        )
    except PacketError as exc:
        assert "SOURCE :: OBSERVED FACT" in str(exc)
    else:
        raise AssertionError("unanchored evidence was accepted")

    bundle = build_packet(
        {
            **raw,
            "context_mode": "bundle",
            "artifacts": [
                {
                    "label": "sanitized.log",
                    "source": "synthetic fixture",
                    "content": "16:00 service=running endpoint=unreachable",
                }
            ],
        }
    )
    assert bundle["artifacts"][0]["id"] == "D1"
    assert bundle["packet_meta"]["context_completeness"] == "complete"
    assert validate_packet(bundle) == bundle

    oversized_raw = {
        **raw,
        "context_mode": "bundle",
        "artifacts": [
            {
                "label": "oversized.log",
                "source": "synthetic fixture",
                "content": "x" * (MAX_ARTIFACT_CHARS + 1),
            }
        ],
    }
    try:
        build_packet(oversized_raw)
    except PacketError as exc:
        assert "exceeds" in str(exc)
    else:
        raise AssertionError("oversized artifact was silently truncated")
    truncated = build_packet(
        oversized_raw,
        allow_artifact_truncation=True,
    )
    assert truncated["artifacts"][0]["truncated"] is True
    assert truncated["packet_meta"]["context_completeness"] == "partial"
    assert validate_packet(truncated) == truncated

    print(
        json.dumps(
            {
                "self_test": "PASS",
                "schema_version": SCHEMA_VERSION,
                "phase_gates": "enforced",
                "silent_truncation": False,
            }
        )
    )


def main() -> None:
    args = parse_args()
    if args.self_test:
        self_test()
        return
    individual_fields = (
        args.phase,
        args.context_mode,
        args.task,
        args.decision,
        args.proposal,
        args.constraint,
        args.evidence,
        args.attempt,
        args.change,
        args.validation,
        args.conflict,
        args.limitation,
    )
    if args.input and any(value is not None for value in individual_fields):
        raise PacketError("--input cannot be combined with individual packet fields")
    raw = load_input(args.input) if args.input else raw_from_args(args)
    packet = build_packet(
        raw,
        artifact_paths=args.artifact,
        allow_artifact_truncation=args.allow_artifact_truncation,
    )
    rendered = json.dumps(packet, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        Path(args.output).write_text(rendered, encoding="utf-8")
    else:
        sys.stdout.write(rendered)


if __name__ == "__main__":
    try:
        main()
    except (PacketError, OSError, json.JSONDecodeError) as exc:
        print(f"BLOCKED: {exc}", file=sys.stderr)
        raise SystemExit(2)

#!/usr/bin/env python3
"""Prompt an Orca orchestrator to retrieve S3 lessons after repeated failures."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import sys
from typing import Any

from orca_context import is_orca_orchestrator

FAILURE_STATUSES = {"error", "failed", "failure", "rejected", "timeout", "timed_out"}
FAILURE_TEXT = re.compile(
    r"(?:^|\n)\s*(?:error|failed|fatal|traceback)\b|"
    r"\b(?:permission denied|command not found|no such file or directory|timed out)\b",
    re.IGNORECASE,
)
FAILURE_THRESHOLD = 2


def _read_payload() -> dict[str, Any]:
    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, OSError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _is_failure(value: object, *, top_level: bool = True) -> bool:
    if isinstance(value, dict):
        for key in ("is_error", "isError"):
            if value.get(key) is True:
                return True
        for key in ("success", "ok"):
            if value.get(key) is False:
                return True

        exit_code = value.get("exit_code", value.get("exitCode"))
        if isinstance(exit_code, int) and exit_code != 0:
            return True

        status = value.get("status")
        if isinstance(status, str) and status.lower() in FAILURE_STATUSES:
            return True

        error = value.get("error")
        if error not in (None, False, "", {}):
            return True

        return any(_is_failure(item, top_level=False) for item in value.values())

    if isinstance(value, list):
        return any(_is_failure(item, top_level=False) for item in value)

    return top_level and isinstance(value, str) and FAILURE_TEXT.search(value) is not None


def _state_path(payload: dict[str, Any]) -> Path | None:
    plugin_data = os.environ.get("PLUGIN_DATA") or os.environ.get("CLAUDE_PLUGIN_DATA")
    session_id = payload.get("session_id")
    turn_id = payload.get("turn_id")
    if (
        not plugin_data
        or not isinstance(session_id, str)
        or not session_id
        or not isinstance(turn_id, str)
        or not turn_id
    ):
        return None

    key = hashlib.sha256(f"{session_id}\0{turn_id}".encode("utf-8")).hexdigest()
    return Path(plugin_data) / "s3-stuck-retrieval" / f"{key}.json"


def _load_state(path: Path | None) -> dict[str, Any]:
    if path is None:
        return {"consecutive_failures": 0, "triggered": False}
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"consecutive_failures": 0, "triggered": False}
    return state if isinstance(state, dict) else {"consecutive_failures": 0, "triggered": False}


def _save_state(path: Path | None, state: dict[str, Any]) -> None:
    if path is None:
        return
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(f"{path.name}.{os.getpid()}.tmp")
        temporary.write_text(json.dumps(state, sort_keys=True), encoding="utf-8")
        temporary.replace(path)
    except OSError:
        # Retrieval advice must not make ordinary tool processing fail.
        pass


def _retrieval_context(tool_name: object, failure_count: int) -> str:
    tool_label = tool_name if isinstance(tool_name, str) else "unknown tool"
    return (
        f"Potential stuck state detected after {failure_count} consecutive tool failures; "
        f"the latest failing tool was `{tool_label}`. Before repeating the same approach, "
        "form a concise query from the task, component, exact failure symptom, and attempted "
        "approach, then search S3 Research Memory with "
        "`mcp__codex_apps__s3_research_memory_search_lessons` using `retrieval_mode=hybrid_v1`. "
        "Inspect the most relevant Lesson fields and apply a prior lesson only when its context, "
        "evidence, and applicability match. Treat it as evidence rather than authority. If S3 "
        "is unavailable or no relevant Lesson exists, record that negative result briefly and "
        "continue local diagnosis without retrying the same S3 query."
    )


def main() -> int:
    payload = _read_payload()
    tool_name = payload.get("tool_name")
    if (
        payload.get("hook_event_name") != "PostToolUse"
        or not is_orca_orchestrator(payload)
        or not isinstance(tool_name, str)
        or "s3_research_memory" in tool_name
    ):
        return 0

    state_path = _state_path(payload)
    state = _load_state(state_path)
    failed = _is_failure(payload.get("tool_response"))

    if failed:
        state["consecutive_failures"] = int(state.get("consecutive_failures", 0)) + 1
    else:
        state["consecutive_failures"] = 0

    failure_count = state["consecutive_failures"]
    should_trigger = failure_count >= FAILURE_THRESHOLD and state.get("triggered") is not True
    if should_trigger:
        state["triggered"] = True

    _save_state(state_path, state)

    if should_trigger:
        json.dump(
            {
                "hookSpecificOutput": {
                    "hookEventName": "PostToolUse",
                    "additionalContext": _retrieval_context(tool_name, failure_count),
                }
            },
            sys.stdout,
            ensure_ascii=False,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

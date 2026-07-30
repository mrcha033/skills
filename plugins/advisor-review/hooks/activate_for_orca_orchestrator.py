#!/usr/bin/env python3
"""Conditionally activate Advisor Review for an Orca orchestrator."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import sys
from typing import Any

from orca_context import is_orca_orchestrator


ACTIVATION_CONTEXT = (
    "You are the Orca orchestrator. Keep the coordinator on `gpt-5.6-sol` with "
    "`model_reasoning_effort=high`. Load and follow the `advisor-review` skill bundled "
    "by the Advisor Review plugin for this task. Treat this hook activation as an explicit "
    "invocation latch: complete its same-task advisor-style independent review and "
    "validated adopt/reject/defer decision before substantive action or a substantive "
    "final answer. When work stalls, the diagnosis remains uncertain, tool failures "
    "repeat, or a material pivot is being considered, proactively search S3 Research "
    "Memory for relevant prior lessons before repeating the same approach. Treat "
    "retrieved lessons as evidence to evaluate, not authority, and continue local "
    "diagnosis if retrieval is unavailable or irrelevant. For every fresh Orca Codex "
    "worker, create the worker terminal with `codex --model gpt-5.6-luna -c "
    "'model_reasoning_effort=\"max\"'`, wait for `tui-idle`, and then use Orca "
    "orchestration dispatch with `--inject`. Do not rely on inherited model settings, "
    "and do not switch this coordinator to Luna."
)


def _read_payload() -> dict[str, Any]:
    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, OSError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _marker_path(payload: dict[str, Any]) -> Path | None:
    plugin_data = os.environ.get("PLUGIN_DATA") or os.environ.get("CLAUDE_PLUGIN_DATA")
    session_id = payload.get("session_id")
    if not plugin_data or not isinstance(session_id, str) or not session_id:
        return None

    session_hash = hashlib.sha256(session_id.encode("utf-8")).hexdigest()
    return Path(plugin_data) / "orca-orchestrator-activation" / f"{session_hash}.activated"


def _already_activated(payload: dict[str, Any]) -> bool:
    marker = _marker_path(payload)
    return marker is not None and marker.exists()


def _record_activation(payload: dict[str, Any]) -> None:
    marker = _marker_path(payload)
    if marker is None:
        return

    try:
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.touch(exist_ok=True)
    except OSError:
        # Activation must not fail merely because optional deduplication is unavailable.
        pass


def main() -> int:
    payload = _read_payload()
    if not is_orca_orchestrator(payload):
        return 0

    event_name = payload.get("hook_event_name")
    if event_name not in {"SessionStart", "UserPromptSubmit"}:
        return 0

    # SessionStart must re-inject context after resume, clear, or compaction.
    # UserPromptSubmit establishes the Orca orchestrator role on the initial prompt.
    if event_name == "UserPromptSubmit" and _already_activated(payload):
        return 0

    _record_activation(payload)
    output = {
        "hookSpecificOutput": {
            "hookEventName": event_name,
            "additionalContext": ACTIVATION_CONTEXT,
        }
    }
    json.dump(output, sys.stdout, ensure_ascii=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

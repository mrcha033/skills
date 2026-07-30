#!/usr/bin/env python3
"""Request one S3 Research Memory checkpoint before an Orca orchestrator stops."""

from __future__ import annotations

import json
import sys
from typing import Any

from orca_context import is_orca_orchestrator


def _read_payload() -> dict[str, Any]:
    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, OSError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _checkpoint_prompt(payload: dict[str, Any]) -> str:
    session_id = payload.get("session_id", "unknown-session")
    turn_id = payload.get("turn_id", "unknown-turn")
    return f"""Before ending this Orca orchestrator turn, perform one S3 Research Memory checkpoint.

Scope: session `{session_id}`, turn `{turn_id}`.

Review the work completed in this turn and the relevant session context. A memory is eligible only
when it states a reusable lesson that applies beyond this single run and is supported by evidence
actually inspected in the session. Eligible examples include a failed approach whose causal reason
and applicability conditions were established, a verified discovery, or a reusable constraint.

Do not store ordinary task status, a raw transcript, raw command output, transient tool/auth/network
failures, retry bookkeeping, unverified guesses, secrets, personal data, or a merely embarrassing
mistake without a generalizable cause. Do not save anything just to prove this checkpoint ran.

If no eligible lesson exists, make no S3 write and finish normally. If one exists:

1. Search `s3-research-memory` for a duplicate or closely related Lesson, using hybrid retrieval
   when available.
2. Prefer improving or relating an existing Lesson only when the tool's revision and evidence
   contracts can be satisfied. Otherwise create a new draft with
   `mcp__codex_apps__s3_research_memory_create_lesson_draft`.
3. Include structured initial evidence tied to a source, test, benchmark, or sanitized log that was
   actually examined. Calibrate confidence to that evidence.
4. Use an idempotent operation id beginning `orca-stop:{session_id}:{turn_id}:`.
5. Never claim that a Lesson was saved unless the S3 tool call succeeds.

After saving or deciding that nothing qualifies, complete the turn without another memory checkpoint."""


def main() -> int:
    payload = _read_payload()
    should_checkpoint = (
        payload.get("hook_event_name") == "Stop"
        and is_orca_orchestrator(payload)
        and payload.get("stop_hook_active") is not True
    )

    if not should_checkpoint:
        json.dump({}, sys.stdout)
        return 0

    json.dump(
        {
            "decision": "block",
            "reason": _checkpoint_prompt(payload),
        },
        sys.stdout,
        ensure_ascii=False,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

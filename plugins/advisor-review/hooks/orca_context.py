"""Detect an Orca-managed orchestrator session without matching its workers."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
import re
from typing import Any


ORCHESTRATOR_MENTION = re.compile(r"(?<!\w)@orchestrat(?:or|ion)\b", re.IGNORECASE)
ORCHESTRATOR_ROLE_VALUES = {"orchestrator", "orchestration", "coordinator"}


def _plugin_data() -> str | None:
    return os.environ.get("PLUGIN_DATA") or os.environ.get("CLAUDE_PLUGIN_DATA")


def _is_orca_runtime() -> bool:
    return bool(os.environ.get("ORCA_PANE_KEY")) and bool(
        os.environ.get("ORCA_AGENT_HOOK_PORT")
        or os.environ.get("ORCA_AGENT_HOOK_ENDPOINT")
    )


def _explicit_orca_role() -> bool:
    for name in ("ADVISOR_REVIEW_ORCA_ROLE", "ORCA_AGENT_ROLE"):
        value = os.environ.get(name, "").strip().lower()
        if value in ORCHESTRATOR_ROLE_VALUES:
            return True
    return False


def _session_marker(payload: dict[str, Any]) -> Path | None:
    plugin_data = _plugin_data()
    session_id = payload.get("session_id")
    if not plugin_data or not isinstance(session_id, str) or not session_id:
        return None
    session_hash = hashlib.sha256(session_id.encode("utf-8")).hexdigest()
    return Path(plugin_data) / "orca-orchestrator" / f"{session_hash}.role"


def _prompt_selects_orchestrator(payload: dict[str, Any]) -> bool:
    prompt = payload.get("prompt")
    return isinstance(prompt, str) and ORCHESTRATOR_MENTION.search(prompt) is not None


def is_orca_orchestrator(payload: dict[str, Any]) -> bool:
    """Return true only after Orca runtime and orchestrator role are established."""
    if not _is_orca_runtime():
        return False

    marker = _session_marker(payload)
    selected = _explicit_orca_role() or _prompt_selects_orchestrator(payload)
    if selected and marker is not None:
        try:
            marker.parent.mkdir(parents=True, exist_ok=True)
            marker.touch(exist_ok=True)
        except OSError:
            pass

    return selected or (marker is not None and marker.exists())

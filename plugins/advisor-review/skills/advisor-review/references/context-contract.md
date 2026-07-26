# Context Packet Contract

Every review uses one `advisor-context-2.0` packet. The packet is an evidence boundary, not a prose summary of the parent agent's intuition.

## Required shape

```json
{
  "schema_version": "advisor-context-2.0",
  "phase": "stuck",
  "context_mode": "packet",
  "task": "The complete user-visible outcome",
  "decision": "The exact decision the advisor must challenge",
  "constraints": [
    {"id": "C1", "text": "A user, safety, repository, or scope constraint"}
  ],
  "evidence": [
    {"id": "E1", "source": "command/file/session anchor", "fact": "Observed fact"}
  ],
  "attempts": [
    {"id": "A1", "action": "Action already taken", "result": "Exact result"}
  ],
  "proposal": "Current diagnosis, plan, pivot, or completion claim",
  "changes": [
    {"id": "CH1", "text": "Material change already made"}
  ],
  "validation": [
    {"id": "V1", "check": "Exact check", "result": "Exact outcome"}
  ],
  "conflicts": [
    {"id": "K1", "text": "Observed conflict or unresolved hypothesis"}
  ],
  "limitations": [
    {"id": "L1", "text": "Context that could not be obtained or verified"}
  ],
  "artifacts": [],
  "packet_meta": {
    "context_completeness": "complete",
    "artifact_count": 0,
    "artifact_chars": 0,
    "artifact_truncations": []
  },
  "context_hash": "sha256 of every preceding field"
}
```

Use `scripts/build_context_packet.py`; do not hand-calculate IDs or hashes.

## Source and chronology rules

- `evidence` contains only observed facts and an exact source. CLI form is `SOURCE :: OBSERVED FACT`.
- `attempts` preserves order and uses `ACTION :: EXACT RESULT`.
- `validation` uses `CHECK :: EXACT RESULT`.
- Put hypotheses in `proposal` or `conflicts`, never in `evidence`.
- Include failures, no-change results, and user corrections. They often discriminate better than successes.
- Identify task/process boundaries. A receipt in another top-level task is not evidence that the parent consumed it.
- Prefer a file plus line, command plus timestamp, commit SHA, test name, authoritative URL, or session plus record number over an unattributed narrative.

## Context modes

### `packet`

Use when the source-anchored fields preserve every decision-critical fact. Artifacts are rejected in this mode.

### `bundle`

Use when a bounded sanitized artifact materially changes the diagnosis: a log window, event timeline, diff, configuration excerpt, or test output. Pass each UTF-8 file with `--artifact`.

Artifacts are embedded into the prompt and limited to:

- 8 artifacts;
- 12,000 characters per artifact;
- 64,000 included characters total.

Oversized artifacts fail closed by default. `--allow-artifact-truncation` records exact truncation metadata and marks context partial; use it only when the omitted portion cannot change the decision.

## Phase gates

- Every phase: task, decision, proposal, at least one constraint, and at least one source-anchored evidence item.
- `plan`: known dependency evidence and the proposed approach.
- `stuck`: at least one attempted action/result and one unresolved conflict.
- `pivot`: evidence from the current approach plus switching-cost or rollback uncertainty.
- `final`: at least one material change and one exact validation result.

The builder rejects a packet that misses a gate. Do not bypass it by changing the phase.

## Confidentiality and completeness

The builder performs best-effort token/secret redaction, but the parent must sanitize input first. Never attach credentials, cookies, private keys, authorization headers, or unrestricted session dumps.

If required evidence cannot be safely included, add a `limitations` entry. The packet then records `context_completeness: partial`; the final report must disclose that limitation.

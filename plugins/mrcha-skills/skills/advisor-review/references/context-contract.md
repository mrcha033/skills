# Context Packet Contract

Build one bounded JSON packet for every advisor call. The packet makes the review target explicit and is mandatory in both context modes.

## Required shape

```json
{
  "schema_version": "advisor-context-1.0",
  "phase": "plan",
  "task": "What outcome the primary agent must achieve",
  "constraints": ["Applicable user, system, repository, and safety constraints"],
  "evidence": ["Verified observations with source or command context"],
  "proposal": "The decision, plan, diagnosis, or completion claim under review",
  "changes": ["Material changes already made; empty before implementation"],
  "validation": ["Checks run and exact outcomes"],
  "conflicts": ["Evidence conflicts or unresolved uncertainty"],
  "context_hash": "sha256 of the canonical packet without this field"
}
```

Use `scripts/build_context_packet.py` to normalize, redact, bound, and hash the packet. It accepts either an input JSON object or individual command-line fields.

## Evidence rules

- Put only observed facts in `evidence` and `validation`.
- Label estimates, hypotheses, and memory-derived statements in `conflicts` or `proposal`.
- Include exact failures and negative results when they affect the decision.
- Prefer durable references such as file paths, commit IDs, test names, and URLs over narrative summaries.
- Never include credentials, session cookies, private keys, authorization headers, or secret environment values.
- Keep each list focused on facts that can change the verdict.

The builder performs best-effort redaction, but that is a backstop rather than permission to pass secrets into the packet.

## Context modes

### Full-context mode

Fork the complete available conversation when the host supports it. Some Codex hosts require full-history forks to inherit the primary model; honor that constraint and omit model overrides.

This is the closest available approximation to automatic transcript transfer, but it remains a separate subagent task rather than a native in-request advisor call.

### Strong-reviewer mode

Use a stronger reviewer only when the host explicitly exposes one. Because model overrides may require a fresh or bounded-history fork, include the complete packet and enough recent turns to preserve immediate intent.

Do not silently trade away constraints for model strength. If the packet cannot preserve the decisive context, use full-context mode.

## Phase-specific minimums

- `plan`: task, constraints, proposal, known evidence.
- `stuck`: attempted approaches, exact failures, current diagnosis, evidence conflicts.
- `pivot`: current approach, candidate approach, switching cost, rollback path.
- `final`: material changes, validation results, remaining gaps, proposed completion claim.

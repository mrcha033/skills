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

## Isolated execution

Always pass the packet to `scripts/run_advisor.py`. The runner launches a separate `codex exec` process with:

- an explicitly selected reviewer model;
- an ephemeral session;
- a blank temporary working directory;
- a read-only filesystem sandbox;
- user configuration ignored;
- recursive multi-agent disabled;
- a required JSON output schema.

The runner reuses existing Codex CLI authentication and requires no MCP server or separately configured API key. It receives only this packet and the bundled rubric, so include every fact necessary for the decision. Do not substitute a native subagent because that would create a second execution contract with different context inheritance and isolation semantics.

The skill is unavailable when the host cannot execute local commands or the Codex CLI is absent or unauthenticated.

## Phase-specific minimums

- `plan`: task, constraints, proposal, known evidence.
- `stuck`: attempted approaches, exact failures, current diagnosis, evidence conflicts.
- `pivot`: current approach, candidate approach, switching cost, rollback path.
- `final`: material changes, validation results, remaining gaps, proposed completion claim.

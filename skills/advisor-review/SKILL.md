---
name: advisor-review
description: Consult an independent, read-only reviewer at consequential decision points in multi-step Codex work. Use when the user explicitly asks for an advisor, second opinion, adversarial review, or invokes this skill; when task instructions require independent review; after repeated failures; before a major approach change; or before declaring complex work complete. This skill approximates an advisor with a Codex subagent and does not reproduce Claude's native server-side Advisor tool.
---

# Advisor Review

Ask a separate Codex reviewer to challenge the current approach without changing files or external state. Keep the primary agent responsible for evidence gathering, implementation, and the final decision.

## Preconditions

Use the host's subagent or delegation primitive only when it is available and permitted by higher-priority instructions. In Codex environments that expose it, use `spawn_agent`.

If no independent subagent primitive is available, report that the advisor review is unavailable. Do not present same-agent reflection as independent advice.

Treat the reviewer's read-only status as a behavioral boundary, not a technical sandbox: explicitly prohibit tool calls, file edits, messages, commits, and other mutations in the reviewer prompt.

## Review workflow

1. Select exactly one review phase:
   - `plan`: challenge the proposed approach before implementation.
   - `stuck`: diagnose repeated failure or lack of progress.
   - `pivot`: compare the current approach with a materially different one.
   - `final`: audit completed changes and validation before declaring success.
2. Read `references/context-contract.md` and build a bounded context packet with `scripts/build_context_packet.py`.
3. Read the phase-specific questions and result contract in `references/review-rubric.md`.
4. Choose a context mode:
   - Prefer **full-context mode** when preserving the complete task history matters most. Fork all available turns and do not request a different model if the host requires full-history forks to inherit the parent model.
   - Use **strong-reviewer mode** only when the host explicitly exposes a stronger reviewer. Send a bounded recent-turn fork or a fresh fork plus the complete context packet; do not claim it has unseen history.
5. Spawn one reviewer with a concrete, bounded task. Require it to:
   - remain read-only and call no tools;
   - distinguish verified evidence from inference;
   - answer the rubric questions for the selected phase;
   - return only the JSON object defined in `references/review-rubric.md`.
6. Validate the returned JSON with `scripts/validate_advice.py`. If invalid, request one formatting-only correction from the same reviewer or report the malformed response.
7. Compare the advice with primary evidence. Adopt, reject, or defer each consequential recommendation and state why. The advisor does not overrule verified facts or user instructions.
8. Continue the task. For a `final` review, do not declare completion until the relevant recommendations are resolved or explicitly documented as non-blocking.

## Reviewer prompt

Include the following content, adapted to the task:

```text
You are an independent advisor, not an implementer.
Review phase: <plan|stuck|pivot|final>

Read-only boundary:
- Do not call tools.
- Do not edit files, send messages, commit, push, or change external state.
- Do not invent evidence. Mark every inference as an inference.

Use the supplied context packet and the phase rubric.
Return only one JSON object matching the advice schema.

CONTEXT PACKET
<packet JSON>
```

In full-context mode, still include the packet. It identifies the exact evidence and decision under review instead of relying on the reviewer to infer scope from the transcript.

## Call budget

Use one advisor call by default. Use a second call only when new evidence materially changes the decision or an independent check is justified by risk. Use a third call only to reconcile a concrete conflict between the first two reviews. Never create an open-ended advisor loop.

Do not delegate the primary implementation to this reviewer. A separate implementation subagent, if authorized, is outside this skill's advisor call budget.

## Reporting

Report:

- the phase and context mode;
- the advisor verdict;
- which recommendations were adopted, rejected, or deferred;
- evidence supporting the primary agent's final decision;
- any unresolved risk.

Call the result an **advisor-style independent review**, not Claude Advisor parity. Claude's native Advisor can receive server-managed conversation context inside one request; this skill approximates that behavior with Codex subagent orchestration.

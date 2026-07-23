---
name: advisor-review
description: Consult an independent, read-only reviewer at consequential decision points in multi-step Codex work. Use when the user explicitly asks for an advisor, second opinion, adversarial review, or invokes this skill; when task instructions require independent review; after repeated failures; before a major approach change; or before declaring complex work complete. Uses a native Codex subagent when available and an isolated Codex CLI subprocess fallback otherwise; it does not reproduce Claude's native server-side Advisor tool.
---

# Advisor Review

Ask a separate Codex reviewer to challenge the current approach without changing files or external state. Keep the primary agent responsible for evidence gathering, implementation, and the final decision.

## Backend selection

Select the first permitted backend:

1. **Native subagent:** use the host's subagent or delegation primitive. In Codex environments that expose it, use `spawn_agent`.
2. **Codex CLI subprocess:** when the native primitive is absent but shell execution and an authenticated `codex` CLI are available, run `scripts/run_advisor.py`. This starts a separate ephemeral Codex process in a blank temporary directory with user config ignored, recursive multi-agent disabled, and a read-only sandbox.
3. **Unavailable:** if neither backend is available, report that independent advisor review is unavailable. Do not present same-agent reflection as independent advice.

For native subagents, treat read-only status as a behavioral boundary unless the host provides a read-only agent profile. For the CLI backend, the filesystem is read-only and the prompt also prohibits all tool calls. Neither backend is a confidentiality boundary; remove secrets from the context packet.

## Review workflow

1. Select exactly one review phase:
   - `plan`: challenge the proposed approach before implementation.
   - `stuck`: diagnose repeated failure or lack of progress.
   - `pivot`: compare the current approach with a materially different one.
   - `final`: audit completed changes and validation before declaring success.
2. Read `references/context-contract.md` and build a bounded context packet with `scripts/build_context_packet.py`.
3. Read the phase-specific questions and result contract in `references/review-rubric.md`.
4. Choose a backend and context mode:
   - Prefer **full-context mode** when preserving the complete task history matters most. Fork all available turns and do not request a different model if the host requires full-history forks to inherit the parent model.
   - Use **strong-reviewer mode** only when the host explicitly exposes a stronger reviewer. Send a bounded recent-turn fork or a fresh fork plus the complete context packet; do not claim it has unseen history.
   - Use **CLI-isolated mode** when the native subagent primitive is unavailable. This mode receives only the context packet, never the parent transcript.
5. Start one reviewer with a concrete, bounded task. Require it to:
   - remain read-only and call no tools;
   - distinguish verified evidence from inference;
   - answer the rubric questions for the selected phase;
   - return only the JSON object defined in `references/review-rubric.md`.
6. For the CLI backend, run:

   ```bash
   python3 scripts/run_advisor.py \
     --input context-packet.json \
     --reviewer-model gpt-5.6-sol
   ```

   The runner supplies the rubric, enforces the JSON schema, and validates the result. Set `ADVISOR_REVIEW_MODEL` or pass `--reviewer-model` to select another installed model.
7. For a native reviewer, validate the returned JSON with `scripts/validate_advice.py`. If invalid, request one formatting-only correction from the same reviewer or report the malformed response.
8. Compare the advice with primary evidence. Adopt, reject, or defer each consequential recommendation and state why. The advisor does not overrule verified facts or user instructions.
9. Continue the task. For a `final` review, do not declare completion until the relevant recommendations are resolved or explicitly documented as non-blocking.

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

In full-context mode, still include the packet. It identifies the exact evidence and decision under review instead of relying on the reviewer to infer scope from the transcript. Do not manually construct this prompt for CLI-isolated mode; `run_advisor.py` constructs it.

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

Call the result an **advisor-style independent review**, not Claude Advisor parity. Claude's native Advisor can receive server-managed conversation context inside one request; this skill approximates that behavior with either Codex subagent orchestration or a separate Codex CLI process.

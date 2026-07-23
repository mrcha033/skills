---
name: advisor-review
description: Consult an independent, read-only reviewer at consequential decision points in multi-step Codex work. Use when the user explicitly asks for an advisor, second opinion, adversarial review, or invokes this skill; when task instructions require independent review; after repeated failures; before a major approach change; or before declaring complex work complete. Always runs the bundled script to start a separate, isolated Codex CLI process; it does not use native subagents or reproduce Claude's server-side Advisor tool.
---

# Advisor Review

Run every review through `scripts/run_advisor.py`. Keep the primary agent responsible for evidence gathering, implementation, and the final decision.

## Requirements

Require shell execution, an authenticated local `codex` CLI, and access to the selected reviewer model. If any requirement is missing, report that independent review is unavailable.

Do not replace the runner with `spawn_agent` or same-agent reflection. A single script backend keeps context, isolation, model selection, output validation, timeout, and failure behavior consistent across parent models.

The runner is not a confidentiality boundary. Remove secrets before constructing the context packet.

## Review workflow

1. Select exactly one review phase:
   - `plan`: challenge the proposed approach before implementation.
   - `stuck`: diagnose repeated failure or lack of progress.
   - `pivot`: compare the current approach with a materially different one.
   - `final`: audit completed changes and validation before declaring success.
2. Read `references/context-contract.md`.
3. Build a bounded context packet with `scripts/build_context_packet.py`. Include every decision-critical requirement, constraint, observation, failure, validation result, and unresolved conflict.
4. Read the phase questions and result contract in `references/review-rubric.md`.
5. Run:

   ```bash
   python3 scripts/run_advisor.py \
     --input context-packet.json \
     --reviewer-model gpt-5.6-sol
   ```

   Set `ADVISOR_REVIEW_MODEL` or pass `--reviewer-model` to choose another installed model.
6. Treat a runner error or timeout as a blocked review. Do not reconstruct or repair the advice manually.
7. Compare the validated advice with primary evidence. Adopt, reject, or defer each consequential recommendation and state why. The advisor does not overrule verified facts or user instructions.
8. For a `final` review, do not declare completion until relevant recommendations are resolved or explicitly documented as non-blocking.

## Execution contract

The runner:

- starts a separate `codex exec` process;
- uses a blank temporary working directory;
- selects the reviewer model explicitly;
- runs an ephemeral session with user configuration ignored;
- disables recursive multi-agent execution;
- applies a read-only filesystem sandbox;
- supplies only the bounded context packet and bundled rubric;
- enforces the advice JSON Schema;
- validates the final response before returning it.

The isolated reviewer does not receive the parent transcript automatically. If the packet cannot preserve all decision-critical context, report the review as insufficient instead of claiming full-context coverage.

## Call budget

Use one review by default. Use a second only when new evidence materially changes the decision or an independent re-check is justified by risk. Use a third only to reconcile a concrete conflict between the first two reviews. Never create an open-ended advisor loop.

## Reporting

Report:

- the phase and reviewer model;
- the advisor verdict;
- which recommendations were adopted, rejected, or deferred;
- evidence supporting the primary agent's final decision;
- any unresolved risk or context limitation.

Call the result an **advisor-style independent review**, not Claude Advisor parity.

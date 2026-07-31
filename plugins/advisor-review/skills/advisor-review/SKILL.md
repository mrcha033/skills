---
name: advisor-review
description: Run a runtime-selected advisor-style review in the current task when the user invokes $advisor-review, says Advisor Review or @Advisor-Review, explicitly asks for an advisor/second opinion/adversarial review, or task instructions explicitly require independent review. Uses source-anchored evidence, bounded artifacts, and an adopt/reject/defer decision gate; do not substitute tool discovery, another task, or same-agent reflection.
---

# Advisor Review

Use the bundled scripts from this skill directory. An explicit invocation is a latch: run the reviewer in the **same parent task** before a substantive final answer or an action that follows or conflicts with the review. Read-only evidence collection may continue while constructing the packet.

Do not search the global tool catalog for an advisor. Do not open a second user-visible task. The only child execution is the isolated runtime CLI process started by `scripts/run_advisor.py`.

## Requirements

Require shell execution, an authenticated CLI for the active session runtime, access to the caller-selected advisor model, and outbound access for that runtime from the parent shell. Supported runtimes are `codex`, `claude`, `opencode`, and `gemini`; `--runtime auto` uses the active session marker such as `OPENCODE=1`, and refuses an ambiguous host instead of guessing. A parent sandbox that blocks subprocess networking also blocks the nested reviewer. If any requirement is missing, report that the independent review is blocked; do not weaken the sandbox without user or task authority.

The runner is not a confidentiality boundary. Sanitize evidence and artifacts before building the packet.

## Workflow

1. Announce that Advisor Review is being used and why.
2. Select one phase:
   - `plan`: challenge a proposed approach.
   - `stuck`: diagnose failed or looping work.
   - `pivot`: compare a materially different approach.
   - `final`: audit completed work and its endpoint evidence.
3. Read `references/context-contract.md`.
4. Choose the context mode:
   - `packet`: short source-anchored facts are sufficient.
   - `bundle`: sanitized logs, timelines, diffs, or other bounded text artifacts materially affect the diagnosis.
5. Build `advisor-context-2.0` with `scripts/build_context_packet.py`.
   - For `stuck`, include actions and exact results in chronological order.
   - Give every observed fact a concrete source.
   - Include negative results, conflicting state, and context limitations.
   - Never silently truncate decision-critical text. A builder failure is a blocked review.
6. Read `references/review-rubric.md`.
7. Select the advisor model and effort as the calling agent. Use a model available to the selected runtime, honor an explicit model or effort request, and otherwise choose based on the review phase and risk:
   1. Use `max` only for an explicitly requested deepest review or a critical, hard-to-reverse decision with major unresolved ambiguity.
   2. Use `xhigh` for cross-component failures, conflicting verified evidence, two or more failed approaches, major pivots, or high-impact completion claims.
   3. Otherwise use `high`.
8. Run exactly one isolated review by default and save its receipt. Pass the active session runtime, selected model, and selected effort explicitly so the receipt records the caller's request:

   ```bash
    python3 scripts/run_advisor.py \
      --input context-packet.json \
      --runtime auto \
      --model <caller-selected-model> \
      --effort xhigh \
      --output advisor-receipt.json
   ```

   Keep the returned receipt in the invoking task. A receipt produced in another user-visible task is not a handoff.

9. Check the receipt:
   - `request.runtime` must be the active session runtime or the explicitly selected runtime.
   - `request.requested_model` must equal the model selected by the calling agent.
   - `request.requested_effort` must match the selected effort.
   - `observed_model` and `observed_effort` may be `null`; runtime output does not authoritatively expose serving-side identity. Never describe requested identity as verified actual identity.
   - Any runner error, schema failure, timeout, or context-hash mismatch blocks the review.
   - The isolated child uses read-only or plan-mode controls, disables session persistence where supported, and disables plugin/skill loading where the runtime exposes that control. The Codex adapter uses a temporary `CODEX_HOME` with only the host authentication file linked when present; OpenCode runs with `--pure` and external skill/config loading disabled.
10. Read `references/decision-contract.md`. Resolve every `R#` recommendation as `adopt`, `reject`, or `defer`, then run:

    ```bash
    python3 scripts/validate_decision.py \
      --receipt advisor-receipt.json \
      --decision advisor-decision.json
    ```

11. Do not act on advisor recommendations until the decision record validates. A destructive recommendation additionally requires confirmed user authority.
12. Continue the parent task using the validated next action and stop condition.

## Stuck-work rules

- Diagnose before changing more state.
- Distinguish the execution boundary: parent task, isolated reviewer process, and any other user-visible task are separate.
- Do not let a separate task's receipt stand in for a same-task handoff.
- Prefer one read-only or reversible experiment that separates competing hypotheses.
- After a repeated user correction, do not repeat the same prescription without new evidence.
- Do not broaden scope from one failing subsystem to plugins, services, repositories, or packages unless evidence connects them.

## Review budget

Use one review by default. A second is allowed only when new evidence materially changes the decision or a high-risk final re-check is justified. A third is allowed only to reconcile a concrete conflict. Never create an open-ended review loop.

## Skill-change behavior gate

When modifying Advisor Review itself, run the deterministic tests and at least one sanitized behavior fixture:

```bash
python3 scripts/evaluate_advisor_behavior.py \
  --fixture behavior-fixture.json \
  --receipt advisor-receipt.json
```

Use paired old/new fixtures where practical. Judge detection, evidence linkage, bounded experiments, and risky-action rate—not prose similarity.

## Reporting

Report:

- phase, context mode, selected runtime, requested advisor model, requested effort, and identity verification status;
- advisor verdict and evidence-linked diagnosis;
- each consequential recommendation's adopt/reject/defer disposition;
- the selected next action and stop condition;
- unresolved risks or context limitations.

Call the result an **advisor-style independent review**, not parity with any one runtime's native advisor feature.

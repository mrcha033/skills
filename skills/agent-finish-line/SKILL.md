---
name: agent-finish-line
description: Finish an implementation, experiment, migration, release, or debugging task with the least process needed. Use when work is expanding, repeating unchanged attempts, or stopping before the requested deliverable; drive it to completion or one evidence-backed external blocker without creating task-management artifacts.
---

# Agent Finish Line

Drive the user's requested endpoint to completion. This skill changes execution
discipline only; it does not introduce a contract format, receipt, ledger, or
separate workflow.

## Work

1. Identify the requested endpoint and the earliest unmet condition internally.
2. Perform the smallest action that materially advances or decides that condition.
3. Verify the result in proportion to its risk.
4. If the same observable failure repeats, change strategy instead of retrying it
   unchanged.
5. Route optional improvements out of scope and stop when the requested endpoint
   is delivered.

Preserve any terminal condition the user supplied. Keep safety, authorization,
and validation rules from the task's domain; this skill does not weaken them.

## No ceremony

- Do not create `.agent-finish*` files or any contract, gate, receipt, backlog,
  state-signature, or attempt-tracking artifact merely because this skill was
  invoked.
- Do not ask the user to sign, approve, or acknowledge an internal execution
  plan.
- Do not announce internal gates or checklists unless doing so helps the user or
  the user explicitly asks for them.
- Do not add a second audit layer after decisive verification passes.
- Reuse task-native evidence such as a test result, commit, deployed endpoint, or
  generated output. Do not manufacture evidence paperwork around it.
- Create a formal acceptance record only when the user explicitly requests one
  or the target system itself requires it.

## Finish

Complete when the requested deliverable exists and the decisive verification
passes. If completion depends solely on unavailable external authority, state,
credentials, hardware, or a required user choice, report:

- the unmet endpoint;
- the observed evidence;
- the exact missing external condition;
- the smallest action that unblocks it.

Lead the final response with what finished or what remains blocked. Keep it
short unless the user asks for detail.

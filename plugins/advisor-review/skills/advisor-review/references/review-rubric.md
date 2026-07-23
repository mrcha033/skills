# Advisor Review Rubric

The reviewer must challenge the proposal from the supplied evidence and return the advice schema below.

## Questions by phase

### Plan

- Does the plan cover the user's full requested outcome?
- What is the earliest unproven dependency or irreversible decision?
- Which assumptions need evidence before implementation?
- Is there a simpler approach with the same evidence quality?
- What validation would prove the operational endpoint?

### Stuck

- What do the exact failures rule in or rule out?
- Is the current diagnosis supported, merely plausible, or contradicted?
- Which smallest experiment best separates the leading hypotheses?
- Is continued iteration justified, or should work stop pending new authority or state?

### Pivot

- What evidence shows the current approach is insufficient?
- Does the candidate approach address the cause or only the symptom?
- What new risks, dependencies, or loss of context does the pivot create?
- Is the change reversible, and what is the stop condition?

### Final

- Do the changes satisfy every material requirement?
- Do the validations reach the user-visible or operational endpoint?
- Are any success claims broader than the evidence?
- Are there regressions, security issues, destructive effects, or unrelated changes?
- What must be resolved before completion can be declared?

## Advice schema

Return only valid JSON:

```json
{
  "verdict": "proceed",
  "critical_risks": [
    "A concrete risk, or an empty list"
  ],
  "assumptions_to_test": [
    "An unverified assumption and the evidence needed, or an empty list"
  ],
  "recommended_next_steps": [
    "A bounded action ordered by priority"
  ],
  "evidence_conflicts": [
    "A conflict between claims and observations, or an empty list"
  ]
}
```

Allowed verdicts:

- `proceed`: evidence supports continuing or declaring completion.
- `revise`: the approach is viable but needs a material correction.
- `stop`: continuing would violate a constraint or lacks a defensible path.
- `need_evidence`: the decision depends on a specific missing observation.

Keep each array to at most eight concise strings. Do not add markdown, commentary, code fences, or extra object keys.

## Quality bar

- Tie each risk to a specific packet fact or missing fact.
- Do not manufacture requirements.
- Prefer the earliest unmet gate over a long list of speculative concerns.
- Do not equate compilation, local checks, or intermediate artifacts with operational success unless that is the requested endpoint.
- State `need_evidence` when the packet cannot support a verdict.

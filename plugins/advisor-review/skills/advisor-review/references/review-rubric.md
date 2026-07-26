# Advisor Review Rubric

The reviewer challenges one decision using only the supplied IDs and returns `advisor-advice-2.0`.

## Universal review lenses

1. **Evidence boundary** — Which claims are facts, inferences, conflicts, or gaps?
2. **Execution boundary** — Did the relevant parent task actually invoke and consume the reviewer, validator, test, or external result?
3. **Root cause** — What do failed attempts rule in or rule out?
4. **Discriminating experiment** — What smallest read-only or reversible action separates the leading hypotheses?
5. **Scope and safety** — Does a proposed action touch unrelated components or require authority?
6. **Endpoint** — Does validation reach the user's operational outcome?

Use these as lenses, not as separate agent roles.

## Phase questions

### Plan

- Does the proposal cover the full requested outcome?
- What is the earliest unproven dependency?
- Which assumption must be tested before an expensive or hard-to-reverse change?
- Is there a simpler path with equivalent evidence quality?

### Stuck

- Which prior action/result pairs repeat the same hypothesis?
- What evidence contradicts the current diagnosis?
- Is the failure in the target system, evidence collection, advisor activation, reviewer reasoning, or parent follow-through?
- Which one bounded experiment has distinct success and failure signals?
- What must not be changed until causality is shown?

### Pivot

- Does the candidate address a cause or merely bypass a symptom?
- What state, context, or rollback ability is lost?
- What evidence would make staying preferable?
- What is the stop condition?

### Final

- Does every material requirement have endpoint evidence?
- Are any claims broader than the cited checks?
- Are unrelated changes, regressions, destructive effects, or hidden context limitations unresolved?
- What blocks completion?

## Output contract

Return only one JSON object with:

- `verdict`: `proceed`, `revise`, `stop`, or `need_evidence`;
- `diagnosis`: a concise summary, confidence, and context IDs;
- `findings`: ordered `F#` fact/inference/conflict/gap objects with evidence IDs and impact;
- `experiments`: ordered `T#` experiments with distinct signals, stop condition, and risk;
- `recommendations`: ordered `R#` actions with rationale, context IDs, and risk;
- `missing_evidence`: specific observations still needed;
- `do_not_do`: tempting actions unsupported by current evidence.

Risk is exactly one of `read_only`, `reversible`, or `destructive`.

## Quality gates

- Every diagnosis, finding, and recommendation cites valid context IDs.
- A `need_evidence` verdict includes both specific missing evidence and at least one bounded experiment.
- A `revise` or `stop` verdict includes at least one recommendation.
- Do not emit generic advice such as “be careful,” “investigate further,” or “add tests” without a target, evidence link, signal, and stop condition.
- Do not manufacture requirements or treat requested model identity as observed identity.
- Prefer the earliest unmet gate over an exhaustive speculative list.
- Recommend no destructive action when a read-only or reversible discriminator exists.

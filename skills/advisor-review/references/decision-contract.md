# Parent Decision Contract

Advisor output does not act on its own. The parent agent must resolve every `R#` recommendation before finalizing the reviewed decision or taking an action that implements or conflicts with the advice. Unrelated read-only evidence collection may continue.

## Required shape

```json
{
  "schema_version": "advisor-decision-2.0",
  "receipt_context_hash": "copied from advisor-receipt.json",
  "decisions": [
    {
      "recommendation_id": "R1",
      "disposition": "adopt",
      "reason": "Why this disposition follows from the cited evidence",
      "evidence_refs": ["E1", "A2"],
      "authorization": "not_required"
    }
  ],
  "next_action": "One bounded next action",
  "stop_condition": "The exact signal that stops or redirects work"
}
```

Allowed dispositions:

- `adopt`: use the recommendation in the parent plan.
- `reject`: evidence or user instructions contradict it.
- `defer`: a named evidence gap or authority boundary prevents a decision.

Allowed authorization states:

- `not_required`: the adopted action is already within scope and is not destructive.
- `confirmed`: the user explicitly authorized the adopted destructive action.
- `missing`: authority is absent; the recommendation cannot be adopted.

## Rules

- Handle every recommendation exactly once.
- Cite only evidence IDs used by that recommendation.
- Explain dispositions with evidence; agreement alone is not a reason.
- Adopting a destructive recommendation requires `authorization: confirmed`.
- An adopted recommendation cannot have `authorization: missing`.
- The `next_action` and `stop_condition` remain the parent's responsibility.

Run `scripts/validate_decision.py` against the saved receipt and decision record. A failure blocks follow-through; do not summarize it away.

# Finish contract

## Terminal rules

1. Preserve the user's full requested endpoint.
2. Declare at most three gates: requested behavior, decisive evidence, and delivery.
3. Treat optional polish, refactors, additional experiments, and adjacent ideas as backlog.
4. Run a test again only after relevant state changes.
5. After the same state signature fails twice, change strategy or record a concrete blocker.
6. Predeclare experiment trials and stop thresholds. Do not extend them because results are disappointing.
7. Prefer the experiment that separates competing explanations over a larger sweep.
8. Ship the exact source state that passed. A plan, scaffold, local-only intermediate, or unreviewed draft is not terminal evidence unless that is what the user requested.
9. Run one final verification pass. If it passes, stop.

## Blocker contract

A blocker must name:

- the unmet gate;
- the observed evidence;
- the external state or authority that is missing;
- the smallest action that would unblock it.

Difficulty, uncertainty, an imperfect result, or a desirable extra improvement is not a blocker.

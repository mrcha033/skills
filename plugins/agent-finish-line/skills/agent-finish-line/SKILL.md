---
name: agent-finish-line
description: Drive an implementation, experiment, migration, release, or debugging task to a bounded terminal outcome. Use when agent work keeps expanding, repeating tests, adding optional improvements, or stopping at an intermediate artifact instead of shipping the requested deliverable or naming one concrete blocker.
---

# Agent Finish Line

Turn the user's objective into a small finish contract, execute only its required gates, and end with a shipped artifact or one evidence-backed blocker. Do not use this as a post-hoc quality audit.

## Start the finish contract

Create the contract before doing more implementation:

```bash
python3 "$SKILL_DIR/scripts/finish_contract.py" init \
  --contract .agent-finish.json \
  --objective "..." \
  --deliverable "..." \
  --gate "build:requested behavior exists" \
  --gate "test:decisive test passes" \
  --gate "ship:requested endpoint is delivered"
```

Use one to three gates. Put desirable but nonessential ideas in `--backlog`, not in a gate. If the user supplied a terminal condition, preserve it exactly.

## Execute

1. Read `references/finish-contract.md`.
2. Choose the earliest unmet gate.
3. Run the smallest decisive action that can pass or falsify it.
4. Record the result immediately:

   ```bash
   python3 "$SKILL_DIR/scripts/finish_contract.py" record \
     --contract .agent-finish.json \
     --gate build \
     --result pass \
     --evidence "path or concise observed result"
   ```

5. On failure, provide a state signature. Never repeat an identical failed state without a named strategy change:

   ```bash
   python3 "$SKILL_DIR/scripts/finish_contract.py" record \
     --contract .agent-finish.json \
     --gate test \
     --result fail \
     --state-signature "test-name:error-class:relevant-state" \
     --evidence "failure evidence"
   ```

6. Route every newly discovered improvement to the backlog unless it is necessary to pass a declared gate.
7. Do not add another verification layer after the final gate passes.

Inspect the next required action at any time:

```bash
python3 "$SKILL_DIR/scripts/finish_contract.py" status \
  --contract .agent-finish.json
```

## Finish

Complete only after all required gates pass:

```bash
python3 "$SKILL_DIR/scripts/finish_contract.py" complete \
  --contract .agent-finish.json \
  --terminal-evidence "commit, PR, output path, deployed endpoint, or experiment result"
```

If an external permission, credential, unavailable device, or user choice is the sole remaining obstacle, record that gate as `blocked` with the exact missing condition. Report the smallest action that unblocks it. Do not relabel hard or unfinished work as blocked.

End the response with:

- what finished;
- the terminal evidence;
- any deferred backlog, without starting it.

Run `python3 "$SKILL_DIR/scripts/self_test.py"` after modifying the contract script.
